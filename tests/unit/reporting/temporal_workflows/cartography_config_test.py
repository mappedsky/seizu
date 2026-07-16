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


def test_settings_allowlist_parser_strips_whitespace(monkeypatch):
    from cartography_sync.registry import parse_enabled_modules

    monkeypatch.setattr(
        cartography_config.settings,
        "CARTOGRAPHY_ENABLED_MODULES",
        parse_enabled_modules(" cve, github ,,cve "),
    )
    assert cartography_config.enabled_module_names() == ["cve", "github"]


def test_enabled_modules_never_include_internal_stages(mocker):
    assert "create-indexes" not in cartography_config.enabled_module_names()
    assert "analysis" not in cartography_config.enabled_module_names()
    # Not even when an operator lists them explicitly.
    mocker.patch("reporting.settings.CARTOGRAPHY_ENABLED_MODULES", ["cve", "analysis"])
    assert cartography_config.enabled_module_names() == ["cve"]


def test_internal_stages_rejected_in_user_config():
    error = cartography_config.validate_config({"modules": ["analysis"]})
    assert "automatically" in error


def test_duplicate_module_within_stage_rejected():
    pipeline = json.dumps({"stages": [{"runs": [{"module": "cve"}, {"module": "cve"}]}]})
    error = cartography_config.validate_config({"pipeline": pipeline})
    assert "more than once" in error
    # The same module in separate (sequential) stages is fine.
    pipeline = json.dumps({"stages": [{"runs": [{"module": "cve"}]}, {"runs": [{"module": "cve"}]}]})
    assert cartography_config.validate_config({"pipeline": pipeline}) is None


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
    # Indexes once before ingestion, analysis once after all of it.
    assert [run.module for stage in result.stages for run in stage.runs] == [
        "create-indexes",
        "aws",
        "cve",
        "analysis",
    ]
    assert all(len(stage.runs) == 1 for stage in result.stages)
    assert result.activity_task_queue == "carto-q"
    assert result.module_timeout_seconds == 30 * 60
    assert result.stop_on_failure is False


def test_build_input_pipeline_preserves_stage_grouping():
    pipeline = json.dumps(
        {"stages": [{"runs": [{"module": "aws"}, {"module": "github"}]}, {"runs": [{"module": "cve"}]}]}
    )
    result = cartography_config.build_input(_context(pipeline=pipeline, stop_on_failure=True))
    # create-indexes + the two user stages + analysis.
    assert [len(stage.runs) for stage in result.stages] == [1, 2, 1, 1]
    assert result.stages[0].runs[0].module == "create-indexes"
    assert result.stages[-1].runs[0].module == "analysis"
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
