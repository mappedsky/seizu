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
    mocker.patch(
        "reporting.services.action_confirmations.report_store.generate_id",
        return_value="123456789012345678",
    )
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
    assert result.confirmation_id == "123456789012345678"
    assert result.arguments == {"report_id": "r1", "comment": "new"}
    create_confirmation.assert_awaited_once()


async def test_public_arguments_mirror_model_provided_arguments(mocker):
    """Public arguments show user-provided model args as-is when they are already safe."""
    mocker.patch(
        "reporting.services.action_confirmations.report_store.generate_id",
        return_value="123456789012345678",
    )
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


def test_bearer_session_key_and_batch_payload_are_stable(mocker):
    mocker.patch("reporting.services.action_confirmations.settings.SEIZU_PUBLIC_URL", "https://seizu.example.com")
    confirmation = _confirmation().model_copy(update={"batch_id": "batch-1"})

    assert action_confirmations.bearer_session_key("token") == action_confirmations.bearer_session_key("token")
    assert action_confirmations.bearer_session_key("token") != action_confirmations.bearer_session_key("other")
    payload = action_confirmations.confirmation_required_payload(confirmation)
    assert payload["batch_id"] == "batch-1"
    assert payload["batch_url"] == "https://seizu.example.com/app/confirmations/batch/batch-1"


async def test_decide_confirmation_returns_none_when_missing(mocker):
    mocker.patch(
        "reporting.services.action_confirmations.report_store.get_action_confirmation",
        return_value=None,
    )

    assert (
        await action_confirmations.decide_confirmation(
            confirmation_id="missing",
            user_id="user-1",
            decision="approved",
        )
        is None
    )


async def test_decide_confirmation_surfaces_expired_and_already_decided(mocker):
    get_confirmation = mocker.patch(
        "reporting.services.action_confirmations.report_store.get_action_confirmation",
        return_value=_confirmation().model_copy(update={"expires_at": _PAST}),
    )
    decide = mocker.patch(
        "reporting.services.action_confirmations.report_store.decide_action_confirmation",
    )

    expired = await action_confirmations.decide_confirmation(
        confirmation_id="confirm-1",
        user_id="user-1",
        decision="approved",
    )
    assert expired is not None and expired.status == "expired"
    decide.assert_not_awaited()

    get_confirmation.return_value = _confirmation("denied")
    denied = await action_confirmations.decide_confirmation(
        confirmation_id="confirm-1",
        user_id="user-1",
        decision="approved",
    )
    assert denied is not None and denied.status == "denied"
    decide.assert_not_awaited()


async def test_decide_confirmation_returns_successful_write(mocker):
    pending = _confirmation()
    approved = pending.model_copy(update={"status": "approved"})
    mocker.patch(
        "reporting.services.action_confirmations.report_store.get_action_confirmation",
        return_value=pending,
    )
    decide = mocker.patch(
        "reporting.services.action_confirmations.report_store.decide_action_confirmation",
        return_value=approved,
    )

    result = await action_confirmations.decide_confirmation(
        confirmation_id="confirm-1",
        user_id="user-1",
        decision="approved",
    )

    assert result == approved
    decide.assert_awaited_once_with(
        confirmation_id="confirm-1",
        user_id="user-1",
        decision="approved",
    )


async def test_decide_confirmation_refetches_after_write_race(mocker):
    pending = _confirmation()
    get_confirmation = mocker.patch(
        "reporting.services.action_confirmations.report_store.get_action_confirmation",
        side_effect=[pending, None],
    )
    mocker.patch(
        "reporting.services.action_confirmations.report_store.decide_action_confirmation",
        return_value=None,
    )

    result = await action_confirmations.decide_confirmation(
        confirmation_id="confirm-1",
        user_id="user-1",
        decision="denied",
    )

    assert result is None
    assert get_confirmation.await_count == 2


async def test_decide_confirmation_refetches_expiry_or_concurrent_decision(mocker):
    pending = _confirmation()
    get_confirmation = mocker.patch(
        "reporting.services.action_confirmations.report_store.get_action_confirmation",
        side_effect=[pending, pending.model_copy(update={"expires_at": _PAST})],
    )
    mocker.patch(
        "reporting.services.action_confirmations.report_store.decide_action_confirmation",
        return_value=None,
    )

    expired = await action_confirmations.decide_confirmation(
        confirmation_id="confirm-1",
        user_id="user-1",
        decision="approved",
    )
    assert expired is not None and expired.status == "expired"

    get_confirmation.side_effect = [pending, pending.model_copy(update={"status": "denied"})]
    denied = await action_confirmations.decide_confirmation(
        confirmation_id="confirm-1",
        user_id="user-1",
        decision="approved",
    )
    assert denied is not None and denied.status == "denied"
