from pathlib import Path

import yaml

from reporting.schema.external_mcp import parse_external_mcp_proxies
from reporting.services import external_mcp, mcp_runtime
from reporting.services.plugin_packages import files_from_directory, logical_mcp_ref, parse_package


def _activity(workflow: dict, stage: int, position: int = 0) -> dict:
    return workflow["stages"][stage]["activities"][position]


def test_portable_plugin_dependencies_match_development_proxy_aliases(mocker) -> None:
    root = Path(__file__).parents[4]
    config_path = root / ".config/dev/seizu/reporting-dashboard.yaml"
    config = yaml.safe_load(config_path.read_text())
    plugin_config = config["plugins"]["portable_security_review"]
    package_root = config_path.parent / plugin_config["source"]
    parsed = parse_package(files_from_directory(package_root))

    assert parsed.valid
    assert parsed.plugin_id == "portable_security_review"
    assert len(parsed.skills) == 1

    example_line = next(
        line
        for line in (root / ".env.example").read_text().splitlines()
        if line.startswith("# MCP_EXTERNAL_PROXIES=") and '"upstream_urls"' in line
    )
    proxies = parse_external_mcp_proxies(example_line.removeprefix("# MCP_EXTERNAL_PROXIES="))
    proxies_by_upstream = {upstream: proxy for proxy in proxies for upstream in proxy.upstream_urls}

    available: set[str] = set()
    for dependency in parsed.skills[0].allowed_tools:
        logical = logical_mcp_ref(dependency)
        assert logical is not None
        server_name, tool_name = logical
        upstream = parsed.skills[0].mcp_servers[server_name]["url"]
        assert proxies_by_upstream[upstream].name == server_name
        available.add(external_mcp.namespaced_tool_name(server_name, tool_name))

    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_PROXIES", proxies)
    resolved, missing = mcp_runtime._resolve_plugin_allowed_tools(parsed.skills[0], available)  # noqa: SLF001

    assert set(resolved) == available
    assert missing == []


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
