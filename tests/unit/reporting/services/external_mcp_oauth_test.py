"""Seizu's user_oauth client against a fake gateway, with the real vault on SQLite."""

import base64
import json
import os
import time
from contextlib import asynccontextmanager
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from reporting.authnz import CurrentUser
from reporting.schema.external_mcp import ExternalMCPProxy
from reporting.schema.report_config import User
from reporting.services import external_mcp_oauth, report_store, token_vault
from reporting.services.external_mcp_oauth import GrantUnavailable, OAuthConnectError
from reporting.services.report_store.sql import SQLModelReportStore

GATEWAY = "https://deps.gateway.test"
REDIRECT = "https://seizu.test/api/v1/chat/connections/oauth/callback"


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture(autouse=True)
def _keys(mocker):
    mocker.patch("reporting.settings.SESSION_TOKEN_ENCRYPTION_KEY", _key())
    mocker.patch("reporting.settings.DELEGATED_TOKEN_ENCRYPTION_KEYS", f"k1:{_key()}")


@pytest.fixture(autouse=True)
async def _store():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    store = SQLModelReportStore()
    with (
        patch("reporting.services.report_store.sql._get_engine", return_value=engine),
        patch.object(report_store, "get_store", return_value=store),
    ):
        yield
    await engine.dispose()


class FakeGateway:
    """A Pomerium-shaped MCP gateway: its own authorization server, rotating refresh tokens."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.registrations = 0
        self.rotations = 0
        self.refresh_tokens = {"rt-0"}
        self.metadata_issuer = GATEWAY
        self.prm_resource = GATEWAY
        self.issue_refresh = True
        self.secret = "s3cr+t/with=specials"
        self.clients = {"client-1"}

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        url = urlsplit(str(request.url))
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()} if request.content else {}
        self.calls.append((request.method, url.path, form))
        if request.method == "POST" and url.path == "/mcp":
            return httpx2.Response(
                401,
                headers={
                    "WWW-Authenticate": f'Bearer resource_metadata="{GATEWAY}/.well-known/oauth-protected-resource"'
                },
            )
        if url.path == "/.well-known/oauth-protected-resource":
            return httpx2.Response(200, json={"resource": self.prm_resource, "authorization_servers": [GATEWAY]})
        if url.path == "/.well-known/oauth-authorization-server":
            return httpx2.Response(
                200,
                json={
                    "issuer": self.metadata_issuer,
                    "authorization_endpoint": f"{GATEWAY}/authorize",
                    "token_endpoint": f"{GATEWAY}/token",
                    "registration_endpoint": f"{GATEWAY}/register",
                    "revocation_endpoint": f"{GATEWAY}/revoke",
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if url.path == "/register":
            self.registrations += 1
            self.clients.add(f"client-{self.registrations}")
            body = json.loads(request.content)
            assert body["token_endpoint_auth_method"] == "client_secret_basic"
            return httpx2.Response(
                201, json={"client_id": f"client-{self.registrations}", "client_secret": self.secret}
            )
        if url.path == "/token":
            accepted = {
                "Basic " + base64.b64encode(f"{client}:{self.secret}".encode()).decode() for client in self.clients
            }
            if request.headers.get("Authorization") not in accepted:
                return httpx2.Response(401, json={"error": "invalid_client"})
            if form["grant_type"] == "authorization_code":
                assert form["code"] == "the-code" and form["code_verifier"]
                tokens = {"access_token": "at-0", "token_type": "Bearer", "expires_in": 3600}
                if self.issue_refresh:
                    tokens["refresh_token"] = "rt-0"
                return httpx2.Response(200, json=tokens)
            if form["refresh_token"] not in self.refresh_tokens:
                return httpx2.Response(400, json={"error": "invalid_grant"})
            self.refresh_tokens.discard(form["refresh_token"])
            self.rotations += 1
            new = f"rt-{self.rotations}"
            self.refresh_tokens.add(new)
            return httpx2.Response(
                200,
                json={
                    "access_token": f"at-{self.rotations}",
                    "token_type": "bearer",
                    "expires_in": 3600,
                    "refresh_token": new,
                },
            )
        if url.path == "/revoke":
            return httpx2.Response(200)
        return httpx2.Response(404)


@pytest.fixture
def gateway(mocker):
    fake = FakeGateway()

    @asynccontextmanager
    async def client(proxy):
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(fake)) as http:
            yield http

    mocker.patch.object(external_mcp_oauth, "_http_client", client)
    return fake


@pytest.fixture
def proxy():
    return ExternalMCPProxy(name="deps", url=f"{GATEWAY}/mcp", transport="streamable_http", auth_mode="user_oauth")


def _user(user_id: str = "alice") -> CurrentUser:
    return CurrentUser(
        user=User(user_id=user_id, sub=user_id, iss="https://idp.test", created_at="now", last_login="now"),
        jwt_claims={},
        permissions=frozenset({"chat:use"}),
    )


async def _connect(proxy, user=None) -> str:
    start = await external_mcp_oauth.begin_connect(proxy, user or _user(), REDIRECT)
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]
    await external_mcp_oauth.complete_connect(start.cookie, state=state, code="the-code", proxies=[proxy])
    return start.authorization_url


# ── discovery and connecting ───────────────────────────────────────────────


async def test_connect_discovers_registers_and_stores_the_grant(gateway, proxy):
    url = await _connect(proxy)
    params = parse_qs(urlsplit(url).query)
    assert url.startswith(f"{GATEWAY}/authorize?")
    assert params["client_id"] == ["client-1"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["resource"] == [GATEWAY]
    assert params["redirect_uri"] == [REDIRECT]
    stored = await token_vault.load("alice", "deps", proxy.url)
    assert stored is not None
    assert stored.grant.refresh_token == "rt-0" and stored.grant.access_token == "at-0"
    assert stored.grant.issuer == GATEWAY and stored.grant.revocation_endpoint == f"{GATEWAY}/revoke"


async def test_one_registration_serves_every_user_and_connection(gateway, proxy):
    await _connect(proxy, _user("alice"))
    await _connect(proxy, _user("bob"))
    assert gateway.registrations == 1


async def test_an_unreadable_registration_is_replaced_not_fatal(gateway, proxy, mocker):
    await _connect(proxy, _user("alice"))
    mocker.patch("reporting.settings.DELEGATED_TOKEN_ENCRYPTION_KEYS", f"k2:{_key()}")
    await _connect(proxy, _user("bob"))
    assert gateway.registrations == 2


async def test_the_registration_secret_is_stored_encrypted(gateway, proxy):
    await _connect(proxy)
    row = await report_store.get_oauth_client(GATEWAY, "client-1")
    assert gateway.secret.encode() not in base64.b64decode(row["ciphertext"])


async def test_a_callback_without_this_browsers_cookie_is_refused(gateway, proxy):
    start = await external_mcp_oauth.begin_connect(proxy, _user(), REDIRECT)
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]
    with pytest.raises(OAuthConnectError) as exc:
        await external_mcp_oauth.complete_connect(None, state=state, code="the-code", proxies=[proxy])
    assert exc.value.code == "connect_expired"
    with pytest.raises(OAuthConnectError) as exc:
        await external_mcp_oauth.complete_connect(start.cookie, state="forged", code="the-code", proxies=[proxy])
    assert exc.value.code == "state_mismatch"
    assert await token_vault.load("alice", "deps", proxy.url) is None


async def test_an_expired_connection_attempt_is_refused(gateway, proxy, mocker):
    start = await external_mcp_oauth.begin_connect(proxy, _user(), REDIRECT)
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]
    mocker.patch.object(external_mcp_oauth.time, "time", return_value=time.time() + 3600)
    with pytest.raises(OAuthConnectError) as exc:
        await external_mcp_oauth.complete_connect(start.cookie, state=state, code="the-code", proxies=[proxy])
    assert exc.value.code == "connect_expired"


async def test_a_grant_without_offline_access_is_not_stored(gateway, proxy):
    gateway.issue_refresh = False
    with pytest.raises(OAuthConnectError) as exc:
        await _connect(proxy)
    assert exc.value.code == "no_refresh_token"
    assert await token_vault.load("alice", "deps", proxy.url) is None


async def test_the_callback_is_refused_once_the_proxy_changes(gateway, proxy):
    start = await external_mcp_oauth.begin_connect(proxy, _user(), REDIRECT)
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]
    moved = proxy.model_copy(update={"url": "https://elsewhere.test/mcp"})
    with pytest.raises(OAuthConnectError) as exc:
        await external_mcp_oauth.complete_connect(start.cookie, state=state, code="the-code", proxies=[moved])
    assert exc.value.code == "not_connectable"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("metadata_issuer", "https://impostor.test", "different issuer"),
        ("prm_resource", "https://other.test", "not the configured gateway"),
    ],
)
async def test_discovery_refuses_metadata_that_does_not_match(gateway, proxy, field, value, message):
    setattr(gateway, field, value)
    with pytest.raises(external_mcp_oauth.GatewayOAuthUnavailable, match=message):
        await external_mcp_oauth.begin_connect(proxy, _user(), REDIRECT)


async def test_a_pre_registered_client_is_used_without_registering(gateway, mocker):
    mocker.patch.dict(os.environ, {"DEPS_CLIENT_SECRET": gateway.secret})
    proxy = ExternalMCPProxy(
        name="deps",
        url=f"{GATEWAY}/mcp",
        transport="streamable_http",
        auth_mode="user_oauth",
        oauth={"client_id": "client-1", "client_secret_env": "DEPS_CLIENT_SECRET"},
    )
    await _connect(proxy)
    assert gateway.registrations == 0
    assert (await token_vault.load("alice", "deps", proxy.url)).grant.client_id == "client-1"


# ── using a grant ──────────────────────────────────────────────────────────


async def _expire(proxy, user_id="alice"):
    stored = await token_vault.load(user_id, "deps", proxy.url)
    import dataclasses

    await token_vault.replace(
        user_id,
        "deps",
        proxy.url,
        dataclasses.replace(stored.grant, access_token_expires_at=1),
        expected_version=stored.version,
    )


async def test_a_fresh_access_token_is_used_without_refreshing(gateway, proxy):
    await _connect(proxy)
    assert await external_mcp_oauth.access_token(proxy, _user()) == "at-0"
    assert gateway.rotations == 0


async def test_an_expiring_access_token_is_refreshed_and_the_rotation_stored(gateway, proxy):
    await _connect(proxy)
    await _expire(proxy)
    assert await external_mcp_oauth.access_token(proxy, _user()) == "at-1"
    stored = await token_vault.load("alice", "deps", proxy.url)
    assert stored.grant.refresh_token == "rt-1"
    refresh = [
        form for method, path, form in gateway.calls if path == "/token" and form["grant_type"] == "refresh_token"
    ]
    assert refresh[-1]["resource"] == GATEWAY


async def test_a_refresh_that_lost_the_race_uses_the_winners_grant(gateway, proxy):
    await _connect(proxy)
    await _expire(proxy)
    stale = await token_vault.load("alice", "deps", proxy.url)
    assert await external_mcp_oauth._refresh(proxy, _user(), stale) == "at-1"
    # The loser spends a refresh token the gateway has already rotated away.
    assert await external_mcp_oauth._refresh(proxy, _user(), stale) == "at-1"
    assert (await token_vault.load("alice", "deps", proxy.url)).grant.refresh_token == "rt-1"


async def test_a_revoked_grant_is_unavailable_and_kept(gateway, proxy):
    await _connect(proxy)
    await _expire(proxy)
    gateway.refresh_tokens.clear()
    with pytest.raises(GrantUnavailable):
        await external_mcp_oauth.access_token(proxy, _user())
    assert await token_vault.load("alice", "deps", proxy.url) is not None


async def test_no_grant_means_unavailable(gateway, proxy):
    with pytest.raises(GrantUnavailable):
        await external_mcp_oauth.access_token(proxy, _user())


async def test_grants_never_cross_users(gateway, proxy):
    await _connect(proxy, _user("alice"))
    with pytest.raises(GrantUnavailable):
        await external_mcp_oauth.access_token(proxy, _user("bob"))


async def test_a_refused_access_token_is_forgotten_so_the_next_use_refreshes(gateway, proxy):
    await _connect(proxy)
    await external_mcp_oauth.forget_access_token(proxy, _user(), "at-0")
    assert await external_mcp_oauth.access_token(proxy, _user()) == "at-1"


async def test_forgetting_a_token_that_was_already_replaced_changes_nothing(gateway, proxy):
    await _connect(proxy)
    await external_mcp_oauth.forget_access_token(proxy, _user(), "some-older-token")
    assert (await token_vault.load("alice", "deps", proxy.url)).grant.access_token == "at-0"


async def test_disconnect_revokes_then_deletes(gateway, proxy):
    await _connect(proxy)
    assert await external_mcp_oauth.disconnect(proxy, _user()) is True
    revocations = [form for method, path, form in gateway.calls if path == "/revoke"]
    assert revocations == [{"token": "rt-0", "token_type_hint": "refresh_token"}]
    assert await token_vault.load("alice", "deps", proxy.url) is None
    assert await external_mcp_oauth.disconnect(proxy, _user()) is False


# ── transport ──────────────────────────────────────────────────────────────


def test_tls_verification_defaults_to_the_system_store(mocker):
    mocker.patch("reporting.settings.MCP_EXTERNAL_CA_FILE", "")
    assert external_mcp_oauth.ssl_verification() is True


def test_an_unreadable_ca_file_names_the_setting(mocker, tmp_path):
    mocker.patch("reporting.settings.MCP_EXTERNAL_CA_FILE", str(tmp_path / "missing.pem"))
    with pytest.raises(RuntimeError, match="MCP_EXTERNAL_CA_FILE"):
        external_mcp_oauth.ssl_verification()
