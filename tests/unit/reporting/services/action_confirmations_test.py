from reporting.schema.confirmations import ActionConfirmation, ActionConfirmationTarget
from reporting.services import action_confirmations

_NOW = "2024-01-01T00:00:00+00:00"
_LATER = "2099-01-01T00:30:00+00:00"
_PAST = "2000-01-01T00:00:00+00:00"


def _confirmation(status: str = "pending", arguments: dict[str, object] | None = None) -> ActionConfirmation:
    args = arguments or {"report_id": "r1"}
    return ActionConfirmation.model_validate(
        {
            "confirmation_id": "confirm-1",
            "user_id": "user-1",
            "source": "mcp",
            "session_key": "session-1",
            "tool_name": "reports__delete",
            "action": "delete",
            "resource_type": "report",
            "resource_id": "r1",
            "arguments": args,
            "arguments_hash": action_confirmations.arguments_hash(args),
            "status": status,
            "created_at": _NOW,
            "expires_at": _LATER,
        }
    )


async def test_pending_confirmation_is_bound_to_arguments_hash(mocker):
    create_confirmation = mocker.patch(
        "reporting.services.action_confirmations.report_store.create_action_confirmation",
        side_effect=lambda confirmation: confirmation,
    )
    # No approved/denied grant, no matching pending — a new confirmation is created.
    mocker.patch(
        "reporting.services.action_confirmations.report_store.find_action_confirmation_grant",
        return_value=None,
    )

    result = await action_confirmations.ensure_confirmation(
        user_id="user-1",
        source="mcp",
        session_key="session-1",
        tool_name="reports__delete",
        target=ActionConfirmationTarget(action="delete", resource_type="report", resource_id="r1"),
        arguments={"report_id": "r1", "comment": "new"},
    )

    assert result is not None
    assert result.arguments == {"report_id": "r1", "comment": "new"}
    create_confirmation.assert_awaited_once()


async def test_public_arguments_mirror_model_provided_arguments(mocker):
    """Public arguments show user-provided model args as-is when they are already safe."""
    mocker.patch(
        "reporting.services.action_confirmations.report_store.create_action_confirmation",
        side_effect=lambda confirmation: confirmation,
    )
    mocker.patch(
        "reporting.services.action_confirmations.report_store.find_action_confirmation_grant",
        return_value=None,
    )

    result = await action_confirmations.ensure_confirmation(
        user_id="user-1",
        source="mcp",
        session_key="session-1",
        tool_name="some__tool",
        target=ActionConfirmationTarget(action="create", resource_type="thing", resource_id="t1"),
        arguments={"keyword": "search-term", "token_count": 42, "cache_key": "abc", "name": "my-thing"},
    )

    assert result is not None
    assert result.arguments == {"keyword": "search-term", "token_count": 42, "cache_key": "abc", "name": "my-thing"}


async def test_approved_confirmation_is_claimed_before_execution(mocker):
    approved = _confirmation("approved")
    mocker.patch(
        "reporting.services.action_confirmations.report_store.find_action_confirmation_grant",
        return_value=approved,
    )
    claim = mocker.patch(
        "reporting.services.action_confirmations.report_store.claim_action_confirmation_for_execution",
        return_value=approved.model_copy(update={"status": "executed"}),
    )

    result = await action_confirmations.ensure_confirmation(
        user_id="user-1",
        source="mcp",
        session_key="session-1",
        tool_name="reports__delete",
        target=ActionConfirmationTarget(action="delete", resource_type="report", resource_id="r1"),
        arguments={"report_id": "r1"},
    )

    assert result is None
    claim.assert_awaited_once_with("confirm-1", "user-1")


async def test_concurrent_race_on_approved_confirmation_returns_executed(mocker):
    approved = _confirmation("approved")
    mocker.patch(
        "reporting.services.action_confirmations.report_store.find_action_confirmation_grant",
        return_value=approved,
    )
    mocker.patch(
        "reporting.services.action_confirmations.report_store.claim_action_confirmation_for_execution",
        return_value=None,
    )
    mocker.patch(
        "reporting.services.action_confirmations.report_store.get_action_confirmation",
        return_value=approved.model_copy(update={"status": "executed"}),
    )

    result = await action_confirmations.ensure_confirmation(
        user_id="user-1",
        source="mcp",
        session_key="session-1",
        tool_name="reports__delete",
        target=ActionConfirmationTarget(action="delete", resource_type="report", resource_id="r1"),
        arguments={"report_id": "r1"},
    )

    assert result is not None
    assert result.status == "executed"


async def test_expired_approved_confirmation_returns_expired_not_executed(mocker):
    """Claim failing due to expiry must surface 'expired', not the misleading 'executed' sentinel."""
    approved = _confirmation("approved")
    mocker.patch(
        "reporting.services.action_confirmations.report_store.find_action_confirmation_grant",
        return_value=approved,
    )
    mocker.patch(
        "reporting.services.action_confirmations.report_store.claim_action_confirmation_for_execution",
        return_value=None,
    )
    # Re-fetch shows still "approved" (expiry prevented the claim, nothing was written).
    expired_approved = approved.model_copy(update={"expires_at": _PAST})
    mocker.patch(
        "reporting.services.action_confirmations.report_store.get_action_confirmation",
        return_value=expired_approved,
    )

    result = await action_confirmations.ensure_confirmation(
        user_id="user-1",
        source="mcp",
        session_key="session-1",
        tool_name="reports__delete",
        target=ActionConfirmationTarget(action="delete", resource_type="report", resource_id="r1"),
        arguments={"report_id": "r1"},
    )

    assert result is not None
    assert result.status == "expired"


# ---------------------------------------------------------------------------
# _public_ui_origin
# ---------------------------------------------------------------------------


def test_public_ui_origin_uses_seizu_public_url(mocker):
    mocker.patch("reporting.services.action_confirmations.settings.SEIZU_PUBLIC_URL", "https://seizu.example.com")
    url = action_confirmations.public_confirmation_url("abc123")
    assert url == "https://seizu.example.com/app/confirmations/abc123"


def test_public_ui_origin_derives_from_mcp_resource_url(mocker):
    mocker.patch("reporting.services.action_confirmations.settings.SEIZU_PUBLIC_URL", "")
    mocker.patch("reporting.services.action_confirmations.settings.MCP_RESOURCE_URL", "https://api.example.com/mcp")
    url = action_confirmations.public_confirmation_url("abc123")
    assert url.startswith("https://api.example.com/app/confirmations/")


def test_public_ui_origin_returns_path_only_when_no_base_url(mocker):
    mocker.patch("reporting.services.action_confirmations.settings.SEIZU_PUBLIC_URL", "")
    mocker.patch("reporting.services.action_confirmations.settings.MCP_RESOURCE_URL", "")
    url = action_confirmations.public_confirmation_url("abc123")
    assert url == "/app/confirmations/abc123"


def test_public_ui_origin_returns_path_only_when_resource_url_has_no_scheme(mocker):
    mocker.patch("reporting.services.action_confirmations.settings.SEIZU_PUBLIC_URL", "")
    mocker.patch("reporting.services.action_confirmations.settings.MCP_RESOURCE_URL", "not-a-url")
    url = action_confirmations.public_confirmation_url("abc123")
    assert url == "/app/confirmations/abc123"
