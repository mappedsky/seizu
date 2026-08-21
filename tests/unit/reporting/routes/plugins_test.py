import io
import json
import zipfile
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.plugins import PluginListItem
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
