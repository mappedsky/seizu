from httpx import ASGITransport, AsyncClient

from reporting.app import create_app


def _make_app():
    return create_app()


async def test_config(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/config")
    assert ret.status_code == 200
    ret_json = ret.json()
    assert "auth_required" in ret_json
    assert ret_json["config"] == {}
    assert "reports" not in ret_json["config"].keys()
    assert "dashboard" not in ret_json["config"].keys()
    assert "schema" not in ret_json


async def test_config_features_chat_disabled_by_default(mocker):
    mocker.patch("reporting.settings.CHAT_ENABLED", False)
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/config")
    assert ret.json()["features"]["chat"] is False


async def test_config_serves_dependent_action_schemas(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/config")
    dependent = ret.json()["scheduled_query_action_dependent_schemas"]
    assert dependent["temporal"]["discriminator"] == "workflow"
    cartography_fields = {f["name"] for f in dependent["temporal"]["schemas"]["cartography_sync"]}
    assert {"modules", "pipeline"} <= cartography_fields
    # Workflows without their own config fields have no sub-schema.
    assert "cve_repo_report" not in dependent["temporal"]["schemas"]
    config = ret.json()
    assert "workflow" in config["workflow_activity_types"]
    assert "workflow" in config["workflow_activity_schemas"]
    workflow_variants = config["workflow_activity_definitions"]["workflow"]["variants"]
    assert workflow_variants["cve_repo_report"]["input_required"] is True
    assert workflow_variants["cartography_sync"]["input_required"] is False
    assert workflow_variants["cartography_sync"]["output_schema"]["type"] == "object"
    assert "workflow" not in config["scheduled_query_action_types"]


async def test_config_features_reflect_chat_enabled(mocker):
    mocker.patch("reporting.settings.CHAT_ENABLED", True)
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/config")
    assert ret.json()["features"]["chat"] is True


async def test_config_auth_required_true(mocker):
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/config")
    assert ret.json()["auth_required"] is True


async def test_config_auth_required_false(mocker):
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", False)
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/config")
    assert ret.json()["auth_required"] is False


async def test_config_oidc_included_when_auth_required(mocker):
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    mocker.patch("reporting.settings.OIDC_AUTHORITY", "https://idp.example.com/o/app")
    mocker.patch("reporting.settings.OIDC_CLIENT_ID", "myapp")
    mocker.patch(
        "reporting.settings.OIDC_REDIRECT_URI",
        "https://app.example.com/auth/callback",
    )
    mocker.patch("reporting.settings.OIDC_SCOPE", "openid email")
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/config")
    oidc = ret.json()["oidc"]
    assert oidc["authority"] == "https://idp.example.com/o/app"
    assert oidc["client_id"] == "myapp"
    assert oidc["redirect_uri"] == "https://app.example.com/auth/callback"
    assert oidc["scope"] == "openid email"


async def test_config_oidc_included_when_auth_not_required(mocker):
    """oidc config is returned regardless of auth_required so the frontend
    can self-configure even in no-auth dev mode."""
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", False)
    mocker.patch("reporting.settings.OIDC_AUTHORITY", "https://idp.example.com/o/app")
    mocker.patch("reporting.settings.OIDC_CLIENT_ID", "myapp")
    mocker.patch(
        "reporting.settings.OIDC_REDIRECT_URI",
        "https://app.example.com/auth/callback",
    )
    mocker.patch("reporting.settings.OIDC_SCOPE", "openid email")
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/config")
    assert ret.json()["oidc"]["authority"] == "https://idp.example.com/o/app"


async def test_config_oidc_null_when_authority_not_configured(mocker):
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    mocker.patch("reporting.settings.OIDC_AUTHORITY", "")
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/config")
    assert ret.json()["oidc"] is None


# ---------------------------------------------------------------------------
# _build_csp_policy
# ---------------------------------------------------------------------------


def test_csp_policy_no_oidc_authority(mocker):
    from reporting.app import _build_csp_policy

    mocker.patch("reporting.settings.OIDC_AUTHORITY", "")
    policy = _build_csp_policy()
    assert "'self'" in policy
    assert "connect-src" in policy


def test_csp_policy_with_oidc_authority(mocker):
    from reporting.app import _build_csp_policy

    mocker.patch(
        "reporting.settings.OIDC_AUTHORITY",
        "https://idp.example.com/application/o/seizu",
    )
    policy = _build_csp_policy()
    assert "connect-src 'self' https://idp.example.com" in policy
    assert "frame-src https://idp.example.com" in policy


def test_csp_policy_oidc_origin_not_duplicated(mocker):
    """oidc origin appears exactly once in connect-src and once in frame-src."""
    from reporting.app import _build_csp_policy

    mocker.patch("reporting.settings.OIDC_AUTHORITY", "https://idp.example.com/o/app")
    policy = _build_csp_policy()
    assert "connect-src 'self' https://idp.example.com" in policy
    assert "frame-src https://idp.example.com" in policy
    # Appears once per directive (connect-src + frame-src), not duplicated within either.
    assert policy.count("https://idp.example.com") == 2


def test_csp_policy_includes_xss_hardening_directives(mocker):
    """script-src, base-uri, form-action, and frame-ancestors are all set."""
    from reporting.app import _build_csp_policy

    mocker.patch("reporting.settings.OIDC_AUTHORITY", "")
    policy = _build_csp_policy()
    assert "script-src 'self'" in policy
    assert "base-uri 'self'" in policy
    assert "form-action 'self'" in policy
    assert "frame-ancestors 'none'" in policy


def test_csp_policy_allows_inline_style_attributes(mocker):
    """style-src-attr 'unsafe-inline' is required for MUI / dynamic positioning libs."""
    from reporting.app import _build_csp_policy

    mocker.patch("reporting.settings.OIDC_AUTHORITY", "")
    policy = _build_csp_policy()
    assert "style-src-attr 'unsafe-inline'" in policy


def test_csp_policy_allows_data_image_uris(mocker):
    """img-src 'self' data: covers MUI/grid-layout inline SVG assets."""
    from reporting.app import _build_csp_policy

    mocker.patch("reporting.settings.OIDC_AUTHORITY", "")
    policy = _build_csp_policy()
    assert "img-src 'self' data:" in policy


def test_csp_policy_includes_nonce_in_style_src(mocker):
    from reporting.app import _build_csp_policy

    mocker.patch("reporting.settings.OIDC_AUTHORITY", "")
    policy = _build_csp_policy("abc123")
    assert "style-src 'self' 'nonce-abc123'" in policy
