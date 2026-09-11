"""Every writer of `allowed-tools` must emit what the resolver reads back.

A package names each dependency `mcp__<server>__<tool>`, Seizu's own included
(AGT-042), and an entry in any other shape is the consuming client's built-in:
portable metadata the resolver deliberately leaves alone. That makes a
declaration written in Seizu's *internal* vocabulary fail in the quietest way
available -- it resolves to nothing, reports nothing missing, and the skill
renders with no tools disclosed.

These tests hold the writers and the resolver together. They are driven off the
built-in registry rather than a fixed list, so a tool added later is covered
without anyone remembering to come back here.
"""

import pytest

from reporting.routes import skillsets as skillsets_routes
from reporting.schema.mcp_config import SkillItem, SkillsetListItem
from reporting.schema.plugins import PluginFile
from reporting.services import external_mcp, mcp_runtime, plugin_packages
from reporting.services.mcp_builtins import registered_tool_names
from reporting.services.plugin_packages import (
    allowed_tool_entries,
    allowed_tool_entry,
    legacy_skillset_package,
    parse_package,
)

_NOW = "2026-01-01T00:00:00+00:00"


def _skillset(skillset_id: str = "skill_authoring") -> SkillsetListItem:
    return SkillsetListItem(
        skillset_id=skillset_id,
        name="Skill authoring",
        description="Author skills",
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="u1",
    )


def _skill(tools_required: list[str], skillset_id: str = "skill_authoring") -> SkillItem:
    return SkillItem(
        skill_id="authoring_workflow",
        skillset_id=skillset_id,
        name="Authoring workflow",
        description="Create or update a skill",
        template="Do the work.",
        tools_required=tools_required,
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="u1",
    )


def _builtin_tool_names() -> list[str]:
    """Every registered tool name, so coverage does not shrink with the config.

    `list_builtin_tools` drops a tool whose feature is off, which would quietly
    leave the sandbox group out of this contract wherever it is disabled.
    """
    return sorted(registered_tool_names())


# ---------------------------------------------------------------------------
# The converter itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_ref", "expected"),
    [
        ("graph__query", "mcp__seizu__graph__query"),
        ("skillsets__create_skill", "mcp__seizu__skillsets__create_skill"),
        ("cve_analysis__get_recent_cves", "mcp__seizu__cve_analysis__get_recent_cves"),
        # Already a package declaration: applying the conversion twice is safe,
        # because a legacy record can hold either spelling after an edit.
        ("mcp__seizu__graph__query", "mcp__seizu__graph__query"),
        ("mcp__github__search_code", "mcp__github__search_code"),
        # Seizu's internal name for an external tool. It needs an mcp.json
        # server entry a legacy skillset cannot carry, so it is left alone
        # rather than promoted into a dependency that cannot resolve.
        ("ext__github__search_code", "ext__github__search_code"),
        # The consumer's own built-ins, which are never ours to rewrite.
        ("Read", "Read"),
        ("Bash(git:*)", "Bash(git:*)"),
        ("WebSearch", "WebSearch"),
    ],
)
def test_allowed_tool_entry_qualifies_only_seizu_tool_names(tool_ref: str, expected: str) -> None:
    assert allowed_tool_entry(tool_ref) == expected


def test_every_builtin_tool_name_qualifies_and_parses_back() -> None:
    """The converter and `mcp_tool_ref` agree across the whole registry."""
    for name in _builtin_tool_names():
        entry = allowed_tool_entry(name)
        assert entry == f"{plugin_packages.SEIZU_TOOL_PREFIX}{name}"
        assert plugin_packages.mcp_tool_ref(entry) == (plugin_packages.SEIZU_MCP_SERVER_NAME, name)


def test_external_namespace_prefix_tracks_external_mcp() -> None:
    """`plugin_packages` copies the prefix to avoid an import cycle."""
    assert plugin_packages._EXTERNAL_NAMESPACE_PREFIX == external_mcp.NAMESPACE_PREFIX  # noqa: SLF001


# ---------------------------------------------------------------------------
# Writer -> reader, for every built-in tool there is
# ---------------------------------------------------------------------------


def test_legacy_projection_declares_every_builtin_so_it_resolves() -> None:
    """The startup projection's output is readable by the resolver.

    A skillset predating Agent Plugins is serialized into a package at startup.
    Writing its `tools_required` bare made every dependency vanish on read, so
    a skill rendered with an empty `tools_required` and the agent was left
    holding no tools it had been told to call.
    """
    names = _builtin_tool_names()
    parsed = legacy_skillset_package(_skillset(), [_skill(names)])

    assert parsed.valid
    skill = parsed.skills[0]
    assert skill.allowed_tools == [f"{plugin_packages.SEIZU_TOOL_PREFIX}{name}" for name in names]

    resolved, missing = mcp_runtime._resolve_plugin_allowed_tools(skill, set(names))  # noqa: SLF001

    assert resolved == names
    assert missing == []


def test_legacy_route_edit_declares_tools_so_they_resolve() -> None:
    """The legacy skill routes write the same package as the projection.

    These are the routes the `skillsets__*` built-ins call, so a skill that
    edits skills would otherwise keep re-writing the broken declaration after
    the projection was fixed.
    """
    names = _builtin_tool_names()
    markdown = skillsets_routes._legacy_skill_markdown(  # noqa: SLF001
        "authoring-workflow",
        "Create or update a skill",
        "Do the work.",
        names,
    )
    parsed = parse_package(
        [
            PluginFile(
                path="plugin.json",
                content=(
                    b'{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", '
                    b'"name": "skill-authoring", "version": "0.0.1", "description": "Author skills", '
                    b'"extensions": {"com.mappedsky.seizu": {"legacySkillsetProjection": true, "skills": {}}}}'
                ),
                media_type="application/json",
            ),
            PluginFile(
                path="skills/authoring-workflow/SKILL.md",
                content=markdown,
                media_type="text/markdown",
            ),
        ]
    )

    assert parsed.valid
    resolved, missing = mcp_runtime._resolve_plugin_allowed_tools(parsed.skills[0], set(names))  # noqa: SLF001

    assert resolved == names
    assert missing == []


def test_a_declared_tool_the_caller_cannot_reach_is_reported_missing() -> None:
    """The resolution is real: it gates on the caller's inventory (AGT-036)."""
    parsed = legacy_skillset_package(_skillset(), [_skill(["graph__query", "skillsets__create_skill"])])

    resolved, missing = mcp_runtime._resolve_plugin_allowed_tools(  # noqa: SLF001
        parsed.skills[0],
        {"graph__query"},
    )

    assert resolved == ["graph__query"]
    assert missing == ["mcp__seizu__skillsets__create_skill"]


def test_projection_leaves_an_external_reference_in_its_legacy_spelling() -> None:
    """A legacy skillset has nowhere to carry the mcp.json a package form needs.

    `SkillItem.tools_required` admits only Seizu's own vocabulary, so this is
    the whole set of shapes a projection can be handed.
    """
    parsed = legacy_skillset_package(
        _skillset(),
        [_skill(["graph__query", "ext__github__search_code"])],
    )

    assert parsed.skills[0].allowed_tools == [
        "mcp__seizu__graph__query",
        "ext__github__search_code",
    ]


def test_allowed_tool_entries_maps_the_whole_list() -> None:
    assert allowed_tool_entries(["graph__query", "ext__x__y"]) == ["mcp__seizu__graph__query", "ext__x__y"]


# ---------------------------------------------------------------------------
# Parse-time diagnostics
# ---------------------------------------------------------------------------


def _package_with_allowed_tools(allowed: str, *, mcp_json: bytes | None = None) -> object:
    files = [
        PluginFile(
            path="plugin.json",
            content=(
                b'{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", '
                b'"name": "demo", "version": "0.0.1", "description": "Demo package", '
                b'"extensions": {"com.mappedsky.seizu": {"skills": {}}}}'
            ),
            media_type="application/json",
        ),
        PluginFile(
            path="skills/review/SKILL.md",
            content=(f"---\nname: review\ndescription: Review it\nallowed-tools: {allowed}\n---\nReview it.").encode(),
            media_type="text/markdown",
        ),
    ]
    if mcp_json is not None:
        files.append(PluginFile(path="mcp.json", content=mcp_json, media_type="application/json"))
    return parse_package(files)


def _codes(parsed) -> list[str]:
    return [item.code for item in parsed.diagnostics]


def test_a_bare_seizu_tool_name_is_reported_rather_than_silently_dropped() -> None:
    parsed = _package_with_allowed_tools("graph__query")

    assert parsed.valid  # a wrong declaration is not a broken package
    assert _codes(parsed) == ["unqualified_tool"]
    assert "mcp__seizu__graph__query" in parsed.diagnostics[0].message


def test_an_internal_external_tool_name_is_reported() -> None:
    parsed = _package_with_allowed_tools("ext__github__search_code")

    assert _codes(parsed) == ["unqualified_tool"]
    assert "mcp.json" in parsed.diagnostics[0].message


def test_a_dependency_on_an_undeclared_mcp_server_is_reported() -> None:
    parsed = _package_with_allowed_tools("mcp__github__search_code")

    assert _codes(parsed) == ["undeclared_mcp_server"]


def test_a_declared_mcp_server_is_not_reported() -> None:
    parsed = _package_with_allowed_tools(
        "mcp__github__search_code",
        mcp_json=(
            b'{"$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json", '
            b'"mcpServers": {"github": {"type": "streamable-http", "url": "https://github.example/mcp"}}}'
        ),
    )

    assert _codes(parsed) == []


@pytest.mark.parametrize("allowed", ["mcp__seizu__graph__query", "Read", "WebSearch", "Bash(git:*)"])
def test_correct_and_portable_declarations_are_not_reported(allowed: str) -> None:
    assert _codes(_package_with_allowed_tools(allowed)) == []


def test_the_projections_own_output_parses_without_a_declaration_warning() -> None:
    """The writers and the parse-time check cannot disagree about what is wrong."""
    parsed = legacy_skillset_package(_skillset(), [_skill(_builtin_tool_names())])

    assert [item.code for item in parsed.diagnostics if item.code == "unqualified_tool"] == []
