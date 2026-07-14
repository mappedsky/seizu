import json

import pytest

from reporting.temporal_workflows import (
    WorkflowInputContext,
    cartography_config,
    validate_workflow_action_config,
    workflow_config_schemas,
)


def _context(**config) -> WorkflowInputContext:
    return WorkflowInputContext(
        scheduled_query_id="sq-1",
        creator_user_id="user-1",
        rows=[],
        chat_timeout_seconds=60,
        action_config={"workflow": "cartography_sync", **config},
    )


def test_config_fields_lists_enabled_modules():
    fields = {f.name: f for f in cartography_config.config_fields()}
    assert set(fields) == {"modules", "pipeline", "stop_on_failure", "timeout_minutes"}
    assert "aws" in fields["modules"].description
    assert "github" in fields["modules"].description


def test_enabled_modules_honors_allowlist(mocker):
    mocker.patch("reporting.settings.CARTOGRAPHY_ENABLED_MODULES", ["cve", "github", "not-a-module"])
    assert cartography_config.enabled_module_names() == ["cve", "github"]


def test_validate_requires_exactly_one_of_modules_or_pipeline():
    assert "exactly one" in cartography_config.validate_config({})
    both = {"modules": ["cve"], "pipeline": '{"stages": []}'}
    assert "exactly one" in cartography_config.validate_config(both)


def test_validate_modules_accepts_known_modules():
    assert cartography_config.validate_config({"modules": ["cve", "github"]}) is None


def test_validate_modules_rejects_unknown_and_disabled(mocker):
    error = cartography_config.validate_config({"modules": ["gcp"]})
    assert "not enabled" in error
    mocker.patch("reporting.settings.CARTOGRAPHY_ENABLED_MODULES", ["cve"])
    error = cartography_config.validate_config({"modules": ["github"]})
    assert "not enabled" in error


def test_validate_pipeline_valid_json():
    pipeline = json.dumps(
        {
            "stages": [
                {"runs": [{"module": "aws", "params": {"aws_sync_all_profiles": True}}, {"module": "github"}]},
                {"runs": [{"module": "cve"}]},
            ]
        }
    )
    assert cartography_config.validate_config({"pipeline": pipeline}) is None


def test_validate_pipeline_bad_json_and_shape():
    assert "invalid JSON" in cartography_config.validate_config({"pipeline": "{nope"})
    assert "stages" in cartography_config.validate_config({"pipeline": '{"stages": []}'})


def test_validate_pipeline_rejects_disallowed_params():
    pipeline = json.dumps({"stages": [{"runs": [{"module": "cve", "params": {"neo4j_uri": "bolt://evil"}}]}]})
    error = cartography_config.validate_config({"pipeline": pipeline})
    assert "stage 1 run 1" in error
    assert "does not allow param" in error


def test_validate_timeout_bounds():
    assert cartography_config.validate_config({"modules": ["cve"], "timeout_minutes": 0})
    assert cartography_config.validate_config({"modules": ["cve"], "timeout_minutes": 100000})
    assert cartography_config.validate_config({"modules": ["cve"], "timeout_minutes": 90}) is None


def test_build_input_modules_become_sequential_stages(mocker):
    mocker.patch("reporting.settings.CARTOGRAPHY_TASK_QUEUE", "carto-q")
    result = cartography_config.build_input(_context(modules=["aws", "cve"], timeout_minutes=30))
    assert [run.module for stage in result.stages for run in stage.runs] == ["aws", "cve"]
    assert all(len(stage.runs) == 1 for stage in result.stages)
    assert result.activity_task_queue == "carto-q"
    assert result.module_timeout_seconds == 30 * 60
    assert result.stop_on_failure is False


def test_build_input_pipeline_preserves_stage_grouping():
    pipeline = json.dumps(
        {"stages": [{"runs": [{"module": "aws"}, {"module": "github"}]}, {"runs": [{"module": "cve"}]}]}
    )
    result = cartography_config.build_input(_context(pipeline=pipeline, stop_on_failure=True))
    assert [len(stage.runs) for stage in result.stages] == [2, 1]
    assert result.stop_on_failure is True


def test_build_input_raises_when_config_no_longer_validates(mocker):
    mocker.patch("reporting.settings.CARTOGRAPHY_ENABLED_MODULES", ["cve"])
    with pytest.raises(ValueError, match="not enabled"):
        cartography_config.build_input(_context(modules=["github"]))


def test_workflow_config_schemas_exposes_cartography_fields():
    schemas = workflow_config_schemas()
    assert "cartography_sync" in schemas
    assert {f.name for f in schemas["cartography_sync"]} >= {"modules", "pipeline"}
    # Row-consuming workflows without config_fields aren't listed.
    assert "cve_repo_report" not in schemas


def test_validate_workflow_action_config_dispatches_to_validator():
    error = validate_workflow_action_config({"workflow": "cartography_sync"})
    assert "exactly one" in error
    assert validate_workflow_action_config({"workflow": "cartography_sync", "modules": ["cve"]}) is None
    # Unknown workflow is reported; missing workflow is left to the generic check.
    assert "Unknown or disabled" in validate_workflow_action_config({"workflow": "nope"})
    assert validate_workflow_action_config({}) is None
    # Workflows without their own config validate trivially.
    assert validate_workflow_action_config({"workflow": "cve_repo_report"}) is None
