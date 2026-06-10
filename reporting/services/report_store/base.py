from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from reporting.schema.chat import ChatSessionItem
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


class ReportStore(ABC):
    """Abstract base class for report configuration storage backends."""

    @abstractmethod
    def generate_id(self) -> str:
        """Return a new Snowflake ID from the backend's process-wide generator."""

    @abstractmethod
    async def initialize(self) -> None:
        """Perform any one-time setup required by the backend (e.g. create table)."""

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
    ) -> ReportListItem:
        """Create a new empty report (no initial version) and return the ReportListItem."""

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
        """Update report visibility without creating a new report version."""

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
        watch_scans: list[dict[str, Any]],
        enabled: bool,
        actions: list[dict[str, Any]],
        created_by: str,
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
        watch_scans: list[dict[str, Any]],
        enabled: bool,
        actions: list[dict[str, Any]],
        updated_by: str,
        comment: str | None = None,
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
    async def delete_scheduled_query(self, sq_id: str) -> bool:
        """Delete a scheduled query and all its versions. Returns False if not found."""

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
        """Return recent chat sessions for a user, sorted by updated_at descending."""

    @abstractmethod
    async def get_chat_session(self, user_id: str, thread_id: str) -> ChatSessionItem | None:
        """Return a chat session for a user, or None if it does not exist."""

    @abstractmethod
    async def create_chat_session(self, user_id: str, title: str) -> ChatSessionItem:
        """Create a new chat session with a store-generated ID."""

    @abstractmethod
    async def touch_chat_session(self, user_id: str, thread_id: str) -> ChatSessionItem | None:
        """Update a session's updated_at. Returns None if the session is not found."""

    @abstractmethod
    async def update_chat_session_title(self, user_id: str, thread_id: str, title: str) -> ChatSessionItem | None:
        """Update a session's title and updated_at. Returns None if the session is not found."""

    @abstractmethod
    async def delete_chat_session(self, user_id: str, thread_id: str) -> bool:
        """Delete a session. Returns False if not found."""

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
