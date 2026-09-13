from pathlib import Path

import yaml

from reporting.schema.external_mcp import parse_external_mcp_proxies
from reporting.services import external_mcp, mcp_runtime
from reporting.services.mcp_builtins import registered_tool_names
from reporting.services.plugin_packages import (
    SEIZU_MCP_SERVER_NAME,
    ParsedPlugin,
    files_from_directory,
    mcp_tool_ref,
    parse_package,
)
from reporting.temporal_workflows import activities


def _activity(workflow: dict, stage: int, position: int = 0) -> dict:
    return workflow["stages"][stage]["activities"][position]


def _seed_config() -> tuple[Path, dict]:
    config_path = Path(__file__).parents[4] / ".config/dev/seizu/reporting-dashboard.yaml"
    return config_path, yaml.safe_load(config_path.read_text())


def _seeded_package(plugin_id: str) -> ParsedPlugin:
    """Parse the package the seed installs for one plugin id."""
    config_path, config = _seed_config()
    source = config_path.parent / config["plugins"][plugin_id]["source"]
    parsed = parse_package(files_from_directory(source))
    assert parsed.valid, [item.message for item in parsed.diagnostics]
    assert parsed.plugin_id == plugin_id
    return parsed


def test_portable_plugin_dependencies_match_development_proxy_aliases(mocker) -> None:
    root = Path(__file__).parents[4]
    config_path = root / ".config/dev/seizu/reporting-dashboard.yaml"
    config = yaml.safe_load(config_path.read_text())
    plugin_config = config["plugins"]["portable_security_review"]
    package_root = config_path.parent / plugin_config["source"]
    parsed = parse_package(files_from_directory(package_root))

    assert parsed.valid
    assert parsed.plugin_id == "portable_security_review"
    assert len(parsed.skills) == 2
    dependency_review = next(skill for skill in parsed.skills if skill.skill_id == "review_source_dependencies")

    example_line = next(
        line
        for line in (root / ".env.example").read_text().splitlines()
        if line.startswith("# MCP_EXTERNAL_PROXIES=") and '"upstream_urls"' in line
    )
    proxies = parse_external_mcp_proxies(example_line.removeprefix("# MCP_EXTERNAL_PROXIES="))
    proxies_by_upstream = {upstream: proxy for proxy in proxies for upstream in proxy.upstream_urls}

    available: set[str] = set()
    for dependency in dependency_review.allowed_tools:
        ref = mcp_tool_ref(dependency)
        assert ref is not None
        server_name, tool_name = ref
        upstream = dependency_review.mcp_servers[server_name]["url"]
        assert proxies_by_upstream[upstream].name == server_name
        available.add(external_mcp.namespaced_tool_name(server_name, tool_name))

    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_PROXIES", proxies)
    resolved, missing = mcp_runtime._resolve_plugin_allowed_tools(dependency_review, available)  # noqa: SLF001

    assert set(resolved) == available
    assert missing == []

    asset_check = next(skill for skill in parsed.skills if skill.skill_id == "verify_packaged_assets")
    assert asset_check.has_scripts is True
    resolved, missing = mcp_runtime._resolve_plugin_allowed_tools(  # noqa: SLF001
        asset_check,
        {"sandbox__read_file", "sandbox__run_script"},
    )
    assert resolved == ["sandbox__run_script", "sandbox__read_file"]
    assert missing == []


def test_production_security_plugin_is_seeded_independently_of_legacy_skillsets(mocker) -> None:
    root = Path(__file__).parents[4]
    config_path = root / ".config/dev/seizu/reporting-dashboard.yaml"
    config = yaml.safe_load(config_path.read_text())
    plugin_config = config["plugins"]["github_security_investigations"]
    package_root = config_path.parent / plugin_config["source"]
    parsed = parse_package(files_from_directory(package_root))

    assert parsed.valid
    assert parsed.plugin_id == "github_security_investigations"
    assert parsed.manifest["version"] == "1.3.1"
    # The package declares its skills; it does not decide which are on. Every
    # skill it introduces starts enabled, and the seed states the exception
    # (AGT-041).
    assert {skill.skill_id for skill in parsed.skills} == {
        "github_org_security_overview",
        "repo_cve_exploitability",
        "repo_cve_findings",
        "repo_cve_reachability",
    }
    assert all(skill.enabled for skill in parsed.skills)
    assert plugin_config["skills"] == {"repo_cve_exploitability": False}

    reachability = next(skill for skill in parsed.skills if skill.skill_id == "repo_cve_reachability")
    # One vocabulary for every dependency, Seizu's own included (AGT-042).
    assert "mcp__github__get_file_contents" in reachability.allowed_tools
    assert "mcp__deps__depsdev_find_dependency_path" in reachability.allowed_tools
    assert "mcp__seizu__sandbox__delegate" in reachability.allowed_tools

    example_line = next(
        line
        for line in (root / ".env.example").read_text().splitlines()
        if line.startswith("# MCP_EXTERNAL_PROXIES=") and '"upstream_urls"' in line
    )
    proxies = parse_external_mcp_proxies(example_line.removeprefix("# MCP_EXTERNAL_PROXIES="))
    available = {
        "sandbox__delegate",
        "github_security__repo_dependencies",
        "ext__github__list_branches",
        "ext__github__get_file_contents",
        "ext__github__search_code",
        "ext__github__list_commits",
        "ext__github__get_commit",
        "ext__deps__depsdev_find_dependency_path",
        "ext__deps__depsdev_get_requirements",
    }
    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_PROXIES", proxies)

    resolved, missing = mcp_runtime._resolve_plugin_allowed_tools(reachability, available)  # noqa: SLF001

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


def test_cve_severity_skill_declares_the_tools_in_its_workflow() -> None:
    parsed = _seeded_package("cve_response")
    skill = next(item for item in parsed.skills if item.skill_id == "cve_severity_analysis")

    # One vocabulary for every dependency, Seizu's own included (AGT-042).
    assert skill.allowed_tools == [
        "mcp__seizu__cve_analysis__get_recent_cves",
        "mcp__seizu__cve_analysis__count_cves_by_severity",
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


def test_the_seed_ships_skills_as_packages_rather_than_legacy_skillsets() -> None:
    """Legacy skillsets are a compatibility surface, not the shape to seed in.

    A skillset is projected into a package before it can be rendered, and the
    projection cannot express an `mcp.json`, so an external dependency in one is
    unreachable. Authoring the package directly is what the plugin surface is
    for.
    """
    _config_path, config = _seed_config()

    assert config.get("skillsets") is None
    assert set(config["plugins"]) == {
        "cve_response",
        "elicitation_testing",
        "github_security_investigations",
        "portable_security_review",
        "report_authoring",
        "skill_authoring",
    }


def test_every_seeded_package_declares_seizu_tools_that_exist() -> None:
    """A dependency Seizu recognizes must name a real tool, or the skill is unavailable.

    Resolution is by exact name against the caller's inventory (AGT-036), so a
    typo or a renamed tool takes the whole skill out of every user's list with
    no other symptom. The seed's own toolsets are part of that inventory.
    """
    _config_path, config = _seed_config()
    # The whole registry, not what this environment enables: a package is
    # portable, so a dependency on a tool some deployments switch off is a
    # configuration fact rather than a typo. CI runs with the sandbox off.
    available = set(registered_tool_names())
    available |= {
        f"{toolset_id}__{tool_id}"
        for toolset_id, toolset in config["toolsets"].items()
        for tool_id in toolset.get("tools", {})
    }

    unknown: list[str] = []
    for plugin_id in config["plugins"]:
        for skill in _seeded_package(plugin_id).skills:
            for entry in skill.allowed_tools:
                ref = mcp_tool_ref(entry)
                assert ref is not None, f"{plugin_id}/{skill.skill_id}: {entry!r} names no MCP server"
                server_name, tool_name = ref
                if server_name == SEIZU_MCP_SERVER_NAME and tool_name not in available:
                    unknown.append(f"{plugin_id}/{skill.skill_id}: {entry}")

    assert unknown == []


def test_every_seeded_package_declares_the_mcp_servers_it_depends_on() -> None:
    """An external dependency resolves only through the package's own mcp.json."""
    _config_path, config = _seed_config()

    for plugin_id in config["plugins"]:
        for skill in _seeded_package(plugin_id).skills:
            for entry in skill.allowed_tools:
                server_name, _tool = mcp_tool_ref(entry)
                if server_name == SEIZU_MCP_SERVER_NAME:
                    continue
                assert server_name in skill.mcp_servers, f"{plugin_id}/{skill.skill_id}: {entry}"


def test_the_cve_workflow_targets_a_seeded_package_skill() -> None:
    """`cve_repo_report` renders one named skill; the seed has to keep supplying it."""
    parsed = _seeded_package(activities._CVE_SKILLSET_ID)  # noqa: SLF001

    assert activities._CVE_SKILL_ID in {skill.skill_id for skill in parsed.skills}  # noqa: SLF001


def test_report_deletion_is_discoverable_from_the_skill_listing() -> None:
    """A capability only in a skill's body is a capability the model will not find.

    Under progressive disclosure the agent chooses from descriptions and
    triggers, not bodies. `reports__delete` lived only inside the create/update
    skill's clone-cleanup step, so "delete this report" was answered with "I
    can't delete reports" — the skill was never loaded, and no approval was ever
    offered.
    """
    parsed = _seeded_package("report_authoring")
    deleting = [
        skill
        for skill in parsed.skills
        if "delete" in skill.description.lower() or any("delete" in item.lower() for item in skill.triggers)
    ]

    assert deleting, "no report skill advertises deletion in its description or triggers"
    assert any("mcp__seizu__reports__delete" in skill.allowed_tools for skill in deleting)
