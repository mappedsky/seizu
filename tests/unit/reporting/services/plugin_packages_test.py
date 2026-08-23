import io
import json
import zipfile

import pytest

from reporting.schema.mcp_config import SkillItem, SkillsetListItem
from reporting.schema.plugins import PluginFile
from reporting.services.plugin_packages import (
    MCP_SCHEMA,
    PLUGIN_SCHEMA,
    files_from_zip,
    is_legacy_skillset_projection,
    legacy_skillset_package,
    parse_package,
)

_NOW = "2026-01-01T00:00:00+00:00"


def _files(extra: dict[str, bytes] | None = None):
    manifest = {
        "$schema": PLUGIN_SCHEMA,
        "name": "security-tools",
        "extensions": {
            "com.mappedsky.seizu": {
                "skillsetId": "security_tools",
                "skills": {
                    "review-repository": {
                        "skillId": "review_repository",
                        "parameters": [{"name": "repository", "type": "string"}],
                    }
                },
            }
        },
    }
    values = {
        "plugin.json": json.dumps(manifest).encode(),
        "skills/review-repository/SKILL.md": (
            b"---\nname: review-repository\ndescription: Review a repository\n"
            b"allowed-tools: graph__query mcp:github/get_file Read\n---\n"
            b"Review {% $repository %}."
        ),
        "mcp.json": json.dumps(
            {
                "$schema": MCP_SCHEMA,
                "mcpServers": {"github": {"type": "streamable-http", "url": "https://github.example/mcp"}},
            }
        ).encode(),
    }
    values.update(extra or {})
    from reporting.schema.plugins import PluginFile

    return [PluginFile(path=path, content=content) for path, content in values.items()]


def test_parses_namespaced_skill_allowed_tools_and_mcp_dependencies():
    parsed = parse_package(_files({"skills/review-repository/scripts/check.sh": b"#!/bin/sh\n"}))
    assert parsed.valid
    assert parsed.plugin_id == "security_tools"
    assert len(parsed.skills) == 1
    skill = parsed.skills[0]
    assert skill.skill_id == "review_repository"
    assert skill.allowed_tools == ["graph__query", "mcp:github/get_file", "Read"]
    assert skill.has_scripts is True
    assert skill.mcp_servers["github"]["url"] == "https://github.example/mcp"


def test_invalid_skill_is_skipped_without_rejecting_plugin():
    files = _files()
    files[1] = files[1].model_copy(update={"content": b"missing frontmatter"})
    parsed = parse_package(files)
    assert parsed.valid
    assert parsed.skills == []
    assert any(item.code == "invalid_skill" for item in parsed.diagnostics)


def test_nested_skill_is_not_discovered_in_1_0_0():
    parsed = parse_package(
        _files(
            {
                "skills/group/nested/SKILL.md": b"---\nname: nested\ndescription: Nested\n---\nignored",
            }
        )
    )
    assert [skill.portable_name for skill in parsed.skills] == ["review-repository"]


def test_direct_file_packages_apply_unpacked_size_limit(mocker):
    mocker.patch("reporting.services.plugin_packages.MAX_UNPACKED_BYTES", 10)

    parsed = parse_package(_files())

    assert not parsed.valid
    assert parsed.diagnostics[0].code == "package_too_large"


def test_zip_rejects_path_traversal():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("../plugin.json", "{}")
    with pytest.raises(ValueError, match="unsafe package path"):
        files_from_zip(output.getvalue())


def test_legacy_projection_preserves_namespaced_identity_and_tools():
    skillset = SkillsetListItem(
        skillset_id="incident_response",
        name="Incident response",
        current_version=2,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="user",
    )
    skill = SkillItem(
        skill_id="review_alert",
        skillset_id="incident_response",
        name="Review alert",
        description="Review one alert",
        template="Review the alert.",
        tools_required=["graph__query"],
        current_version=3,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="user",
    )
    parsed = legacy_skillset_package(skillset, [skill])
    assert parsed.valid
    assert parsed.plugin_id == "incident_response"
    assert is_legacy_skillset_projection(parsed.manifest)
    assert parsed.skills[0].skill_id == "review_alert"
    assert parsed.skills[0].allowed_tools == ["graph__query"]


def _package(body: str, *, projection: bool = False) -> object:
    extension: dict = {
        "skillsetId": "review_tools",
        "skills": {"review": {"parameters": [{"name": "repo", "type": "string", "required": True}]}},
    }
    if projection:
        extension["legacySkillsetProjection"] = True
    manifest = {
        "$schema": PLUGIN_SCHEMA,
        "name": "review-tools",
        "extensions": {"com.mappedsky.seizu": extension},
    }
    return parse_package(
        [
            PluginFile(path="plugin.json", content=json.dumps(manifest).encode()),
            PluginFile(
                path="skills/review/SKILL.md",
                content=f"---\nname: review\ndescription: Review a target\n---\n{body}".encode(),
            ),
        ]
    )


def test_a_templated_body_is_published_with_an_advisory():
    """AGT-039: it renders, but a consumer without the extension reads the tags."""
    parsed = _package("Review {% $repo %}.")

    assert parsed.valid
    codes = [item.code for item in parsed.diagnostics]
    assert "templated_skill_body" in codes


def test_a_static_body_publishes_without_the_advisory():
    parsed = _package("Review the `repo` input.")

    assert parsed.valid
    assert "templated_skill_body" not in [item.code for item in parsed.diagnostics]


def test_the_legacy_projection_is_not_advised_to_restructure():
    """Its bodies are generated from records the author cannot restructure."""
    parsed = _package("Review {% $repo %}.", projection=True)

    assert "templated_skill_body" not in [item.code for item in parsed.diagnostics]


def _named_package(name: str, *, skillset_id: str | None = None, skill_id: str | None = None) -> object:
    extension: dict = {}
    if skillset_id is not None:
        extension["skillsetId"] = skillset_id
    if skill_id is not None:
        extension["skills"] = {"review": {"skillId": skill_id}}
    manifest: dict = {"$schema": PLUGIN_SCHEMA, "name": name}
    if extension:
        manifest["extensions"] = {"com.mappedsky.seizu": extension}
    return parse_package(
        [
            PluginFile(path="plugin.json", content=json.dumps(manifest).encode()),
            PluginFile(
                path="skills/review/SKILL.md",
                content=b"---\nname: review\ndescription: Review a target\n---\nReview it.",
            ),
        ]
    )


def test_ids_derive_from_names_with_no_seizu_extension():
    """AGT-040: a stock Agent Plugin installs unmodified."""
    parsed = _named_package("review-tools")

    assert parsed.valid
    assert parsed.plugin_id == "review_tools"
    assert [skill.skill_id for skill in parsed.skills] == ["review"]


def test_a_skillset_id_that_repeats_the_derived_one_is_redundant():
    parsed = _named_package("review-tools", skillset_id="review_tools")

    assert parsed.valid
    assert "redundant_skillset_id" in [item.code for item in parsed.diagnostics]


def test_a_skillset_id_that_names_something_else_is_refused():
    """Two identities for one package is the thing being removed."""
    parsed = _named_package("review-tools", skillset_id="other_tools")

    assert not parsed.valid
    assert "conflicting_skillset_id" in [item.code for item in parsed.diagnostics]


def test_a_divergent_skill_id_is_refused():
    parsed = _named_package("review-tools", skill_id="something_else")

    assert "conflicting_skill_id" in [item.code for item in parsed.diagnostics]
    assert parsed.skills == []


def test_a_package_name_that_derives_no_id_is_refused():
    parsed = _named_package("2fa-tools")

    assert not parsed.valid
    assert "underivable_plugin_id" in [item.code for item in parsed.diagnostics]
