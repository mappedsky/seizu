from temporalio.converter import DataConverter

from reporting.temporal_workflows import (
    WORKFLOW_REGISTRY,
    WorkflowInputContext,
    enabled_workflow_names,
    get_enabled_workflow_spec,
    get_workflow_spec,
)
from reporting.temporal_workflows.shared import (
    ConfiguredActivityOutput,
    CveDependencyRemediationInput,
    group_rows_by_repo,
    group_rows_by_repo_package,
    normalize_configured_rows,
)


def test_normalize_configured_rows_recovers_legacy_record_payload():
    assert normalize_configured_rows(
        [[{"id": "CVE-1"}]],
        "payload",
    ) == [{"payload": {"id": "CVE-1"}}]


async def test_configured_activity_output_decodes_arbitrary_json_values():
    converter = DataConverter.default
    payloads = await converter.encode(
        [
            {
                "output_id": "findings",
                "value": [[{"id": "CVE-1"}]],
                "metadata": {"truncated": False},
            }
        ]
    )
    result = (await converter.decode(payloads, [ConfiguredActivityOutput]))[0]

    assert isinstance(result, ConfiguredActivityOutput)
    assert result.value == [[{"id": "CVE-1"}]]


def test_registry_contains_cve_repo_report():
    spec = get_workflow_spec("cve_repo_report")
    assert spec is not None
    assert spec.description


def test_registry_contains_cve_dependency_remediation(mocker):
    spec = get_workflow_spec("cve_dependency_remediation")
    assert spec is not None
    assert spec.description

    mocker.patch("reporting.settings.REMEDIATION_TIMEOUT_SECONDS", 3600)
    workflow_input = spec.build_input(
        WorkflowInputContext(
            scheduled_query_id="sq-1",
            creator_user_id="user-1",
            rows=[{"repo": "org/app", "package": "requests"}],
            chat_timeout_seconds=600,
        )
    )
    assert isinstance(workflow_input, CveDependencyRemediationInput)
    assert workflow_input.rows == [{"repo": "org/app", "package": "requests"}]
    # Remediation runs a full clone → upgrade → test → PR cycle, so its input
    # uses the dedicated (larger) timeout, not the context's generic one.
    assert workflow_input.timeout_seconds == 3600


def test_registry_unknown_workflow():
    assert get_workflow_spec("nope") is None


def test_enabled_workflow_names_defaults_to_all(mocker):
    mocker.patch("reporting.settings.TEMPORAL_ENABLED_WORKFLOWS", [])
    assert enabled_workflow_names() == sorted(WORKFLOW_REGISTRY)


def test_enabled_workflow_names_allowlist(mocker):
    # Unknown names are ignored; only registered + configured survive.
    mocker.patch("reporting.settings.TEMPORAL_ENABLED_WORKFLOWS", ["cve_repo_report", "does_not_exist"])
    assert enabled_workflow_names() == ["cve_repo_report"]


def test_get_enabled_workflow_spec_gates_on_allowlist(mocker):
    mocker.patch("reporting.settings.TEMPORAL_ENABLED_WORKFLOWS", ["cve_repo_report"])
    assert get_enabled_workflow_spec("cve_repo_report") is not None
    # Registered but not enabled.
    assert get_enabled_workflow_spec("cve_dependency_remediation") is None
    assert get_workflow_spec("cve_dependency_remediation") is not None


def test_registry_names_match_specs():
    for name, spec in WORKFLOW_REGISTRY.items():
        assert spec.name == name


def test_group_rows_by_repo():
    rows = [
        {"repo": "org/a", "cve_id": "CVE-1"},
        {"repo": "org/b", "cve_id": "CVE-2"},
        {"repo": "org/a", "cve_id": "CVE-3"},
        {"cve_id": "CVE-4"},
        {"repo": "", "cve_id": "CVE-5"},
        {"repo": 7, "cve_id": "CVE-6"},
    ]
    grouped = group_rows_by_repo(rows)
    assert set(grouped) == {"org/a", "org/b"}
    assert [row["cve_id"] for row in grouped["org/a"]] == ["CVE-1", "CVE-3"]
    assert [row["cve_id"] for row in grouped["org/b"]] == ["CVE-2"]


def test_group_rows_by_repo_package():
    rows = [
        {"repo": "org/a", "package": "requests", "cve_id": "CVE-1", "manifest_path": "a/requirements.txt"},
        {"repo": "org/a", "package": "requests", "cve_id": "CVE-2", "manifest_path": "b/requirements.txt"},
        {"repo": "org/a", "package": "flask", "cve_id": "CVE-3"},
        {"repo": "org/b", "package": "requests", "cve_id": "CVE-4"},
        {"repo": "org/c", "cve_id": "CVE-5"},
        {"package": "lodash", "cve_id": "CVE-6"},
        {"repo": "", "package": "requests", "cve_id": "CVE-7"},
        {"repo": "org/d", "package": "", "cve_id": "CVE-8"},
        {"repo": "org/e", "package": 7, "cve_id": "CVE-9"},
    ]
    grouped = group_rows_by_repo_package(rows)
    assert set(grouped) == {("org/a", "requests"), ("org/a", "flask"), ("org/b", "requests")}
    # One package in several manifests stays a single group (one chat / one PR).
    assert [row["cve_id"] for row in grouped[("org/a", "requests")]] == ["CVE-1", "CVE-2"]
    assert [row["cve_id"] for row in grouped[("org/b", "requests")]] == ["CVE-4"]
