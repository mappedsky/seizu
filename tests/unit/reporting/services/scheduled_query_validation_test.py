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
        ]
    }


def _patch_schemas(mocker, validators=None):
    mocker.patch(
        "reporting.scheduled_query_modules.get_action_schemas",
        return_value=_schemas(),
    )
    mocker.patch(
        "reporting.scheduled_query_modules.get_action_validators",
        return_value=validators or {},
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


def test_module_validator_hook_reports_error(mocker):
    _patch_schemas(mocker, validators={"temporal": lambda config: "custom rule violated"})
    error = validate_action_configs(
        [
            {
                "action_type": "temporal",
                "action_config": {"workflow": "cve_repo_report", "accept_confirmation_bypass": True},
            }
        ]
    )
    assert error is not None
    assert "custom rule violated" in error


def test_module_validator_hook_passes(mocker):
    _patch_schemas(mocker, validators={"temporal": lambda config: None})
    error = validate_action_configs(
        [
            {
                "action_type": "temporal",
                "action_config": {"workflow": "cve_repo_report", "accept_confirmation_bypass": True},
            }
        ]
    )
    assert error is None
