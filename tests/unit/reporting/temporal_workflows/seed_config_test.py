from pathlib import Path

import yaml


def _activity(workflow: dict, stage: int, position: int = 0) -> dict:
    return workflow["stages"][stage]["activities"][position]


def test_cve_repo_workflow_uses_new_security_issue_observation() -> None:
    config_path = Path(__file__).parents[4] / ".config/dev/seizu/reporting-dashboard.yaml"
    config = yaml.safe_load(config_path.read_text())
    configured_workflow = next(
        item for item in config["workflows"] if item["name"] == "New CVEs affecting repositories"
    )
    query = _activity(configured_workflow, 0)
    cypher = query["parameters"]["cypher"]

    assert "datetime(s.created_at) > window_start" in cypher
    assert "s.firstseen" not in cypher
    assert "datetime(c.published_date) > window_start" not in cypher
    assert query["type"] == "query"
    assert query["output"] == "repository_cves"
    assert configured_workflow["watch_scans"] == [
        {
            "grouptype": "GitHubOrganization",
            "syncedtype": "GitHubOrganization",
        }
    ]


def test_cve_dependency_remediation_workflow() -> None:
    config_path = Path(__file__).parents[4] / ".config/dev/seizu/reporting-dashboard.yaml"
    config = yaml.safe_load(config_path.read_text())
    configured_workflow = next(
        item for item in config["workflows"] if item["name"] == "New CVE dependencies requiring remediation"
    )
    query = _activity(configured_workflow, 0)
    cypher = query["parameters"]["cypher"]

    # Select newly observed open security issues rather than newly published
    # CVEs. firstseen is returned as remediation context, not used as a filter.
    assert "datetime(s.created_at) > window_start" in cypher
    assert "firstseen: s.firstseen" in cypher
    assert "datetime(c.published_date) > window_start" not in cypher
    # Remediation needs a concrete package to upgrade.
    assert "s.dependency_package_name IS NOT NULL" in cypher
    # Org-agnostic: no hardcoded organization or organization-id filter, and
    # the watch scan matches every organization sync (groupid omitted → ".*").
    assert "mappedsky" not in cypher
    assert "WHERE o.id" not in cypher
    assert configured_workflow["watch_scans"] == [
        {
            "grouptype": "GitHubOrganization",
            "syncedtype": "GitHubOrganization",
        }
    ]
    assert query["type"] == "query"
    assert query["output"] == "vulnerable_dependencies"
    assert _activity(configured_workflow, 1) == {
        "type": "cve_dependency_remediation",
        "input": "vulnerable_dependencies",
        "output": "remediation_results",
        "parameters": {},
    }
