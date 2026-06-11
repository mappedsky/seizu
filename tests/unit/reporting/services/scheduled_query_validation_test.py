from reporting.schema.report_config import ActionConfigFieldDef
from reporting.services.scheduled_query_validation import validate_action_configs


def _schemas():
    return {
        "temporal": [
            ActionConfigFieldDef(
                name="workflow",
                label="Workflow",
                type="select",
                required=True,
                options=["cve_repo_report"],
            ),
            ActionConfigFieldDef(
                name="accept_confirmation_bypass",
                label="Accept",
                type="boolean",
                required=True,
                default=False,
            ),
        ],
        "agent_chat": [
            ActionConfigFieldDef(
                name="prompt",
                label="Prompt",
                type="text",
                required=True,
            ),
        ],
    }


def _patch_schemas(mocker):
    mocker.patch(
        "reporting.scheduled_query_modules.get_action_schemas",
        return_value=_schemas(),
    )
    mocker.patch(
        "reporting.scheduled_query_modules.get_action_permissions",
        return_value={"agent_chat": "chat:bypass_permissions"},
    )


def test_unknown_action_type(mocker):
    _patch_schemas(mocker)
    error = validate_action_configs([{"action_type": "nope", "action_config": {}}])
    assert error is not None
    assert "Unknown action type" in error


def test_missing_required_field(mocker):
    _patch_schemas(mocker)
    error = validate_action_configs(
        [{"action_type": "temporal", "action_config": {"accept_confirmation_bypass": True}}]
    )
    assert error is not None
    assert "workflow" in error


def test_required_boolean_must_be_true(mocker):
    _patch_schemas(mocker)
    for value in (False, "yes", 1):
        error = validate_action_configs(
            [
                {
                    "action_type": "temporal",
                    "action_config": {"workflow": "cve_repo_report", "accept_confirmation_bypass": value},
                }
            ]
        )
        assert error is not None, f"value {value!r} should fail"
        assert "accept_confirmation_bypass" in error


def test_valid_config_passes(mocker):
    _patch_schemas(mocker)
    error = validate_action_configs(
        [
            {
                "action_type": "temporal",
                "action_config": {"workflow": "cve_repo_report", "accept_confirmation_bypass": True},
            }
        ]
    )
    assert error is None


def test_permission_gated_action_rejected_without_permission(mocker):
    _patch_schemas(mocker)
    error = validate_action_configs(
        [{"action_type": "agent_chat", "action_config": {"prompt": "do it"}}],
        permissions=frozenset({"scheduled_queries:write"}),
    )
    assert error is not None
    assert "chat:bypass_permissions" in error


def test_permission_gated_action_allowed_with_permission(mocker):
    _patch_schemas(mocker)
    error = validate_action_configs(
        [{"action_type": "agent_chat", "action_config": {"prompt": "do it"}}],
        permissions=frozenset({"scheduled_queries:write", "chat:bypass_permissions"}),
    )
    assert error is None


def test_permission_gate_skipped_when_permissions_not_provided(mocker):
    """Callers that don't pass permissions (e.g. internal paths) skip the gate."""
    _patch_schemas(mocker)
    error = validate_action_configs([{"action_type": "agent_chat", "action_config": {"prompt": "do it"}}])
    assert error is None
