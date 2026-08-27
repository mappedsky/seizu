from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from reporting import settings
from reporting.schema.chat import (
    CHAT_TURN_MAX_BATCH_BYTES,
    CHAT_TURN_MAX_SEQ,
    ChatSessionItem,
    ChatTurnAdmission,
    ChatTurnCommand,
    ChatTurnEventPage,
    ChatTurnItem,
    IdleChatSession,
    ScheduledChatItem,
    ScheduledChatVersion,
)
from reporting.schema.confirmations import (
    ActionConfirmation,
    ConfirmationDecision,
    ConfirmationSource,
)
from reporting.schema.mcp_config import (
    SkillItem,
    SkillsetListItem,
    SkillsetVersion,
    SkillVersion,
    ToolItem,
    ToolsetListItem,
    ToolsetVersion,
    ToolVersion,
)
from reporting.schema.model_profiles import ModelProfileItem, ModelProfileVersion
from reporting.schema.plugins import PluginFile, PluginFileInfo, PluginListItem, PluginSkillItem, PluginVersion
from reporting.schema.rbac import RoleItem, RoleVersion
from reporting.schema.report_config import (
    QueryHistoryItem,
    ReportAccess,
    ReportListItem,
    ReportVersion,
    ScheduledQueryItem,
    ScheduledQueryVersion,
    User,
)
from reporting.schema.space_config import (
    SPACE_MEMBER_MUST_BE_PUBLIC_DETAIL,
    SpaceConflictError,
    SpaceDeleteResult,
    SpaceListItem,
    SubspaceItem,
)


class PluginRevisionConflict(RuntimeError):
    """A staged package was derived from a revision that is no longer current."""


def require_public_space_member(access: ReportAccess, space_id: str | None) -> None:
    """Refuse to create a private report inside a space.

    The same invariant ``update_report_space`` enforces, applied to the one write
    that can set membership and visibility together. There is no race here -- a
    single caller supplies both -- but the store API is shared, and a caller that
    forgets to publish should get an error rather than a member nobody else can
    see and that quietly blocks the space's deletion.

    Backends call this rather than normalising to public: silently widening a
    caller's requested visibility is the wrong failure mode for a store.
    """
    if space_id is not None and access.scope != "public":
        raise SpaceConflictError(SPACE_MEMBER_MUST_BE_PUBLIC_DETAIL)


def initial_report_config(name: str) -> dict[str, Any]:
    """Return the minimal valid config stored with a newly created report."""
    return {"name": name, "rows": [], "schema_version": 1}


def validate_chat_turn_batch(parts_json: str) -> None:
    """Reject an oversized event-log batch before any I/O.

    Measure bytes rather than characters so multi-byte stream content observes
    the same storage and replay bound. The sequence number is not checked here
    any more: the store allocates it, so it cannot be out of range by the time
    anything is written.
    """
    size = len(parts_json.encode("utf-8"))
    if size > CHAT_TURN_MAX_BATCH_BYTES:
        raise ValueError(f"chat turn batch is {size} bytes, over the {CHAT_TURN_MAX_BATCH_BYTES} limit")


def validate_chat_turn_seq(seq: int) -> None:
    """Reject a sequence number the log cannot hold, after allocation."""
    if seq < 1 or seq > CHAT_TURN_MAX_SEQ:
        raise ValueError(f"chat turn sequence {seq} is outside 1..{CHAT_TURN_MAX_SEQ}")


def resolve_chat_turn_for_key(turn: "ChatTurnItem") -> ChatTurnAdmission:
    """Resolve a repeated key to its immutable admitted turn."""
    # This terminal state has no event log to replay: the turn was admitted but
    # its claim ran too low before a producer could safely be started. Preserve
    # that result across every repeat of the key instead of turning the second
    # attempt into an apparently successful ``existing`` empty response.
    if turn.status == "expired":
        return ChatTurnAdmission(outcome="expired")
    return ChatTurnAdmission(outcome="existing", turn=turn)


#: Time reserved at the end of a turn's claim for the cancellation to land.
#: Comfortably more than the activity's heartbeat interval, which is what bounds
#: how long a timed-out activity keeps running before it hears about it.
CHAT_TURN_CANCELLATION_BUFFER_SECONDS = 60


def chat_turn_lease_margin_seconds() -> int:
    """Derived safety room between the activity bound and thread takeover."""
    return max(CHAT_TURN_CANCELLATION_BUFFER_SECONDS * 2, settings.CHAT_TURN_TIMEOUT_SECONDS // 3)


def chat_turn_execution_bound_seconds(expires_at: str | None = None, now: datetime | None = None) -> int:
    """How long a turn's whole workflow may take, queue time included.

    The safety property is that a workflow never outlives its turn's claim on
    the thread: past the claim, a successor can be admitted, and two producers
    then write one conversation.

    A duration alone cannot express that. The claim is an *instant* fixed at
    admission, while a timeout starts whenever the workflow is finally created
    -- and a handoff repaired minutes later restarts it, so the workflow can run
    past a claim that has already lapsed. Given the turn's ``expires_at``, this
    returns whatever is left of it instead, which is the same instant no matter
    when the workflow starts.

    Without one it falls back to the derived duration, which is smaller than a
    fresh lease by construction: the lease adds the whole margin, this adds half.
    Zero or negative means the claim is already gone and there is nothing safe
    to start.
    """
    margin = chat_turn_lease_margin_seconds()
    bound = settings.CHAT_TURN_TIMEOUT_SECONDS + margin // 2
    if expires_at is None:
        return bound
    remaining = int((datetime.fromisoformat(expires_at) - (now or datetime.now(tz=UTC))).total_seconds())
    # Timing a workflow out does not stop its activity there and then: the
    # cancellation reaches it on its next heartbeat. Handing it the claim's full
    # remainder therefore still lets a producer run past the instant a successor
    # can be admitted, so the buffer comes off the top.
    return min(bound, remaining - CHAT_TURN_CANCELLATION_BUFFER_SECONDS)


def chat_turn_lease_expiry(now: datetime) -> str:
    """When a *running* turn's claim on its thread lapses.

    Derived from the turn's own timeout rather than from the retention window:
    the lease is what tells admission a producer still holds the thread, and
    retiring a turn that is merely slow puts a second producer on the same
    conversation. A finished turn is re-stamped with the (much shorter)
    retention window instead -- that one is a replay deadline, not a claim.
    """
    return (now + timedelta(seconds=settings.CHAT_TURN_TIMEOUT_SECONDS + chat_turn_lease_margin_seconds())).isoformat()


class ReportStore(ABC):
    """Application persistence contract and test seam."""

    @abstractmethod
    def generate_id(self) -> str:
        """Return a new Snowflake ID from the backend's process-wide generator."""

    @abstractmethod
    async def initialize(self) -> None:
        """Upgrade the PostgreSQL application schema."""

    @abstractmethod
    async def list_reports(self, user_id: str | None = None) -> list[ReportListItem]:
        """Return lightweight metadata for reports visible to the user."""

    @abstractmethod
    async def get_report_metadata(
        self,
        report_id: str,
        user_id: str | None = None,
    ) -> ReportListItem | None:
        """Return report metadata if it exists and is visible to the user."""

    @abstractmethod
    async def get_report_latest(
        self,
        report_id: str,
        user_id: str | None = None,
    ) -> ReportVersion | None:
        """Return the latest version of a report config, or None if not found."""

    @abstractmethod
    async def get_report_version(
        self,
        report_id: str,
        version: int,
        user_id: str | None = None,
    ) -> ReportVersion | None:
        """Return a specific version of a report config, or None if not found."""

    @abstractmethod
    async def list_report_versions(
        self,
        report_id: str,
        user_id: str | None = None,
    ) -> list[ReportVersion]:
        """Return all stored versions for a report, newest first."""

    @abstractmethod
    async def create_report(
        self,
        name: str,
        created_by: str,
        access: ReportAccess | None = None,
        space_id: str | None = None,
        subspace_id: str | None = None,
    ) -> ReportListItem:
        """Create a report with an initial version and return its metadata.

        The space arguments are not validated here — callers go through
        ``reporting.services.spaces.resolve_report_space`` first.
        """

    @abstractmethod
    async def save_report_version(
        self,
        report_id: str,
        config: dict[str, Any],
        created_by: str,
        comment: str | None = None,
        user_id: str | None = None,
    ) -> ReportVersion | None:
        """Append a new version to an existing report and return it.

        Returns None if the report does not exist.
        """

    @abstractmethod
    async def update_report_visibility(
        self,
        report_id: str,
        updated_by: str,
        access: ReportAccess | None = None,
    ) -> ReportListItem | None:
        """Update report visibility without creating a new report version.

        Implementations MUST refuse to make a report private while it is filed in
        a space, raising ``SpaceConflictError`` — the other half of the rule in
        ``update_report_space``, and atomic for the same reason.
        """

    @abstractmethod
    async def update_report_space(
        self,
        report_id: str,
        space_id: str | None,
        subspace_id: str | None,
        updated_by: str,
        user_id: str | None = None,
    ) -> ReportListItem | None:
        """Set a report's space membership without creating a new report version.

        Replace semantics: both arguments describe the desired final state, so
        moving a report to a different space without naming a sub-space clears
        the sub-space. Cross-entity validity (the sub-space exists and belongs
        to the space) is the caller's responsibility — see
        ``reporting.services.spaces``.

        Implementations MUST refuse to file a non-public report, raising
        ``SpaceConflictError``: the caller checks first for a better error
        message, but only an atomic check here survives a concurrent unpublish.

        Returns None if the report does not exist or is not visible to the user.
        """

    @abstractmethod
    async def delete_report(self, report_id: str, user_id: str | None = None) -> bool:
        """Delete a report and all its versions.

        Also clears the dashboard pointer if it points to this report.
        Returns False if the report does not exist.
        """

    @abstractmethod
    async def pin_report(
        self,
        report_id: str,
        pinned: bool,
        updated_by: str,
        user_id: str | None = None,
    ) -> bool:
        """Set or clear the pinned flag on a report.

        Returns False if the report does not exist.
        """

    @abstractmethod
    async def get_dashboard_report_id(self) -> str | None:
        """Return the report_id of the current dashboard report, or None if not set."""

    @abstractmethod
    async def set_dashboard_report(self, report_id: str) -> bool:
        """Point the dashboard pointer at the given report.

        Returns False if the report does not exist.
        """

    @abstractmethod
    async def get_dashboard_report(self) -> ReportVersion | None:
        """Return the latest version of the dashboard report, or None if not set."""

    @abstractmethod
    async def get_or_create_user(
        self,
        sub: str,
        iss: str,
        email: str | None = None,
        display_name: str | None = None,
        preferred_username: str | None = None,
        role: str | None = None,
    ) -> User:
        """Get an existing user by (iss, sub), or create one on first login.

        Existing users are returned as-is, with one security-relevant
        exception: ``role`` is the role claim observed on this request and is
        synced (written only on drift, including clearing to None when the
        claim is absent) so headless callers can later resolve the user's
        permissions without a token. Other profile updates (email drift,
        last_login) are done separately via ``update_user_profile``, called
        only from the ``/api/v1/me`` route.
        Returns the User model.
        """

    @abstractmethod
    async def update_user_profile(
        self,
        user_id: str,
        email: str | None = None,
        display_name: str | None = None,
        preferred_username: str | None = None,
        token_iat: datetime | None = None,
    ) -> User:
        """Sync mutable profile fields, writing only what has changed.

        - ``email`` is written only when provided and differs from the stored value.
        - ``display_name`` is written only when provided and differs from stored.
        - ``preferred_username`` is written only when provided and differs from stored.
        - ``last_login`` is written only when ``token_iat`` is provided and
          newer than the stored value (i.e. a new credential was issued).

        Returns the updated User.
        """

    @abstractmethod
    async def get_user(self, user_id: str) -> User | None:
        """Return a user by their internal user_id, or None if not found."""

    @abstractmethod
    async def archive_user(self, user_id: str) -> bool:
        """Soft-delete a user by setting archived_at.

        Returns False if the user does not exist.
        """

    @abstractmethod
    async def list_scheduled_queries(self) -> list[ScheduledQueryItem]:
        """Return all scheduled queries."""

    @abstractmethod
    async def get_scheduled_query(self, sq_id: str) -> ScheduledQueryItem | None:
        """Return a scheduled query by ID, or None if not found."""

    @abstractmethod
    async def create_scheduled_query(
        self,
        name: str,
        cypher: str,
        params: list[dict[str, Any]],
        frequency: int | None,
        schedule: dict[str, Any] | None,
        watch_scans: list[dict[str, Any]],
        enabled: bool,
        actions: list[dict[str, Any]],
        created_by: str,
        stages: list[dict[str, Any]] | None = None,
        inputs: dict[str, Any] | None = None,
        activities: list[dict[str, Any]] | None = None,
    ) -> ScheduledQueryItem:
        """Create a new scheduled query (at version 1) and return it."""

    @abstractmethod
    async def update_scheduled_query(
        self,
        sq_id: str,
        name: str,
        cypher: str,
        params: list[dict[str, Any]],
        frequency: int | None,
        schedule: dict[str, Any] | None,
        watch_scans: list[dict[str, Any]],
        enabled: bool,
        actions: list[dict[str, Any]],
        updated_by: str,
        comment: str | None = None,
        stages: list[dict[str, Any]] | None = None,
        inputs: dict[str, Any] | None = None,
        activities: list[dict[str, Any]] | None = None,
    ) -> ScheduledQueryItem | None:
        """Save a new version of an existing scheduled query. Returns None if not found."""

    @abstractmethod
    async def list_scheduled_query_versions(self, sq_id: str) -> list[ScheduledQueryVersion]:
        """Return all stored versions for a scheduled query, newest first."""

    @abstractmethod
    async def get_scheduled_query_version(self, sq_id: str, version: int) -> ScheduledQueryVersion | None:
        """Return a specific version of a scheduled query, or None if not found."""

    @abstractmethod
    async def acquire_scheduled_query_lock(self, sq_id: str, expected_last_scheduled_at: str | None) -> bool:
        """Atomically set last_scheduled_at = now if it still equals expected.

        Returns True if the lock was acquired (CAS succeeded), False if another
        worker already updated the value (CAS failed).
        """

    @abstractmethod
    async def record_scheduled_query_result(self, sq_id: str, status: str, error: str | None = None) -> None:
        """Record the result of a scheduled query execution.

        Updates ``last_run_status`` and ``last_run_at`` on the item.  When
        *status* is ``"failure"`` and *error* is provided, the error is
        prepended to ``last_errors`` (capped at 5 entries).
        """

    @abstractmethod
    async def request_scheduled_query_run(self, sq_id: str) -> str | None:
        """Request a manual "run now" by setting run_requested_at to now.

        The worker picks the request up on its next poll. Returns the
        timestamp set, or None if the scheduled query does not exist.
        """

    @abstractmethod
    async def set_workflow_schedule_sync_status(
        self,
        workflow_id: str,
        status: str,
        *,
        error: str | None = None,
        synced_at: str | None = None,
    ) -> None:
        """Persist Temporal Schedule reconciliation status."""

    @abstractmethod
    async def set_chat_schedule_sync_status(
        self,
        sc_id: str,
        status: str,
        *,
        error: str | None = None,
        synced_at: str | None = None,
    ) -> None:
        """Persist a scheduled chat's Temporal Schedule reconciliation status."""

    @abstractmethod
    async def delete_scheduled_query(self, sq_id: str) -> bool:
        """Delete a scheduled query and all its versions. Returns False if not found."""

    # ------------------------------------------------------------------
    # Spaces
    #
    # Flat records: no version history and no access scope. Spaces are
    # globally visible; the reports listed inside one are still filtered by
    # the report's own visibility.
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_spaces(self) -> list[SpaceListItem]:
        """Return all spaces."""

    @abstractmethod
    async def get_space(self, space_id: str) -> SpaceListItem | None:
        """Return a space, or None if it does not exist."""

    @abstractmethod
    async def create_space(
        self,
        name: str,
        description: str,
        created_by: str,
    ) -> SpaceListItem:
        """Create an empty space.

        No overview report is created: the overview is a pointer the user sets
        at any of the space's member reports, so a new space starts with none.
        """

    @abstractmethod
    async def update_space(
        self,
        space_id: str,
        name: str,
        description: str,
        updated_by: str,
    ) -> SpaceListItem | None:
        """Rename a space. Returns None if it does not exist."""

    @abstractmethod
    async def delete_space(self, space_id: str) -> SpaceDeleteResult:
        """Delete a space that holds no reports, along with its sub-spaces.

        Sub-spaces do not block the delete: they are grouping labels, and with
        no member reports left nothing references them, so they are removed
        with the space. No report is ever deleted here — the overview is only a
        pointer.

        Emptiness is evaluated **without** per-user visibility filtering —
        filtering it would let a space holding another user's private report
        read as empty and be deleted, orphaning that report.
        """

    @abstractmethod
    async def set_space_overview(
        self,
        space_id: str,
        report_id: str | None,
        updated_by: str,
    ) -> SpaceListItem | None:
        """Point the space at one of its reports as the landing page.

        ``report_id=None`` clears it. Membership is the caller's
        responsibility — see ``reporting.services.spaces``. Returns None if the
        space does not exist.
        """

    @abstractmethod
    async def list_space_reports(
        self,
        space_id: str,
        user_id: str | None = None,
    ) -> list[ReportListItem]:
        """Return the reports filed in a space that are visible to the user.

        Includes the overview report; callers rendering the space sidebar
        filter it out. Pass ``user_id=None`` for an unfiltered view.
        """

    # ------------------------------------------------------------------
    # Sub-spaces (nested under spaces)
    #
    # Grouping labels only: no detail page, no version history. Deleting one
    # leaves member reports with an unresolvable subspace_id, which reads as
    # "ungrouped" — see reporting/routes/spaces.py.
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_subspaces(self, space_id: str) -> list[SubspaceItem]:
        """Return the sub-spaces of a space."""

    @abstractmethod
    async def get_subspace(self, subspace_id: str) -> SubspaceItem | None:
        """Return a sub-space, or None if it does not exist."""

    @abstractmethod
    async def create_subspace(
        self,
        space_id: str,
        name: str,
        created_by: str,
    ) -> SubspaceItem | None:
        """Create a sub-space. Returns None if the space does not exist."""

    @abstractmethod
    async def update_subspace(
        self,
        subspace_id: str,
        name: str,
        updated_by: str,
    ) -> SubspaceItem | None:
        """Rename a sub-space. Returns None if it does not exist."""

    @abstractmethod
    async def delete_subspace(self, subspace_id: str) -> bool:
        """Delete a sub-space. Returns False if it does not exist.

        Member reports are left untouched; their now-dangling ``subspace_id``
        renders as ungrouped rather than triggering an unbounded,
        non-transactional fan-out write.
        """

    # ------------------------------------------------------------------
    # Toolsets
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_toolsets(self) -> list[ToolsetListItem]:
        """Return all toolsets."""

    @abstractmethod
    async def get_toolset(self, toolset_id: str) -> ToolsetListItem | None:
        """Return a toolset by ID, or None if not found."""

    @abstractmethod
    async def create_toolset(
        self,
        toolset_id: str,
        name: str,
        description: str,
        enabled: bool,
        created_by: str,
    ) -> ToolsetListItem:
        """Create a new toolset (at version 1) and return it."""

    @abstractmethod
    async def update_toolset(
        self,
        toolset_id: str,
        name: str,
        description: str,
        enabled: bool,
        updated_by: str,
        comment: str | None = None,
    ) -> ToolsetListItem | None:
        """Save a new version of an existing toolset. Returns None if not found."""

    @abstractmethod
    async def delete_toolset(self, toolset_id: str) -> bool:
        """Delete a toolset, all its versions, and all its tools. Returns False if not found."""

    @abstractmethod
    async def list_toolset_versions(self, toolset_id: str) -> list[ToolsetVersion]:
        """Return all stored versions for a toolset, newest first."""

    @abstractmethod
    async def get_toolset_version(self, toolset_id: str, version: int) -> ToolsetVersion | None:
        """Return a specific version of a toolset, or None if not found."""

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_tools(self, toolset_id: str) -> list[ToolItem]:
        """Return all tools within a toolset."""

    @abstractmethod
    async def get_tool(self, tool_id: str) -> ToolItem | None:
        """Return a tool by ID, or None if not found."""

    @abstractmethod
    async def create_tool(
        self,
        toolset_id: str,
        tool_id: str,
        name: str,
        description: str,
        cypher: str,
        parameters: list[dict[str, Any]],
        enabled: bool,
        created_by: str,
    ) -> ToolItem | None:
        """Create a new tool (at version 1). Returns None if the toolset does not exist."""

    @abstractmethod
    async def update_tool(
        self,
        tool_id: str,
        name: str,
        description: str,
        cypher: str,
        parameters: list[dict[str, Any]],
        enabled: bool,
        updated_by: str,
        comment: str | None = None,
    ) -> ToolItem | None:
        """Save a new version of an existing tool. Returns None if not found."""

    @abstractmethod
    async def delete_tool(self, tool_id: str) -> bool:
        """Delete a tool and all its versions. Returns False if not found."""

    @abstractmethod
    async def list_tool_versions(self, tool_id: str) -> list[ToolVersion]:
        """Return all stored versions for a tool, newest first."""

    @abstractmethod
    async def get_tool_version(self, tool_id: str, version: int) -> ToolVersion | None:
        """Return a specific version of a tool, or None if not found."""

    @abstractmethod
    async def list_enabled_tools(self) -> list[ToolItem]:
        """Return all enabled tools in all enabled toolsets."""

    @abstractmethod
    async def get_enabled_tool(self, toolset_id: str, tool_id: str) -> ToolItem | None:
        """Return an enabled tool in an enabled toolset, or None if not found."""

    # ------------------------------------------------------------------
    # Skillsets
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_skillsets(self) -> list[SkillsetListItem]:
        """Return all skillsets."""

    @abstractmethod
    async def get_skillset(self, skillset_id: str) -> SkillsetListItem | None:
        """Return a skillset by ID, or None if not found."""

    @abstractmethod
    async def create_skillset(
        self,
        skillset_id: str,
        name: str,
        description: str,
        enabled: bool,
        created_by: str,
    ) -> SkillsetListItem:
        """Create a new skillset (at version 1) and return it."""

    @abstractmethod
    async def update_skillset(
        self,
        skillset_id: str,
        name: str,
        description: str,
        enabled: bool,
        updated_by: str,
        comment: str | None = None,
    ) -> SkillsetListItem | None:
        """Save a new version of an existing skillset. Returns None if not found."""

    @abstractmethod
    async def delete_skillset(self, skillset_id: str) -> bool:
        """Delete a skillset, all its versions, and all its skills. Returns False if not found."""

    @abstractmethod
    async def list_skillset_versions(self, skillset_id: str) -> list[SkillsetVersion]:
        """Return all stored versions for a skillset, newest first."""

    @abstractmethod
    async def get_skillset_version(self, skillset_id: str, version: int) -> SkillsetVersion | None:
        """Return a specific version of a skillset, or None if not found."""

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_skills(self, skillset_id: str) -> list[SkillItem]:
        """Return all skills within a skillset."""

    @abstractmethod
    async def get_skill(self, skill_id: str) -> SkillItem | None:
        """Return a skill by ID, or None if not found."""

    @abstractmethod
    async def create_skill(
        self,
        skillset_id: str,
        skill_id: str,
        name: str,
        description: str,
        template: str,
        parameters: list[dict[str, Any]],
        triggers: list[str],
        tools_required: list[str],
        enabled: bool,
        created_by: str,
    ) -> SkillItem | None:
        """Create a new skill (at version 1). Returns None if the skillset does not exist."""

    @abstractmethod
    async def update_skill(
        self,
        skill_id: str,
        name: str,
        description: str,
        template: str,
        parameters: list[dict[str, Any]],
        triggers: list[str],
        tools_required: list[str],
        enabled: bool,
        updated_by: str,
        comment: str | None = None,
    ) -> SkillItem | None:
        """Save a new version of an existing skill. Returns None if not found."""

    @abstractmethod
    async def delete_skill(self, skill_id: str) -> bool:
        """Delete a skill and all its versions. Returns False if not found."""

    @abstractmethod
    async def list_skill_versions(self, skill_id: str) -> list[SkillVersion]:
        """Return all stored versions for a skill, newest first."""

    @abstractmethod
    async def get_skill_version(self, skill_id: str, version: int) -> SkillVersion | None:
        """Return a specific version of a skill, or None if not found."""

    @abstractmethod
    async def list_enabled_skills(self) -> list[SkillItem]:
        """Return all enabled skills in all enabled skillsets."""

    @abstractmethod
    async def get_enabled_skill(self, skillset_id: str, skill_id: str) -> SkillItem | None:
        """Return an enabled skill in an enabled skillset, or None if not found."""

    # ------------------------------------------------------------------
    # Agent plugins
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_plugins(self) -> list[PluginListItem]:
        """Return installed Agent Plugins."""

    @abstractmethod
    async def get_plugin(self, plugin_id: str) -> PluginListItem | None:
        """Return an installed Agent Plugin."""

    @abstractmethod
    async def publish_plugin(
        self,
        plugin_id: str,
        manifest: dict[str, Any],
        files: list[PluginFile],
        skills: list[PluginSkillItem],
        diagnostics: list[dict[str, Any]],
        package_digest: str,
        created_by: str,
        comment: str | None = None,
        expected_revision: int | None = None,
    ) -> PluginListItem:
        """Atomically publish a complete immutable plugin package revision."""

    @abstractmethod
    async def set_plugin_enabled(self, plugin_id: str, enabled: bool, updated_by: str) -> PluginListItem | None:
        """Set plugin activation without changing package contents."""

    @abstractmethod
    async def delete_plugin(self, plugin_id: str) -> bool:
        """Delete an installed plugin, its revisions, files, and indexes."""

    @abstractmethod
    async def list_plugin_versions(self, plugin_id: str) -> list[PluginVersion]:
        """Return immutable plugin revisions newest first."""

    @abstractmethod
    async def list_plugin_files(self, plugin_id: str, revision: int | None = None) -> list[PluginFileInfo]:
        """Return a plugin revision's file manifest."""

    @abstractmethod
    async def read_plugin_file(self, plugin_id: str, path: str, revision: int | None = None) -> PluginFile | None:
        """Read one file from a published plugin revision."""

    @abstractmethod
    async def read_plugin_files(
        self, plugin_id: str, revision: int | None = None, paths: list[str] | None = None
    ) -> list[PluginFile]:
        """Read a revision's files in one statement, optionally restricted to ``paths``."""

    @abstractmethod
    async def list_enabled_plugin_skills(self) -> list[PluginSkillItem]:
        """Return enabled skills indexed from enabled plugins."""

    @abstractmethod
    async def list_plugin_skills(self, plugin_id: str) -> list[PluginSkillItem]:
        """Return all indexed skills for one plugin, including disabled skills."""

    @abstractmethod
    async def get_plugin_skill(self, plugin_id: str, skill_id: str) -> PluginSkillItem | None:
        """Return one indexed plugin skill regardless of activation state."""

    @abstractmethod
    async def get_enabled_plugin_skill(self, plugin_id: str, skill_id: str) -> PluginSkillItem | None:
        """Return an enabled plugin skill by its namespaced identity."""

    @abstractmethod
    async def set_plugin_skill_enabled(self, plugin_id: str, skill_id: str, enabled: bool) -> PluginSkillItem | None:
        """Turn one indexed skill on or off without republishing the package."""

    @abstractmethod
    async def read_plugin_blob(self, plugin_id: str, sha256: str) -> PluginFile | None:
        """Read a blob already stored by one of this plugin's revisions, by digest."""

    # ------------------------------------------------------------------
    # Query history
    # ------------------------------------------------------------------

    @abstractmethod
    async def save_query_history(self, user_id: str, query: str) -> QueryHistoryItem:
        """Append a query execution to the user's history and return the new item."""

    @abstractmethod
    async def list_query_history(self, user_id: str, page: int, per_page: int) -> tuple[list[QueryHistoryItem], int]:
        """Return a paginated page of query history (newest first) and the total count.

        Only items belonging to ``user_id`` are returned — callers must never
        pass a user_id they do not own.
        """

    @abstractmethod
    async def get_query_history_item(self, user_id: str, history_id: str) -> QueryHistoryItem | None:
        """Return a single history item by ID, scoped to user_id, or None if not found."""

    # ------------------------------------------------------------------
    # Roles (user-defined, versioned)
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_roles(self) -> list[RoleItem]:
        """Return all user-defined roles."""

    @abstractmethod
    async def get_role(self, role_id: str) -> RoleItem | None:
        """Return a user-defined role by ID, or None if not found."""

    @abstractmethod
    async def get_role_by_name(self, name: str) -> RoleItem | None:
        """Return a user-defined role by name, or None if not found."""

    @abstractmethod
    async def create_role(
        self,
        name: str,
        description: str,
        permissions: list[str],
        created_by: str,
    ) -> RoleItem:
        """Create a new user-defined role (at version 1) and return it."""

    @abstractmethod
    async def update_role(
        self,
        role_id: str,
        name: str,
        description: str,
        permissions: list[str],
        updated_by: str,
        comment: str | None = None,
    ) -> RoleItem | None:
        """Save a new version of an existing role. Returns None if not found."""

    @abstractmethod
    async def delete_role(self, role_id: str) -> bool:
        """Delete a role and all its versions. Returns False if not found."""

    @abstractmethod
    async def list_role_versions(self, role_id: str) -> list[RoleVersion]:
        """Return all stored versions for a role, newest first."""

    @abstractmethod
    async def get_role_version(self, role_id: str, version: int) -> RoleVersion | None:
        """Return a specific version of a role, or None if not found."""

    # ------------------------------------------------------------------
    # Chat sessions
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_chat_sessions(self, user_id: str, limit: int) -> list[ChatSessionItem]:
        """Return recent interactive chat sessions for a user, newest first.

        Headless sessions (``origin="scheduled"`` or ``origin="workflow"``)
        are excluded.
        """

    @abstractmethod
    async def get_chat_session(self, user_id: str, thread_id: str) -> ChatSessionItem | None:
        """Return a chat session for a user, or None if it does not exist."""

    @abstractmethod
    async def list_idle_chat_sessions(self, idle_before: str, limit: int) -> list[IdleChatSession]:
        """Interactive sessions last updated before ``idle_before``, oldest first.

        The one session read that spans users, for the reaper
        (:mod:`reporting.services.session_reaper`). Headless sessions are
        excluded: they belong to a schedule's run history, are already bounded
        by it, and never leave a suspended sandbox behind
        (``sandbox_persistence_allowed`` refuses to persist one for a headless
        turn).
        """

    @abstractmethod
    async def create_chat_session(
        self,
        user_id: str,
        title: str,
        origin: str = "interactive",
        scheduled_chat_id: str | None = None,
        model_profile_id: str | None = None,
        model_reasoning_effort: str | None = None,
    ) -> ChatSessionItem:
        """Create a new chat session with a store-generated ID.

        Headless sessions are hidden from ``list_chat_sessions`` and are
        read-only in the web UI; ``scheduled_chat_id`` links scheduled
        sessions to the schedule that created them.
        """

    @abstractmethod
    async def list_scheduled_chat_sessions(
        self,
        user_id: str,
        scheduled_chat_id: str,
        limit: int,
    ) -> list[ChatSessionItem]:
        """Return a schedule's run sessions, newest first."""

    @abstractmethod
    async def touch_chat_session(self, user_id: str, thread_id: str) -> ChatSessionItem | None:
        """Update a session's updated_at.

        Returns None when the session is not found **or has been claimed for
        retirement** — a claimed session is about to lose its checkpoint and
        sandbox, so a turn must not start against it. Callers that can tell the
        two apart should treat both as "this conversation is gone".
        """

    @abstractmethod
    async def claim_chat_session_for_retirement(
        self,
        user_id: str,
        thread_id: str,
        expected_updated_at: str,
    ) -> bool:
        """Mark a session as being retired, if it has not been touched since.

        The reaper's atomic pivot (SBX-011). Fails — returning False — when the
        session has been used since ``expected_updated_at`` was observed, or has
        already been deleted, so a conversation the owner came back to is never
        destroyed by a sweep that read it a moment earlier.

        A claim is deliberately **re-claimable**: it is conditioned only on
        ``updated_at``, so a sweep that died between claiming and finishing can
        be resumed by the next one. It is the caller's job to delete the record
        *last*, after the checkpoint and sandbox are gone, so nothing becomes
        unfindable before its dependents are.
        """

    @abstractmethod
    async def complete_chat_session_run(
        self,
        user_id: str,
        thread_id: str,
        status: str,
        errors: list[str],
    ) -> ChatSessionItem | None:
        """Record terminal status and errors for a scheduled chat run session."""

    @abstractmethod
    async def update_chat_session_title(self, user_id: str, thread_id: str, title: str) -> ChatSessionItem | None:
        """Update a session's title and updated_at. Returns None if the session is not found."""

    @abstractmethod
    async def update_chat_session_model_profile(
        self,
        user_id: str,
        thread_id: str,
        model_profile_id: str | None,
        model_reasoning_effort: str | None = None,
    ) -> ChatSessionItem | None:
        """Remember a profile and effort, refusing a family change after admission."""

    @abstractmethod
    async def delete_chat_session(self, user_id: str, thread_id: str) -> bool:
        """Delete a session. Returns False if not found.

        Deletes the thread's chat turn event logs with it: a turn log is
        meaningless once its conversation is gone, and nothing else would ever
        find it.
        """

    # ------------------------------------------------------------------
    # Chat turn event log
    # ------------------------------------------------------------------

    @abstractmethod
    async def admit_chat_turn(
        self,
        user_id: str,
        thread_id: str,
        message_id: str,
        text_id: str,
        idempotency_key: str,
        command: ChatTurnCommand,
    ) -> ChatTurnAdmission:
        """Reserve a thread for a turn, and say what happened.

        ``command`` is captured in the same commit. A repeat always dispatches
        that immutable work and permission cap, never the retrying request.

        Returns an outcome rather than raising one of several errors the caller
        must interpret: ``created``, ``existing``, ``busy`` or ``retired``. The
        store knows which of those it did, so it reports it -- earlier versions
        inferred it afterwards from whichever constraint rejected the write,
        and a single collision could legitimately mean any of them.

        **Admission is a request of its own, answered before anything streams.**
        The turn exists, with an id, before the client can need one. That is
        what removes an entire class of problem: there is never a command
        against a turn that does not exist yet, so nothing has to be parked,
        replayed against a placeholder, or addressed by a second identity.

        ``idempotency_key`` makes a repeat resolve to the turn it already
        admitted (``existing``). A lost response is therefore fixed by asking
        again. A repeat is never read as a cancellation.

        The session's ``updated_at`` moves in the same commit, conditioned on
        the session not being claimed for retirement -- the turn's half of the
        handshake in SBX-011. Two writes would leave a window where a delete
        reads the fresh timestamp, claims the session, sees no running turn and
        cascades, and the turn is then admitted to a conversation that is gone.

        A thread holds at most one running, unexpired turn. The exclusion has to
        expire, or a producer that died without finishing wedges the
        conversation forever.
        """

    @abstractmethod
    async def get_active_chat_turn(self, user_id: str, thread_id: str) -> ChatTurnItem | None:
        """Return the thread's running, unexpired turn, or None.

        The reconnect endpoint's entry point. An expired running turn reads as
        None: its producer is gone and nothing will ever finish it.
        """

    @abstractmethod
    async def get_chat_turn(self, turn_id: str, user_id: str | None = None) -> ChatTurnItem | None:
        """Return a turn by id, optionally scoped to its owner."""

    @abstractmethod
    async def append_chat_turn_events(self, turn_id: str, parts_json: str) -> int | None:
        """Append one already-rendered batch of UI-stream parts; return its ``seq``.

        ``parts_json`` is stored verbatim -- it is the exact JSON array text the
        live stream sent -- so a replay is byte-identical rather than
        re-serialized from a decoded copy.

        **The store allocates ``seq``, not the caller.** A turn has more than one
        producer once its plan steps run as separate activities (AGT-018), and a
        counter held by any of them cannot stay dense: two writers picking their
        own next number either collide or leave a gap, and
        :meth:`read_chat_turn_events` truncates at the first gap, so a gap is a
        reader that stops mid-answer. Allocating under the turn row's lock makes
        the numbering total across every writer, and makes "row N exists" mean
        rows 1..N-1 were committed before it.

        Returns ``None`` when the turn is gone -- there is nothing left to append
        to, and a caller that kept writing would leave headerless rows behind.
        Raises ValueError when the batch exceeds ``CHAT_TURN_MAX_BATCH_BYTES``;
        splitting is the producer's job.
        """

    @abstractmethod
    async def put_chat_turn_payload(self, turn_id: str, payload_id: str, body: str) -> None:
        """Store one oversized turn payload out of band, keyed within the turn.

        A distributed plan step's result carries every tool call it made and what
        each returned, which is bounded by ``CHAT_TOOL_RESULT_MAX_BYTES`` *per
        call* and so can run to megabytes for a step that made dozens. Returning
        that through Temporal would copy it into workflow history on the way out
        and again on the way in; the reference goes through history instead and
        the body stays here, collected with the turn (AGT-018).
        """

    @abstractmethod
    async def get_chat_turn_payload(self, turn_id: str, payload_id: str) -> str | None:
        """Read back a payload stored by :meth:`put_chat_turn_payload`."""

    @abstractmethod
    async def read_chat_turn_events(self, turn_id: str, after_seq: int, limit: int) -> ChatTurnEventPage | None:
        """Return the turn plus up to ``limit`` batches with seq > after_seq, in order.

        The page is **truncated at the first gap** in ``seq``. A store can make
        a later batch visible before an earlier one; a reader that accepted the
        gap would advance its cursor past the missing batch and lose it
        permanently, which is a hole in the replay rather than a delay. Callers
        advance from the last batch actually returned, never from
        ``after_seq + len(batches)``.
        """

    @abstractmethod
    async def request_chat_turn_cancel(self, turn_id: str, user_id: str) -> ChatTurnItem | None:
        """Ask a running turn to stop, returning it, or None.

        **Only ever marks a turn that exists.** It creates nothing: a stop for a
        turn that has not been admitted is not this method's problem, because a
        client cannot name a turn before admission gives it one.

        A request, not a signal -- the turn runs elsewhere, so it is told
        through the record. Scoped to the owner, so a guessed id stops nothing.
        """

    @abstractmethod
    async def finish_chat_turn(
        self,
        turn_id: str,
        status: Literal["completed", "failed", "canceled", "expired"],
        last_seq: int,
    ) -> ChatTurnItem | None:
        """Move a *running* turn to a terminal status, first writer wins.

        ``last_seq`` is what lets a reader tell "finished" from "finished, and
        you have seen all of it": a terminal status alone races the visibility
        of the final batches. Also sets ``expires_at`` to the retention horizon.

        **Conditional on the turn still being ``running``.** Two writers can
        reach here for one turn -- the turn closing itself, and the workflow's
        fallback closing it after the activity timed out -- and a turn that
        timed out spuriously is still alive, so the later write would replace a
        recorded outcome (and its ``last_seq``) with a stale one.

        Returns what is *recorded*, which is not necessarily what was asked for:
        a caller that lost sees the winning status rather than its own, and
        ``None`` only when the turn is gone.
        """

    @abstractmethod
    async def delete_chat_turn(self, turn_id: str) -> bool:
        """Delete a turn, every one of its batches, and its spilled payloads.

        Returns False if not found.
        """

    @abstractmethod
    async def list_expired_chat_turns(self, expired_before: str, limit: int) -> list[str]:
        """IDs of turns whose ``expires_at`` has passed, oldest first."""

    # ------------------------------------------------------------------
    # Model profiles
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_model_profiles(self, *, enabled_only: bool = False) -> list[ModelProfileItem]:
        """Return model profiles, optionally limited to enabled choices."""

    @abstractmethod
    async def get_model_profile(self, profile_id: str) -> ModelProfileItem | None:
        """Return a model profile by id."""

    @abstractmethod
    async def create_model_profile(self, data: dict[str, Any], created_by: str) -> ModelProfileItem:
        """Create a model profile and its first immutable version."""

    @abstractmethod
    async def update_model_profile(
        self, profile_id: str, data: dict[str, Any], updated_by: str, comment: str | None = None
    ) -> ModelProfileItem | None:
        """Replace a model profile and append an immutable version."""

    @abstractmethod
    async def delete_model_profile(self, profile_id: str) -> bool:
        """Delete a profile and its versions."""

    @abstractmethod
    async def list_model_profile_versions(self, profile_id: str) -> list[ModelProfileVersion]:
        """Return a profile's versions newest first."""

    @abstractmethod
    async def get_model_profile_version(self, profile_id: str, version: int) -> ModelProfileVersion | None:
        """Return one model profile version."""

    # ------------------------------------------------------------------
    # Scheduled chats
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_scheduled_chats(self, user_id: str | None = None) -> list[ScheduledChatItem]:
        """Return scheduled chats, optionally filtered to one owner.

        The worker lists all schedules (user_id=None); the API lists only the
        requesting user's own schedules.
        """

    @abstractmethod
    async def get_scheduled_chat(self, sc_id: str) -> ScheduledChatItem | None:
        """Return a scheduled chat, or None if it does not exist."""

    @abstractmethod
    async def create_scheduled_chat(
        self,
        name: str,
        prompt: str,
        schedule: dict[str, Any] | None,
        watch_scans: list[dict[str, Any]],
        enabled: bool,
        created_by: str,
        model_profile_id: str | None = None,
    ) -> ScheduledChatItem:
        """Create a scheduled chat owned by created_by."""

    @abstractmethod
    async def update_scheduled_chat(
        self,
        sc_id: str,
        name: str,
        prompt: str,
        schedule: dict[str, Any] | None,
        watch_scans: list[dict[str, Any]],
        enabled: bool,
        updated_by: str,
        comment: str | None = None,
        model_profile_id: str | None = None,
    ) -> ScheduledChatItem | None:
        """Replace a scheduled chat's configuration, appending a new version.

        Returns None if not found.
        """

    @abstractmethod
    async def list_scheduled_chat_versions(self, sc_id: str) -> list[ScheduledChatVersion]:
        """Return all stored versions for a scheduled chat, newest first."""

    @abstractmethod
    async def get_scheduled_chat_version(self, sc_id: str, version: int) -> ScheduledChatVersion | None:
        """Return a specific version of a scheduled chat, or None if not found."""

    @abstractmethod
    async def delete_scheduled_chat(self, sc_id: str) -> bool:
        """Delete a scheduled chat and all its versions. Returns False if not found."""

    @abstractmethod
    async def acquire_scheduled_chat_lock(self, sc_id: str, expected_last_scheduled_at: str | None) -> bool:
        """Atomically claim a run by compare-and-setting last_scheduled_at.

        Returns False when another worker already claimed this run.
        """

    @abstractmethod
    async def record_scheduled_chat_result(self, sc_id: str, status: str, error: str | None = None) -> None:
        """Record a run outcome (last_run_status/last_run_at/last_errors)."""

    @abstractmethod
    async def request_scheduled_chat_run(self, sc_id: str) -> str | None:
        """Request a manual "run now" by setting run_requested_at to now.

        The worker picks the request up on its next poll. Returns the
        timestamp set, or None if the scheduled chat does not exist.
        """

    # ------------------------------------------------------------------
    # Action confirmations
    # ------------------------------------------------------------------

    @abstractmethod
    async def create_action_confirmation(self, confirmation: ActionConfirmation) -> ActionConfirmation:
        """Persist a pending action confirmation."""

    @abstractmethod
    async def get_action_confirmation(
        self, confirmation_id: str, user_id: str | None = None
    ) -> ActionConfirmation | None:
        """Return a confirmation by ID, optionally scoped to a user."""

    @abstractmethod
    async def list_action_confirmations(
        self,
        user_id: str,
        source: ConfirmationSource,
        session_key: str,
        status: str | None = None,
    ) -> list[ActionConfirmation]:
        """List confirmations for a user session, optionally narrowed by status."""

    @abstractmethod
    async def list_batch_action_confirmations(self, user_id: str, batch_id: str) -> list[ActionConfirmation]:
        """List confirmations for a user's batch."""

    @abstractmethod
    async def decide_action_confirmation(
        self,
        confirmation_id: str,
        user_id: str,
        decision: ConfirmationDecision,
    ) -> ActionConfirmation | None:
        """Approve or deny a pending confirmation."""

    @abstractmethod
    async def claim_action_confirmation_for_execution(
        self,
        confirmation_id: str,
        user_id: str,
    ) -> ActionConfirmation | None:
        """Atomically consume an approved confirmation before executing it."""

    @abstractmethod
    async def find_action_confirmation_grant(
        self,
        user_id: str,
        source: ConfirmationSource,
        session_key: str,
        tool_name: str,
        action: str,
        resource_type: str,
        resource_id: str,
        arguments_hash: str,
        statuses: tuple[str, ...] = ("approved", "denied"),
    ) -> ActionConfirmation | None:
        """Return the newest unexpired match in *statuses* for this action scope."""
