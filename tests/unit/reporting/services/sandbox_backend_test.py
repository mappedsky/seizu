"""Account-wide sandbox operations: tagging, listing and killing by id.

These are the provider-specific half of the reaper (SBX-011). Everything E2B
knows about lives here, so ``sandbox_reaper`` can be tested without a provider.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from reporting.services import sandbox_backend
from reporting.services.sandbox_backend import (
    MANAGED_METADATA_KEY,
    kill_sandbox,
    list_paused_sandboxes,
    open_backend,
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


async def test_every_sandbox_is_tagged_as_seizus_at_creation() -> None:
    """Without the tag the sweep cannot tell Seizu's sandboxes from anything
    else sharing the API key, so it would have to leave all of them alone."""
    created: dict[str, Any] = {}

    async def _create(**kwargs: Any) -> Any:
        created.update(kwargs)
        return MagicMock(sandbox_id="sandbox-fake", kill=AsyncMock(), pause=AsyncMock())

    with _patch_sandbox_module(create=AsyncMock(side_effect=_create)):
        async with open_backend(api_key="k", domain="", purpose="chat-session"):
            pass

    assert created["metadata"] == {MANAGED_METADATA_KEY: "chat-session"}


async def test_listing_returns_provider_agnostic_snapshots() -> None:
    paused = datetime(2026, 8, 9, tzinfo=UTC)
    pages = [
        [_listed("sbx-1", metadata={MANAGED_METADATA_KEY: "chat-session"}, end_at=paused)],
        [_listed("sbx-2")],
    ]
    with (
        _patch_sandbox_module(list=MagicMock(return_value=_FakePaginator(pages))),
        patch("reporting.settings.SANDBOX_API_KEY", "k"),
        patch("reporting.settings.SANDBOX_DOMAIN", ""),
    ):
        snapshots = await list_paused_sandboxes()

    assert [s.sandbox_id for s in snapshots] == ["sbx-1", "sbx-2"]
    ours, foreign = snapshots
    assert (ours.managed, ours.purpose, ours.end_at) == (True, "chat-session", paused)
    # No tag: created by something else, or before tagging existed.
    assert (foreign.managed, foreign.purpose) == (False, "")


async def test_listing_asks_only_for_suspended_sandboxes() -> None:
    """A running sandbox already has a provider-enforced expiry; a paused one
    does not, which is the asymmetry the sweep exists for."""
    from e2b import SandboxState

    lister = MagicMock(return_value=_FakePaginator([]))
    with (
        _patch_sandbox_module(list=lister),
        patch("reporting.settings.SANDBOX_API_KEY", "k"),
        patch("reporting.settings.SANDBOX_DOMAIN", ""),
    ):
        assert await list_paused_sandboxes() == []

    assert lister.call_args.kwargs["query"].state == [SandboxState.PAUSED]


async def test_listing_stops_after_the_page_cap() -> None:
    """A provider that keeps handing out a next-token must not spin forever."""

    class _Endless:
        has_next = True

        async def next_items(self) -> list[Any]:
            return [_listed("sbx-x")]

    with (
        _patch_sandbox_module(list=MagicMock(return_value=_Endless())),
        patch("reporting.settings.SANDBOX_API_KEY", "k"),
        patch("reporting.settings.SANDBOX_DOMAIN", ""),
        patch.object(sandbox_backend, "_MAX_LIST_PAGES", 3),
    ):
        snapshots = await list_paused_sandboxes()

    assert len(snapshots) == 3


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
