"""Account-wide sandbox operations: tagging, listing, state and killing by id.

These are the provider-specific half of the reaper (SBX-011). Everything E2B
knows about lives here, so ``session_reaper`` can be tested without a provider.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from reporting.services import sandbox_backend
from reporting.services.sandbox_backend import (
    MANAGED_METADATA_KEY,
    PURPOSE_METADATA_KEY,
    THREAD_METADATA_KEY,
    kill_sandbox,
    list_paused_sandboxes,
    open_backend,
    sandbox_is_paused,
)


def _patch_sandbox_module(**attrs: Any) -> Any:
    module = MagicMock()
    for name, value in attrs.items():
        setattr(module.AsyncSandbox, name, value)
    return patch.dict("sys.modules", {"e2b_code_interpreter": module})


def _listed(sandbox_id: str, *, metadata: dict[str, str] | None = None, **fields: Any) -> Any:
    return SimpleNamespace(
        sandbox_id=sandbox_id,
        metadata={} if metadata is None else metadata,
        started_at=fields.get("started_at", datetime(2026, 8, 1, tzinfo=UTC)),
        end_at=fields.get("end_at"),
    )


class _FakePaginator:
    """Two pages, so the pagination loop is exercised rather than assumed."""

    def __init__(self, pages: list[list[Any]]) -> None:
        self._pages = pages

    @property
    def has_next(self) -> bool:
        return bool(self._pages)

    async def next_items(self) -> list[Any]:
        return self._pages.pop(0)


def _account(deployment: str = "prod") -> Any:
    stack = patch.multiple(
        "reporting.settings",
        SANDBOX_API_KEY="k",
        SANDBOX_DOMAIN="",
        SEIZU_DEPLOYMENT_ID=deployment,
    )
    return stack


async def test_a_sandbox_is_tagged_with_its_deployment_purpose_and_thread() -> None:
    """The deployment id is the only ownership claim the reaper acts on, and the
    thread is what lets it find the session that owns the sandbox."""
    created: dict[str, Any] = {}

    async def _create(**kwargs: Any) -> Any:
        created.update(kwargs)
        return MagicMock(sandbox_id="sandbox-fake", kill=AsyncMock(), pause=AsyncMock())

    with _account(), _patch_sandbox_module(create=AsyncMock(side_effect=_create)):
        async with open_backend(api_key="k", domain="", purpose="chat-session", thread="user:u1:thread:t1"):
            pass

    assert created["metadata"] == {
        MANAGED_METADATA_KEY: "prod",
        PURPOSE_METADATA_KEY: "chat-session",
        THREAD_METADATA_KEY: "user:u1:thread:t1",
    }


async def test_a_sandbox_outside_a_chat_thread_carries_no_thread_tag() -> None:
    """Remediation and proxy sandboxes belong to no session and outlive nothing."""
    created: dict[str, Any] = {}

    async def _create(**kwargs: Any) -> Any:
        created.update(kwargs)
        return MagicMock(sandbox_id="sandbox-fake", kill=AsyncMock(), pause=AsyncMock())

    with _account(), _patch_sandbox_module(create=AsyncMock(side_effect=_create)):
        async with open_backend(api_key="k", domain="", purpose="remediation"):
            pass

    assert THREAD_METADATA_KEY not in created["metadata"]


async def test_an_unset_deployment_id_still_produces_an_owner() -> None:
    """An empty tag would be indistinguishable from an untagged sandbox, which
    is the one thing the ownership check must be able to tell apart."""
    created: dict[str, Any] = {}

    async def _create(**kwargs: Any) -> Any:
        created.update(kwargs)
        return MagicMock(sandbox_id="sandbox-fake", kill=AsyncMock(), pause=AsyncMock())

    with _account(deployment=""), _patch_sandbox_module(create=AsyncMock(side_effect=_create)):
        async with open_backend(api_key="k", domain=""):
            pass

    assert created["metadata"][MANAGED_METADATA_KEY] == sandbox_backend.DEFAULT_DEPLOYMENT_ID


async def test_listing_filters_to_this_deployment_provider_side() -> None:
    """Client-side filtering would spend the page cap on a shared account's
    other sandboxes and could leave this deployment's own permanently unseen."""
    from e2b import SandboxState

    lister = MagicMock(return_value=_FakePaginator([]))
    with _account(), _patch_sandbox_module(list=lister):
        assert await list_paused_sandboxes() == []

    query = lister.call_args.kwargs["query"]
    assert query.state == [SandboxState.PAUSED]
    assert query.metadata == {MANAGED_METADATA_KEY: "prod"}


async def test_listing_all_owners_drops_the_filter() -> None:
    lister = MagicMock(return_value=_FakePaginator([]))
    with _account(), _patch_sandbox_module(list=lister):
        assert await list_paused_sandboxes(all_owners=True) == []

    assert lister.call_args.kwargs["query"].metadata is None


async def test_listing_returns_provider_agnostic_snapshots() -> None:
    paused_at = datetime(2026, 8, 9, tzinfo=UTC)
    pages = [
        [
            _listed(
                "sbx-1",
                metadata={
                    MANAGED_METADATA_KEY: "prod",
                    PURPOSE_METADATA_KEY: "chat-session",
                    THREAD_METADATA_KEY: "user:u1:thread:t1",
                },
                end_at=paused_at,
            )
        ],
        [_listed("sbx-2")],
    ]
    with _account(), _patch_sandbox_module(list=MagicMock(return_value=_FakePaginator(pages))):
        snapshots = await list_paused_sandboxes(all_owners=True)
        ours, foreign = snapshots
        assert ours.ours is True
        # No tag: created by something else, or before tagging existed.
        assert foreign.ours is False

    assert [s.sandbox_id for s in snapshots] == ["sbx-1", "sbx-2"]
    assert (ours.owner, ours.purpose, ours.thread, ours.end_at) == (
        "prod",
        "chat-session",
        "user:u1:thread:t1",
        paused_at,
    )


async def test_a_sibling_deployments_sandbox_is_not_ours() -> None:
    pages = [[_listed("sbx-staging", metadata={MANAGED_METADATA_KEY: "staging"})]]
    with _account(), _patch_sandbox_module(list=MagicMock(return_value=_FakePaginator(pages))):
        (snapshot,) = await list_paused_sandboxes(all_owners=True)
        assert snapshot.ours is False


async def test_listing_stops_after_the_page_cap() -> None:
    """A provider that keeps handing out a next-token must not spin forever."""

    class _Endless:
        has_next = True

        async def next_items(self) -> list[Any]:
            return [_listed("sbx-x")]

    with (
        _account(),
        _patch_sandbox_module(list=MagicMock(return_value=_Endless())),
        patch.object(sandbox_backend, "_MAX_LIST_PAGES", 3),
    ):
        snapshots = await list_paused_sandboxes()

    assert len(snapshots) == 3


async def test_a_resumed_sandbox_reports_as_not_paused() -> None:
    """The check that stands between a stale listing and killing live work."""
    from e2b import SandboxState

    info = SimpleNamespace(state=SandboxState.RUNNING)
    with _account(), _patch_sandbox_module(get_info=AsyncMock(return_value=info)):
        assert await sandbox_is_paused("sbx-1") is False


async def test_an_unreadable_sandbox_is_treated_as_not_paused() -> None:
    """Skipping a reap is cheaper than destroying something still in use."""
    with _account(), _patch_sandbox_module(get_info=AsyncMock(side_effect=RuntimeError("boom"))):
        assert await sandbox_is_paused("sbx-1") is False


async def test_killing_by_id_passes_self_hosted_connection_options() -> None:
    """A self-hosted domain issues keys that fail E2B's client-side format check."""
    kill = AsyncMock()
    with (
        _patch_sandbox_module(kill=kill),
        patch("reporting.settings.SANDBOX_API_KEY", "k"),
        patch("reporting.settings.SANDBOX_DOMAIN", "sandbox.internal"),
    ):
        await kill_sandbox("sbx-1")

    kill.assert_awaited_once_with("sbx-1", api_key="k", domain="sandbox.internal", validate_api_key=False)
