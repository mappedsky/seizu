import base64
import io
import json
import zipfile
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.plugins import PluginFile, PluginListItem
from reporting.schema.report_config import User
from reporting.services.plugin_packages import PLUGIN_SCHEMA

_NOW = "2026-01-01T00:00:00+00:00"
_USER = User(
    user_id="user",
    sub="subject",
    iss="issuer",
    created_at=_NOW,
    last_login=_NOW,
)
_CURRENT = CurrentUser(user=_USER, jwt_claims={}, permissions=ALL_PERMISSIONS)


def _archive() -> bytes:
    manifest = {
        "$schema": PLUGIN_SCHEMA,
        "name": "review-tools",
        "extensions": {"com.mappedsky.seizu": {"skillsetId": "review_tools"}},
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("plugin.json", json.dumps(manifest))
        archive.writestr(
            "skills/review/SKILL.md",
            "---\nname: review\ndescription: Review a target\n---\nReview it.",
        )
    return output.getvalue()


def _app():
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _CURRENT
    return app


async def test_validate_plugin_zip_without_installing():
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/plugins/validate",
            files={"package": ("plugin.zip", _archive(), "application/zip")},
        )
    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["plugin_id"] == "review_tools"


async def test_install_publishes_validated_package(mocker):
    item = PluginListItem(
        plugin_id="review_tools",
        name="review-tools",
        current_revision=1,
        package_digest="a" * 64,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="user",
    )
    mocker.patch("reporting.routes.plugins.report_store.get_plugin", new=AsyncMock(return_value=None))
    publish = mocker.patch("reporting.routes.plugins.report_store.publish_plugin", new=AsyncMock(return_value=item))
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/plugins/install",
            files={"package": ("plugin.zip", _archive(), "application/zip")},
        )
    assert response.status_code == 201
    assert response.json()["plugin_id"] == "review_tools"
    assert publish.await_args.kwargs["created_by"] == "user"


async def test_create_plugin_publishes_minimal_portable_package(mocker):
    item = PluginListItem(
        plugin_id="review_tools",
        name="review-tools",
        package_version="1.0.0",
        current_revision=1,
        package_digest="a" * 64,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="user",
    )
    mocker.patch("reporting.routes.plugins.report_store.get_plugin", new=AsyncMock(return_value=None))
    publish = mocker.patch("reporting.routes.plugins.report_store.publish_plugin", new=AsyncMock(return_value=item))

    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/plugins",
            json={
                "plugin_id": "review_tools",
                "name": "review-tools",
                "version": "1.0.0",
                "description": "Review security findings",
            },
        )

    assert response.status_code == 201
    files = publish.await_args.kwargs["files"]
    manifest = json.loads(files[0].content)
    assert manifest["$schema"] == PLUGIN_SCHEMA
    assert manifest["extensions"]["com.mappedsky.seizu"]["skillsetId"] == "review_tools"
    assert manifest["extensions"]["com.mappedsky.seizu"]["skills"] == {}


async def test_create_plugin_rejects_existing_id(mocker):
    mocker.patch(
        "reporting.routes.plugins.report_store.get_plugin",
        new=AsyncMock(return_value=object()),
    )
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/plugins",
            json={"plugin_id": "review_tools", "name": "review-tools"},
        )
    assert response.status_code == 409


def _staged_files() -> list[dict]:
    manifest = {
        "$schema": PLUGIN_SCHEMA,
        "name": "review-tools",
        "extensions": {"com.mappedsky.seizu": {"skillsetId": "review_tools"}},
    }
    skill = "---\nname: review\ndescription: Review a target\n---\nReview it."
    return [
        {
            "path": "plugin.json",
            "content_base64": base64.b64encode(json.dumps(manifest).encode()).decode(),
            "media_type": "application/json",
        },
        {
            "path": "skills/review/SKILL.md",
            "content_base64": base64.b64encode(skill.encode()).decode(),
            "media_type": "text/markdown",
        },
    ]


def _installed(revision: int = 2) -> PluginListItem:
    return PluginListItem(
        plugin_id="review_tools",
        name="review-tools",
        current_revision=revision,
        package_digest="a" * 64,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="user",
    )


async def test_publish_stages_a_whole_package_in_one_request(mocker):
    mocker.patch("reporting.routes.plugins.report_store.get_plugin", new=AsyncMock(return_value=_installed()))
    publish = mocker.patch(
        "reporting.routes.plugins.report_store.publish_plugin", new=AsyncMock(return_value=_installed(3))
    )
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/plugins/review_tools/publish",
            json={"files": _staged_files(), "base_revision": 2, "comment": "Edited"},
        )
    assert response.status_code == 200
    assert publish.await_args.kwargs["expected_revision"] == 2
    assert {item.path for item in publish.await_args.kwargs["files"]} == {
        "plugin.json",
        "skills/review/SKILL.md",
    }


async def test_publish_refuses_a_stale_base_revision(mocker):
    mocker.patch("reporting.routes.plugins.report_store.get_plugin", new=AsyncMock(return_value=_installed(5)))
    publish = mocker.patch("reporting.routes.plugins.report_store.publish_plugin", new=AsyncMock())
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/plugins/review_tools/publish",
            json={"files": _staged_files(), "base_revision": 2},
        )
    assert response.status_code == 409
    publish.assert_not_awaited()


async def test_publish_requires_a_base_revision(mocker):
    mocker.patch("reporting.routes.plugins.report_store.get_plugin", new=AsyncMock(return_value=_installed()))
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/plugins/review_tools/publish",
            json={"files": _staged_files()},
        )
    assert response.status_code == 422


async def test_publish_retains_untouched_content_by_digest(mocker):
    retained = PluginFile(path="skills/review/assets/logo.png", content=b"binary", media_type="image/png")
    mocker.patch("reporting.routes.plugins.report_store.get_plugin", new=AsyncMock(return_value=_installed()))
    mocker.patch("reporting.routes.plugins.report_store.read_plugin_blob", new=AsyncMock(return_value=retained))
    publish = mocker.patch(
        "reporting.routes.plugins.report_store.publish_plugin", new=AsyncMock(return_value=_installed(3))
    )
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/plugins/review_tools/publish",
            json={
                "files": [
                    *_staged_files(),
                    {"path": "skills/review/assets/logo.png", "sha256": "b" * 64},
                ],
                "base_revision": 2,
            },
        )
    assert response.status_code == 200
    asset = next(item for item in publish.await_args.kwargs["files"] if item.path.endswith("logo.png"))
    assert asset.content == b"binary"


async def test_publish_rejects_content_the_plugin_does_not_store(mocker):
    mocker.patch("reporting.routes.plugins.report_store.get_plugin", new=AsyncMock(return_value=_installed()))
    mocker.patch("reporting.routes.plugins.report_store.read_plugin_blob", new=AsyncMock(return_value=None))
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/plugins/review_tools/publish",
            json={
                "files": [*_staged_files(), {"path": "notes.txt", "sha256": "b" * 64}],
                "base_revision": 2,
            },
        )
    assert response.status_code == 400


async def test_publish_rejects_an_escaping_path(mocker):
    mocker.patch("reporting.routes.plugins.report_store.get_plugin", new=AsyncMock(return_value=_installed()))
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/plugins/review_tools/publish",
            json={
                "files": [
                    *_staged_files(),
                    {"path": "../escape.txt", "content_base64": base64.b64encode(b"x").decode()},
                ],
                "base_revision": 2,
            },
        )
    assert response.status_code == 400


async def test_validate_package_reports_diagnostics_without_publishing(mocker):
    mocker.patch("reporting.routes.plugins.report_store.get_plugin", new=AsyncMock(return_value=_installed()))
    publish = mocker.patch("reporting.routes.plugins.report_store.publish_plugin", new=AsyncMock())
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/plugins/review_tools/validate",
            json={"files": _staged_files()},
        )
    assert response.status_code == 200
    assert response.json()["valid"] is True
    publish.assert_not_awaited()


async def test_version_skills_are_parsed_from_that_revisions_files(mocker):
    """plugin_skills indexes only the current revision, so an older one is re-parsed."""
    manifest = {
        "$schema": PLUGIN_SCHEMA,
        "name": "review-tools",
        "extensions": {"com.mappedsky.seizu": {"skillsetId": "review_tools"}},
    }
    files = [
        PluginFile(path="plugin.json", content=json.dumps(manifest).encode()),
        PluginFile(
            path="skills/review/SKILL.md",
            content=b"---\nname: review\ndescription: Review a target\n---\nReview it.",
        ),
        PluginFile(path="skills/review/scripts/scan.sh", content=b"#!/bin/sh\n", executable=True),
    ]
    mocker.patch("reporting.routes.plugins.report_store.read_plugin_files", new=AsyncMock(return_value=files))
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.get("/api/v1/plugins/review_tools/versions/1/skills")

    assert response.status_code == 200
    skill = response.json()["skills"][0]
    assert skill["skill_id"] == "review"
    assert skill["source_path"] == "skills/review"
    assert skill["has_scripts"] is True


async def test_version_skills_404_for_a_revision_that_has_no_files(mocker):
    mocker.patch("reporting.routes.plugins.report_store.read_plugin_files", new=AsyncMock(return_value=[]))
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.get("/api/v1/plugins/review_tools/versions/9/skills")
    assert response.status_code == 404


async def test_current_skills_come_from_the_index(mocker):
    from reporting.schema.plugins import PluginSkillItem

    indexed = PluginSkillItem(
        plugin_id="review_tools",
        skill_id="review",
        portable_name="review",
        title="Review",
        description="Review a target",
        template="Review it.",
        source_path="skills/review",
        revision=2,
    )
    mocker.patch("reporting.routes.plugins.report_store.get_plugin", new=AsyncMock(return_value=_installed()))
    mocker.patch("reporting.routes.plugins.report_store.list_plugin_skills", new=AsyncMock(return_value=[indexed]))
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.get("/api/v1/plugins/review_tools/skills")
    assert response.status_code == 200
    assert [item["skill_id"] for item in response.json()["skills"]] == ["review"]


async def test_publish_warns_when_contents_change_but_version_does_not(mocker):
    """Revision and digest are identity here; the manifest version is what travels."""
    existing = _installed()
    existing.package_version = "1.0.0"
    mocker.patch("reporting.routes.plugins.report_store.get_plugin", new=AsyncMock(return_value=existing))
    publish = mocker.patch(
        "reporting.routes.plugins.report_store.publish_plugin", new=AsyncMock(return_value=_installed(3))
    )
    manifest = {
        "$schema": PLUGIN_SCHEMA,
        "name": "review-tools",
        "version": "1.0.0",
        "extensions": {"com.mappedsky.seizu": {"skillsetId": "review_tools"}},
    }
    files = [
        {
            "path": "plugin.json",
            "content_base64": base64.b64encode(json.dumps(manifest).encode()).decode(),
        }
    ]
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/plugins/review_tools/publish",
            json={"files": files, "base_revision": 2},
        )

    assert response.status_code == 200
    codes = [item["code"] for item in publish.await_args.kwargs["diagnostics"]]
    assert "unchanged_package_version" in codes


async def test_publish_does_not_warn_when_the_version_moved(mocker):
    existing = _installed()
    existing.package_version = "1.0.0"
    mocker.patch("reporting.routes.plugins.report_store.get_plugin", new=AsyncMock(return_value=existing))
    publish = mocker.patch(
        "reporting.routes.plugins.report_store.publish_plugin", new=AsyncMock(return_value=_installed(3))
    )
    manifest = {
        "$schema": PLUGIN_SCHEMA,
        "name": "review-tools",
        "version": "1.1.0",
        "extensions": {"com.mappedsky.seizu": {"skillsetId": "review_tools"}},
    }
    files = [
        {
            "path": "plugin.json",
            "content_base64": base64.b64encode(json.dumps(manifest).encode()).decode(),
        }
    ]
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/plugins/review_tools/publish",
            json={"files": files, "base_revision": 2},
        )

    assert response.status_code == 200
    codes = [item["code"] for item in publish.await_args.kwargs["diagnostics"]]
    assert "unchanged_package_version" not in codes
