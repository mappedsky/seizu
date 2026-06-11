from reporting.temporal_workflows import WORKFLOW_REGISTRY, get_workflow_spec
from reporting.temporal_workflows.shared import group_rows_by_repo


def test_registry_contains_cve_repo_report():
    spec = get_workflow_spec("cve_repo_report")
    assert spec is not None
    assert spec.description


def test_registry_unknown_workflow():
    assert get_workflow_spec("nope") is None


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
