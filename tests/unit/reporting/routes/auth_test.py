import base64
import os
import time
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.services import oauth_client, oauth_state_cookie, session_cookie


@pytest.fixture(autouse=True)
def _auth_env(mocker):
    mocker.patch(
        "reporting.settings.SESSION_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(os.urandom(32)).decode("ascii"),
    )
    mocker.patch("reporting.settings.OIDC_CLIENT_ID", "seizu")
    mocker.patch("reporting.settings.OIDC_SCOPE", "openid email offline_access")
    mocker.patch("reporting.settings.OIDC_REDIRECT_URI", "http://test/api/v1/auth/callback")
    mocker.patch("reporting.settings.TALISMAN_FORCE_HTTPS", False)
    mocker.patch("reporting.settings.OIDC_REVOKE_REFRESH_TOKEN_ON_LOGOUT", True)
    mocker.patch("reporting.settings.SESSION_COOKIE_NAME", "seizu_session")
    mocker.patch("reporting.settings.SESSION_COOKIE_MAX_AGE_SECONDS", 18 * 60 * 60)
    # ID-token validation reaches out to the IDP JWKS; default it to a no-op
    # success here. Tests that exercise the failure path re-patch it.
    mocker.patch(
        "reporting.services.oauth_client.validate_id_token",
        AsyncMock(return_value={"sub": "user-1", "nonce": "nonce-1"}),
    )
    # Discovery is the same kind of call: every route here reaches it eventually,
    # and the logout route reaches it via build_post_logout_url even when the
    # test is about cookies. Left unstubbed it attempts a real fetch against
    # whatever OIDC_AUTHORITY the environment happens to carry, so these tests
    # passed or failed on the developer's .env rather than on the code. Tests
    # asserting on specific endpoints re-patch this and their patch wins.
    mocker.patch("reporting.services.oauth_client.get_metadata", AsyncMock(return_value=_mock_metadata()))
    oauth_client.reset_metadata_cache()
    yield
    oauth_client.reset_metadata_cache()


def _mock_metadata():
    return oauth_client.OIDCMetadata(
        issuer="http://idp.test",
        authorization_endpoint="http://idp.test/authorize",
        token_endpoint="http://idp.test/token",
        end_session_endpoint="http://idp.test/end-session",
        revocation_endpoint="http://idp.test/revoke",
        introspection_endpoint="http://idp.test/introspect",
        jwks_uri="http://idp.test/jwks",
    )


def _mock_token_response(refresh_token: str = "rt-new", id_token: str | None = "id-token-1"):
    return oauth_client.TokenResponse(
        access_token="at-new",
        refresh_token=refresh_token,
        expires_in=300,
        refresh_expires_in=30 * 24 * 60 * 60,
        id_token=id_token,
        token_type="Bearer",
        scope="openid email offline_access",
    )


async def test_login_returns_authorize_url_and_sets_state_cookie(mocker):
    mocker.patch("reporting.services.oauth_client.get_metadata", AsyncMock(return_value=_mock_metadata()))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/login")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authorize_url"].startswith("http://idp.test/authorize?")
    assert "code_challenge=" in body["authorize_url"]
    assert "code_challenge_method=S256" in body["authorize_url"]
    assert "state=" in body["authorize_url"]
    assert "nonce=" in body["authorize_url"]
    assert oauth_state_cookie.STATE_COOKIE_NAME in resp.cookies


async def test_login_unsafe_return_to_falls_back_to_root(mocker):
    mocker.patch("reporting.services.oauth_client.get_metadata", AsyncMock(return_value=_mock_metadata()))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/login", params={"return_to": "http://evil.com/"})
    state_cookie = resp.cookies.get(oauth_state_cookie.STATE_COOKIE_NAME)
    assert state_cookie is not None
    payload = oauth_state_cookie.decrypt(state_cookie)
    assert payload.return_to == "/"


async def test_login_safe_return_to_is_preserved(mocker):
    mocker.patch("reporting.services.oauth_client.get_metadata", AsyncMock(return_value=_mock_metadata()))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/login", params={"return_to": "/reports/123"})
    payload = oauth_state_cookie.decrypt(resp.cookies[oauth_state_cookie.STATE_COOKIE_NAME])
    assert payload.return_to == "/reports/123"


async def test_login_rewrites_internal_authorize_endpoint_to_external(mocker):
    """When discovery returns an internal-host URL, the SPA-facing authorize URL is externalized."""
    mocker.patch("reporting.settings.OIDC_AUTHORITY", "http://localhost:9000/application/o/seizu")
    mocker.patch(
        "reporting.settings.OIDC_INTERNAL_AUTHORITY",
        "http://authentik-server:9000/application/o/seizu",
    )
    internal_metadata = oauth_client.OIDCMetadata(
        issuer="http://authentik-server:9000/application/o/seizu/",
        authorization_endpoint="http://authentik-server:9000/application/o/authorize/",
        token_endpoint="http://authentik-server:9000/application/o/token/",
        end_session_endpoint="http://authentik-server:9000/application/o/end-session/",
        revocation_endpoint="http://authentik-server:9000/application/o/revoke/",
        introspection_endpoint="http://authentik-server:9000/application/o/introspect/",
        jwks_uri="http://authentik-server:9000/application/o/seizu/jwks/",
    )
    mocker.patch("reporting.services.oauth_client.get_metadata", AsyncMock(return_value=internal_metadata))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/login")
    assert resp.status_code == 200
    authorize_url = resp.json()["authorize_url"]
    assert authorize_url.startswith("http://localhost:9000/application/o/authorize/?")
    assert "authentik-server" not in authorize_url


async def test_callback_rejects_missing_state_cookie(mocker):
    mocker.patch("reporting.services.oauth_client.get_metadata", AsyncMock(return_value=_mock_metadata()))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/callback", params={"code": "c", "state": "s"})
    assert resp.status_code == 400


async def test_callback_clears_state_cookie_on_error(mocker):
    mocker.patch("reporting.services.oauth_client.get_metadata", AsyncMock(return_value=_mock_metadata()))
    state_payload = oauth_state_cookie.OAuthStatePayload(
        state="real-state",
        verifier="v",
        return_to="/",
        exp=int(time.time()) + 60,
        nonce="nonce-1",
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(oauth_state_cookie.STATE_COOKIE_NAME, oauth_state_cookie.encrypt(state_payload))
        resp = await client.get("/api/v1/auth/callback", params={"code": "c", "state": "wrong-state"})
    assert resp.status_code == 400
    set_cookie = resp.headers.get("set-cookie", "")
    assert f"{oauth_state_cookie.STATE_COOKIE_NAME}=" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()


async def test_callback_idp_error_redirects_to_fixed_error_url(mocker):
    mocker.patch("reporting.services.oauth_client.get_metadata", AsyncMock(return_value=_mock_metadata()))
    state_payload = oauth_state_cookie.OAuthStatePayload(
        state="real-state",
        verifier="v",
        return_to="/",
        exp=int(time.time()) + 60,
        nonce="nonce-1",
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(oauth_state_cookie.STATE_COOKIE_NAME, oauth_state_cookie.encrypt(state_payload))
        resp = await client.get(
            "/api/v1/auth/callback",
            params={
                "error": "access_denied",
                "error_description": "https://evil.example/phish",
            },
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/?auth_error=1"
    assert "evil.example" not in resp.headers["location"]


async def test_callback_rejects_state_mismatch(mocker):
    mocker.patch("reporting.services.oauth_client.get_metadata", AsyncMock(return_value=_mock_metadata()))
    state_payload = oauth_state_cookie.OAuthStatePayload(
        state="real-state",
        verifier="v",
        return_to="/",
        exp=int(time.time()) + 60,
        nonce="nonce-1",
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(oauth_state_cookie.STATE_COOKIE_NAME, oauth_state_cookie.encrypt(state_payload))
        resp = await client.get("/api/v1/auth/callback", params={"code": "c", "state": "wrong-state"})
    assert resp.status_code == 400


async def test_callback_exchanges_code_sets_session_cookie_and_redirects(mocker):
    mocker.patch("reporting.services.oauth_client.get_metadata", AsyncMock(return_value=_mock_metadata()))
    mocker.patch(
        "reporting.services.oauth_client.exchange_code",
        AsyncMock(return_value=_mock_token_response(refresh_token="rt-1")),
    )
    state = "matching-state"
    state_payload = oauth_state_cookie.OAuthStatePayload(
        state=state,
        verifier="verifier-1",
        return_to="/reports/abc",
        exp=int(time.time()) + 60,
        nonce="nonce-1",
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(oauth_state_cookie.STATE_COOKIE_NAME, oauth_state_cookie.encrypt(state_payload))
        resp = await client.get(
            "/api/v1/auth/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/reports/abc"
    assert "seizu_session" in resp.cookies
    session_payload = session_cookie.decrypt(resp.cookies["seizu_session"])
    assert session_payload.refresh_token == "rt-1"
    assert session_payload.id_token == "id-token-1"


async def test_callback_rejects_invalid_id_token(mocker):
    mocker.patch("reporting.services.oauth_client.get_metadata", AsyncMock(return_value=_mock_metadata()))
    mocker.patch(
        "reporting.services.oauth_client.exchange_code",
        AsyncMock(return_value=_mock_token_response(refresh_token="rt-1")),
    )
    mocker.patch(
        "reporting.services.oauth_client.validate_id_token",
        AsyncMock(side_effect=oauth_client.OAuthClientError("ID token nonce mismatch")),
    )
    state = "matching-state"
    state_payload = oauth_state_cookie.OAuthStatePayload(
        state=state,
        verifier="verifier-1",
        return_to="/reports/abc",
        exp=int(time.time()) + 60,
        nonce="nonce-1",
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(oauth_state_cookie.STATE_COOKIE_NAME, oauth_state_cookie.encrypt(state_payload))
        resp = await client.get(
            "/api/v1/auth/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
    assert resp.status_code == 400
    assert "seizu_session" not in resp.cookies


async def test_callback_rejects_missing_id_token_when_validation_enabled(mocker):
    mocker.patch("reporting.services.oauth_client.get_metadata", AsyncMock(return_value=_mock_metadata()))
    validate = mocker.patch("reporting.services.oauth_client.validate_id_token", AsyncMock())
    mocker.patch(
        "reporting.services.oauth_client.exchange_code",
        AsyncMock(return_value=_mock_token_response(refresh_token="rt-1", id_token=None)),
    )
    state = "matching-state"
    state_payload = oauth_state_cookie.OAuthStatePayload(
        state=state,
        verifier="verifier-1",
        return_to="/reports/abc",
        exp=int(time.time()) + 60,
        nonce="nonce-1",
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(oauth_state_cookie.STATE_COOKIE_NAME, oauth_state_cookie.encrypt(state_payload))
        resp = await client.get(
            "/api/v1/auth/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
    assert resp.status_code == 400
    assert "seizu_session" not in resp.cookies
    validate.assert_not_called()


async def test_callback_rejects_when_idp_returns_no_refresh_token(mocker):
    mocker.patch("reporting.services.oauth_client.get_metadata", AsyncMock(return_value=_mock_metadata()))
    mocker.patch(
        "reporting.services.oauth_client.exchange_code",
        AsyncMock(return_value=_mock_token_response(refresh_token=None)),
    )
    state = "s"
    state_payload = oauth_state_cookie.OAuthStatePayload(
        state=state, verifier="v", return_to="/", exp=int(time.time()) + 60, nonce="nonce-1"
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(oauth_state_cookie.STATE_COOKIE_NAME, oauth_state_cookie.encrypt(state_payload))
        resp = await client.get("/api/v1/auth/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert resp.status_code == 400


async def test_refresh_returns_401_when_no_cookie():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/auth/refresh", headers={"X-Seizu-Csrf": "1"})
    assert resp.status_code == 401


async def test_refresh_returns_new_access_token_and_rolls_cookie(mocker):
    mocker.patch(
        "reporting.services.oauth_client.refresh_tokens",
        AsyncMock(return_value=_mock_token_response(refresh_token="rt-rotated")),
    )
    payload = session_cookie.SessionPayload(
        refresh_token="rt-old",
        iat=int(time.time()) - 60,
        abs_exp=int(time.time()) + 30 * 24 * 60 * 60,
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("seizu_session", session_cookie.encrypt(payload))
        resp = await client.post("/api/v1/auth/refresh", headers={"X-Seizu-Csrf": "1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] == "at-new"
    assert body["token_type"] == "Bearer"
    # New cookie carries the rotated refresh token.
    new_payload = session_cookie.decrypt(resp.cookies["seizu_session"])
    assert new_payload.refresh_token == "rt-rotated"
    assert new_payload.id_token == "id-token-1"


async def test_refresh_extends_abs_exp_when_idp_advertises_fresh_lifetime(mocker):
    """IDPs that reset refresh-token lifetime on rotation roll the session cap forward."""
    mocker.patch(
        "reporting.services.oauth_client.refresh_tokens",
        AsyncMock(return_value=_mock_token_response(refresh_token="rt-rotated")),
    )
    now = int(time.time())
    near_cap = now + 120  # original absolute cap about to lapse
    payload = session_cookie.SessionPayload(refresh_token="rt-old", iat=now - 60, abs_exp=near_cap)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("seizu_session", session_cookie.encrypt(payload))
        resp = await client.post("/api/v1/auth/refresh", headers={"X-Seizu-Csrf": "1"})
    assert resp.status_code == 200
    new_payload = session_cookie.decrypt(resp.cookies["seizu_session"])
    # _mock_token_response advertises refresh_expires_in=30d, so the cap rolls
    # forward well past the near-lapse original.
    assert new_payload.abs_exp >= now + 30 * 24 * 60 * 60 - 5


async def test_refresh_keeps_abs_exp_when_idp_omits_lifetime(mocker):
    """Without refresh_expires_in the session keeps its original absolute cap."""
    token = oauth_client.TokenResponse(
        access_token="at-new",
        refresh_token="rt-rotated",
        expires_in=300,
        refresh_expires_in=None,
        id_token="id-token-1",
        token_type="Bearer",
        scope="openid email offline_access",
    )
    mocker.patch("reporting.services.oauth_client.refresh_tokens", AsyncMock(return_value=token))
    now = int(time.time())
    original_cap = now + 7 * 24 * 60 * 60
    payload = session_cookie.SessionPayload(refresh_token="rt-old", iat=now - 60, abs_exp=original_cap)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("seizu_session", session_cookie.encrypt(payload))
        resp = await client.post("/api/v1/auth/refresh", headers={"X-Seizu-Csrf": "1"})
    assert resp.status_code == 200
    new_payload = session_cookie.decrypt(resp.cookies["seizu_session"])
    assert new_payload.abs_exp == original_cap


async def test_refresh_uses_configured_session_cookie_name(mocker):
    mocker.patch("reporting.settings.SESSION_COOKIE_NAME", "custom_session")
    mocker.patch(
        "reporting.services.oauth_client.refresh_tokens",
        AsyncMock(return_value=_mock_token_response(refresh_token="rt-rotated")),
    )
    payload = session_cookie.SessionPayload(
        refresh_token="rt-old",
        iat=int(time.time()) - 60,
        abs_exp=int(time.time()) + 30 * 24 * 60 * 60,
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("custom_session", session_cookie.encrypt(payload))
        resp = await client.post("/api/v1/auth/refresh", headers={"X-Seizu-Csrf": "1"})
    assert resp.status_code == 200
    assert "custom_session" in resp.cookies


async def test_refresh_clears_cookie_when_idp_rejects(mocker):
    mocker.patch(
        "reporting.services.oauth_client.refresh_tokens",
        AsyncMock(side_effect=oauth_client.OAuthClientError("invalid_grant")),
    )
    payload = session_cookie.SessionPayload(
        refresh_token="rt-revoked",
        iat=int(time.time()) - 60,
        abs_exp=int(time.time()) + 30 * 24 * 60 * 60,
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("seizu_session", session_cookie.encrypt(payload))
        resp = await client.post("/api/v1/auth/refresh", headers={"X-Seizu-Csrf": "1"})
    assert resp.status_code == 401
    # delete_cookie produces a Set-Cookie that expires the cookie.
    set_cookie = resp.headers.get("set-cookie", "")
    assert "seizu_session=" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()


async def test_logout_clears_cookie_and_revokes_refresh_token(mocker):
    revoke_mock = AsyncMock()
    mocker.patch("reporting.services.oauth_client.revoke_refresh_token", revoke_mock)
    mocker.patch(
        "reporting.services.oauth_client.build_post_logout_url",
        AsyncMock(return_value="http://idp.test/end-session?x=1"),
    )
    payload = session_cookie.SessionPayload(
        refresh_token="rt-active",
        iat=int(time.time()) - 60,
        abs_exp=int(time.time()) + 30 * 24 * 60 * 60,
        id_token="id-token-1",
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("seizu_session", session_cookie.encrypt(payload))
        resp = await client.post("/api/v1/auth/logout", headers={"X-Seizu-Csrf": "1"})
    assert resp.status_code == 200
    assert resp.json()["post_logout_url"] == "http://idp.test/end-session?x=1"
    revoke_mock.assert_awaited_once()
    # passed the refresh token
    assert revoke_mock.await_args.kwargs["refresh_token"] == "rt-active"
    set_cookie = resp.headers.get("set-cookie", "")
    assert "seizu_session=" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()


async def test_logout_builds_post_logout_url_with_id_token(mocker):
    revoke_mock = AsyncMock()
    build_post_logout_mock = AsyncMock(return_value="http://idp.test/end-session?x=1")
    mocker.patch("reporting.services.oauth_client.revoke_refresh_token", revoke_mock)
    mocker.patch("reporting.services.oauth_client.build_post_logout_url", build_post_logout_mock)
    payload = session_cookie.SessionPayload(
        refresh_token="rt-active",
        iat=int(time.time()) - 60,
        abs_exp=int(time.time()) + 30 * 24 * 60 * 60,
        id_token="id-token-1",
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("seizu_session", session_cookie.encrypt(payload))
        resp = await client.post("/api/v1/auth/logout", headers={"X-Seizu-Csrf": "1"})
    assert resp.status_code == 200
    assert resp.json()["post_logout_url"] == "http://idp.test/end-session?x=1"
    build_post_logout_mock.assert_awaited_once_with(
        id_token_hint="id-token-1",
        post_logout_redirect_uri="http://test/logged-out",
    )


async def test_logout_uses_configured_session_cookie_name(mocker):
    mocker.patch("reporting.settings.SESSION_COOKIE_NAME", "custom_session")
    revoke_mock = AsyncMock()
    mocker.patch("reporting.services.oauth_client.revoke_refresh_token", revoke_mock)
    payload = session_cookie.SessionPayload(
        refresh_token="rt-active",
        iat=int(time.time()) - 60,
        abs_exp=int(time.time()) + 30 * 24 * 60 * 60,
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("custom_session", session_cookie.encrypt(payload))
        resp = await client.post("/api/v1/auth/logout", headers={"X-Seizu-Csrf": "1"})
    assert resp.status_code == 200
    revoke_mock.assert_awaited_once()
    set_cookie = resp.headers.get("set-cookie", "")
    assert "custom_session=" in set_cookie


async def test_logout_succeeds_without_cookie(mocker):
    revoke_mock = AsyncMock()
    mocker.patch("reporting.services.oauth_client.revoke_refresh_token", revoke_mock)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/auth/logout", headers={"X-Seizu-Csrf": "1"})
    assert resp.status_code == 200
    revoke_mock.assert_not_awaited()


async def test_logout_swallows_idp_end_session_errors(mocker):
    mocker.patch(
        "reporting.services.oauth_client.revoke_refresh_token",
        AsyncMock(side_effect=RuntimeError("network down")),
    )
    payload = session_cookie.SessionPayload(refresh_token="rt", iat=int(time.time()), abs_exp=int(time.time()) + 60)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("seizu_session", session_cookie.encrypt(payload))
        resp = await client.post("/api/v1/auth/logout", headers={"X-Seizu-Csrf": "1"})
    assert resp.status_code == 200


async def test_logout_skips_end_session_when_disabled(mocker):
    mocker.patch("reporting.settings.OIDC_REVOKE_REFRESH_TOKEN_ON_LOGOUT", False)
    revoke_mock = AsyncMock()
    mocker.patch("reporting.services.oauth_client.revoke_refresh_token", revoke_mock)
    payload = session_cookie.SessionPayload(refresh_token="rt", iat=int(time.time()), abs_exp=int(time.time()) + 60)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("seizu_session", session_cookie.encrypt(payload))
        resp = await client.post("/api/v1/auth/logout", headers={"X-Seizu-Csrf": "1"})
    assert resp.status_code == 200
    revoke_mock.assert_not_awaited()
