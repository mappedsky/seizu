import pytest

from reporting.temporal_workflows import WorkflowInputContext, cartography_config


def _context(**config) -> WorkflowInputContext:
    return WorkflowInputContext(
        scheduled_query_id="sq-1",
        creator_user_id="user-1",
        rows=[],
        chat_timeout_seconds=60,
        action_config=config,
    )


def _runs(*modules: str) -> list[dict]:
    return [{"module": module} for module in modules]


def test_config_fields_exposes_module_runs():
    fields = {f.name: f for f in cartography_config.config_fields()}
    assert set(fields) == {"module_runs", "stop_on_failure", "timeout_minutes"}
    module_runs = fields["module_runs"]
    assert module_runs.type == "module_runs"
    assert module_runs.required is True
    assert "aws" in module_runs.options
    assert "github" in module_runs.options
    assert set(module_runs.item_schemas) == set(module_runs.options)


def test_module_param_fields_map_registry_flags():
    aws_fields = {f.name: f for f in cartography_config.module_param_fields("aws")}
    assert aws_fields["aws_sync_all_profiles"].type == "boolean"
    assert aws_fields["aws_sync_all_profiles"].default is True
    assert aws_fields["aws_requested_syncs"].type == "string"
    # Modules without configurable flags produce empty sub-forms; fixed
    # env-var/path flags do not become schedule-author-controlled fields.
    assert cartography_config.module_param_fields("create-indexes") == []
    github_fields = {f.name: f for f in cartography_config.module_param_fields("github")}
    assert github_fields["github_commit_lookback_days"].type == "number"

    metadata_fields = {f.name: f for f in cartography_config.module_param_fields("cve_metadata")}
    assert metadata_fields["cve_metadata_src"].type == "string_list"
    assert metadata_fields["cve_metadata_src"].options == ["nvd", "epss"]


def test_enabled_modules_honors_allowlist(mocker):
    mocker.patch("reporting.settings.CARTOGRAPHY_ENABLED_MODULES", ["cve", "github", "not-a-module"])
    assert cartography_config.enabled_module_names() == ["analysis", "create-indexes", "cve", "github"]


def test_settings_allowlist_parser_strips_whitespace(monkeypatch):
    from cartography_sync.registry import parse_enabled_modules

    monkeypatch.setattr(
        cartography_config.settings,
        "CARTOGRAPHY_ENABLED_MODULES",
        parse_enabled_modules(" cve, github ,,cve "),
    )
    assert cartography_config.enabled_module_names() == ["analysis", "create-indexes", "cve", "github"]


def test_structural_stages_are_selectable():
    names = cartography_config.enabled_module_names()
    assert "create-indexes" in names
    assert "analysis" in names
    assert cartography_config.validate_config({"module_runs": _runs("create-indexes", "cve", "analysis")}) is None


def test_repeated_module_runs_allowed():
    # Runs are sequential (one stage per run), so repeats never race.
    config = {"module_runs": _runs("create-indexes", "cve", "analysis", "github", "analysis")}
    assert cartography_config.validate_config(config) is None


def test_validate_requires_non_empty_module_runs():
    assert "non-empty" in cartography_config.validate_config({})
    assert "non-empty" in cartography_config.validate_config({"module_runs": []})
    assert "non-empty" in cartography_config.validate_config({"module_runs": "cve"})


def test_validate_rejects_malformed_run_entries():
    error = cartography_config.validate_config({"module_runs": ["cve"]})
    assert "module run 1" in error
    error = cartography_config.validate_config({"module_runs": [{"params": {}}]})
    assert "module run 1" in error


def test_validate_rejects_unknown_and_disabled(mocker):
    error = cartography_config.validate_config({"module_runs": _runs("not-a-module")})
    assert "not enabled" in error
    mocker.patch("reporting.settings.CARTOGRAPHY_ENABLED_MODULES", ["cve"])
    error = cartography_config.validate_config({"module_runs": _runs("github")})
    assert "not enabled" in error


def test_validate_rejects_disallowed_params():
    config = {"module_runs": [{"module": "cve", "params": {"neo4j_uri": "bolt://evil"}}]}
    error = cartography_config.validate_config(config)
    assert "module run 1" in error
    assert "does not allow param" in error


def test_validate_accepts_allowlisted_params():
    config = {"module_runs": [{"module": "aws", "params": {"aws_sync_all_profiles": True}}]}
    assert cartography_config.validate_config(config) is None


def test_validate_timeout_bounds():
    runs = _runs("cve")
    assert cartography_config.validate_config({"module_runs": runs, "timeout_minutes": 0})
    assert cartography_config.validate_config({"module_runs": runs, "timeout_minutes": 100000})
    assert cartography_config.validate_config({"module_runs": runs, "timeout_minutes": 90}) is None


def test_build_input_runs_become_sequential_stages(mocker):
    mocker.patch("reporting.settings.CARTOGRAPHY_TASK_QUEUE", "carto-q")
    result = cartography_config.build_input(
        _context(module_runs=_runs("create-indexes", "aws", "cve", "analysis"), timeout_minutes=30)
    )
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


def test_build_input_adds_no_implicit_stages():
    result = cartography_config.build_input(_context(module_runs=_runs("cve"), stop_on_failure=True))
    assert [run.module for stage in result.stages for run in stage.runs] == ["cve"]
    assert result.stop_on_failure is True


def test_build_input_preserves_params():
    result = cartography_config.build_input(
        _context(module_runs=[{"module": "aws", "params": {"aws_sync_all_profiles": False}}])
    )
    assert result.stages[0].runs[0].params == {"aws_sync_all_profiles": False}


def test_build_input_raises_when_config_no_longer_validates(mocker):
    mocker.patch("reporting.settings.CARTOGRAPHY_ENABLED_MODULES", ["cve"])
    with pytest.raises(ValueError, match="not enabled"):
        cartography_config.build_input(_context(module_runs=_runs("github")))
