from pathlib import Path

import yaml


def test_cve_repo_workflow_uses_new_security_issue_observation() -> None:
    config_path = Path(__file__).parents[4] / ".config/dev/seizu/reporting-dashboard.yaml"
    config = yaml.safe_load(config_path.read_text())
    scheduled_query = next(
        item for item in config["scheduled_queries"] if item["name"] == "New CVEs affecting repositories"
    )

    assert "datetime(s.created_at) > window_start" in scheduled_query["cypher"]
    assert "s.firstseen" not in scheduled_query["cypher"]
    assert "datetime(c.published_date) > window_start" not in scheduled_query["cypher"]
    assert scheduled_query["watch_scans"] == [
        {
            "grouptype": "GitHubOrganization",
            "syncedtype": "GitHubOrganization",
            "groupid": "https://github.com/mappedsky",
        }
    ]
