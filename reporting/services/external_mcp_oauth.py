"""Seizu as each user's MCP OAuth client for ``user_oauth`` gateways (AGT-059, AGT-060).

A user connects a gateway once from Chat Connections:

1. ``begin_connect`` follows the gateway's 401 to its protected-resource
   metadata (RFC 9728) and authorization-server metadata (RFC 8414).
2. It finds or registers Seizu's client, and returns the authorization URL plus
   an encrypted cookie holding the state, the PKCE verifier and the user.
3. ``complete_connect`` accepts the authorization response only from the
   browser carrying that cookie, exchanges the code, and stores the grant in the
   token vault.

From then on ``access_token`` returns that user's current access token,
refreshing it through the vault's version check when it is about to expire.
Nothing here imports ``external_mcp``; failures are this module's exceptions,
which ``external_mcp`` maps to connection statuses.
"""

from __future__ import annotations

import base64
import dataclasses
import functools
import hashlib
import hmac
import json
import os
import re
import secrets
import ssl
import time
import weakref
from asyncio import Lock
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx2

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.schema.external_mcp import ExternalMCPAuthMode, ExternalMCPProxy
from reporting.services import encryption, token_vault
from reporting.services.token_vault import ClientRegistration, DelegatedGrant, StoredGrant

CONNECT_COOKIE_NAME = "seizu_mcp_connect"
CONNECT_COOKIE_MAX_AGE_SECONDS = 10 * 60
CALLBACK_PATH = "/api/v1/chat/connections/oauth/callback"
_CONNECT_AAD = b"seizu-mcp-connect-v1"
# An access token this close to its expiry is refreshed before use.
_REFRESH_MARGIN_SECONDS = 60
_PROBE_PROTOCOL_VERSION = "2025-11-25"
_HTTP_TIMEOUT_SECONDS = 20.0
_MAX_METADATA_BYTES = 256 * 1024
_RESOURCE_METADATA_RE = re.compile(r"(?:^|[,\s])resource_metadata\s*=\s*(?:\"([^\"]+)\"|([^,\s]+))", re.I)


class OAuthConnectError(RuntimeError):
    """Connecting failed. ``code`` is a stable, display-safe reason for the redirect back."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GrantUnavailable(RuntimeError):
    """The user has no usable grant: never connected, revoked, or unreadable."""


class GatewayOAuthUnavailable(RuntimeError):
    """The gateway's authorization server could not be reached or answered unexpectedly."""


@dataclass(frozen=True)
class ServerMetadata:
    resource: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    revocation_endpoint: str | None = None


@dataclass(frozen=True)
class ConnectStart:
    authorization_url: str
    cookie: str


@dataclass(frozen=True)
class ConnectResult:
    proxy_name: str
    user_id: str


# ── transport ───────────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=4)
def _ssl_context_for(ca_file: str) -> ssl.SSLContext:
    context = httpx2.create_ssl_context()
    try:
        context.load_verify_locations(cafile=ca_file)
    except (OSError, ssl.SSLError) as exc:
        raise RuntimeError(f"MCP_EXTERNAL_CA_FILE could not be loaded from {ca_file!r}") from exc
    return context


def ssl_verification() -> ssl.SSLContext | bool:
    """TLS verification for external MCP traffic: the system store plus ``MCP_EXTERNAL_CA_FILE``."""
    if not settings.MCP_EXTERNAL_CA_FILE:
        return True
    return _ssl_context_for(settings.MCP_EXTERNAL_CA_FILE)


@asynccontextmanager
async def _http_client(proxy: ExternalMCPProxy) -> AsyncIterator[httpx2.AsyncClient]:
    async with httpx2.AsyncClient(
        verify=ssl_verification(),
        timeout=httpx2.Timeout(min(_HTTP_TIMEOUT_SECONDS, proxy.connect_timeout_seconds * 2)),
        follow_redirects=False,
    ) as client:
        yield client


def _json_object(response: httpx2.Response, what: str) -> dict[str, Any]:
    if len(response.content) > _MAX_METADATA_BYTES:
        raise GatewayOAuthUnavailable(f"{what} is too large")
    try:
        data = response.json()
    except ValueError as exc:
        raise GatewayOAuthUnavailable(f"{what} is not JSON") from exc
    if not isinstance(data, dict):
        raise GatewayOAuthUnavailable(f"{what} is not a JSON object")
    return data


def _http_url(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise GatewayOAuthUnavailable(f"{what} is missing")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise GatewayOAuthUnavailable(f"{what} is not an http(s) URL")
    return value


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    return parts.scheme, (parts.hostname or "").lower(), port


# ── discovery ───────────────────────────────────────────────────────────────


def _well_known(url: str, name: str) -> str:
    """RFC 8414 section 3.1 / RFC 9728 section 3.1: the segment goes before the path."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/.well-known/{name}{parts.path.rstrip('/')}", "", ""))


async def discover(proxy: ExternalMCPProxy) -> ServerMetadata:
    """Find the authorization server for ``proxy`` from an unauthenticated request."""
    async with _http_client(proxy) as client:
        try:
            probe = await client.post(
                proxy.url,
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": _PROBE_PROTOCOL_VERSION,
                },
            )
        except httpx2.HTTPError as exc:
            raise GatewayOAuthUnavailable("the gateway could not be reached") from exc
        match = _RESOURCE_METADATA_RE.search(probe.headers.get("WWW-Authenticate", ""))
        metadata_url = (
            (match.group(1) or match.group(2)) if match else _well_known(proxy.url, "oauth-protected-resource")
        )
        if _origin(_http_url(metadata_url, "the protected-resource metadata URL")) != _origin(proxy.url):
            raise GatewayOAuthUnavailable("the protected-resource metadata is on another origin")
        try:
            resource_response = await client.get(metadata_url, headers={"Accept": "application/json"})
        except httpx2.HTTPError as exc:
            raise GatewayOAuthUnavailable("the protected-resource metadata could not be fetched") from exc
        if resource_response.status_code != 200:
            raise GatewayOAuthUnavailable("the gateway publishes no protected-resource metadata")
        resource_metadata = _json_object(resource_response, "the protected-resource metadata")
        resource = _http_url(resource_metadata.get("resource"), "the protected resource")
        if _origin(resource) != _origin(proxy.url):
            raise GatewayOAuthUnavailable("the protected resource is not the configured gateway")
        servers = resource_metadata.get("authorization_servers")
        if not isinstance(servers, list) or not servers:
            raise GatewayOAuthUnavailable("the gateway names no authorization server")
        issuer = _http_url(servers[0], "the authorization server")
        return await _authorization_server(client, issuer, resource)


async def _authorization_server(client: httpx2.AsyncClient, issuer: str, resource: str) -> ServerMetadata:
    candidates = [
        _well_known(issuer, "oauth-authorization-server"),
        issuer.rstrip("/") + "/.well-known/openid-configuration",
    ]
    for url in candidates:
        try:
            response = await client.get(url, headers={"Accept": "application/json"})
        except httpx2.HTTPError:
            continue
        if response.status_code != 200:
            continue
        data = _json_object(response, "the authorization-server metadata")
        # RFC 8414 section 3.3: metadata naming another issuer must not be used.
        if data.get("issuer") != issuer:
            raise GatewayOAuthUnavailable("the authorization-server metadata names a different issuer")
        methods = data.get("code_challenge_methods_supported")
        if isinstance(methods, list) and "S256" not in methods:
            raise GatewayOAuthUnavailable("the authorization server does not support PKCE S256")
        registration = data.get("registration_endpoint")
        revocation = data.get("revocation_endpoint")
        return ServerMetadata(
            resource=resource,
            issuer=issuer,
            authorization_endpoint=_http_url(data.get("authorization_endpoint"), "the authorization endpoint"),
            token_endpoint=_http_url(data.get("token_endpoint"), "the token endpoint"),
            registration_endpoint=_http_url(registration, "the registration endpoint") if registration else None,
            revocation_endpoint=_http_url(revocation, "the revocation endpoint") if revocation else None,
        )
    raise GatewayOAuthUnavailable("the authorization server publishes no metadata")


# ── client registration ─────────────────────────────────────────────────────


def _configured_client(proxy: ExternalMCPProxy, redirect_uri: str) -> ClientRegistration | None:
    config = proxy.oauth
    if config is None or not config.client_id:
        return None
    if not config.client_secret_env:
        return ClientRegistration(
            client_id=config.client_id, redirect_uri=redirect_uri, token_endpoint_auth_method="none"
        )
    secret = os.environ.get(config.client_secret_env, "").strip()
    if not secret:
        raise OAuthConnectError("client_misconfigured", f"{config.client_secret_env} is empty for proxy {proxy.name!r}")
    return ClientRegistration(
        client_id=config.client_id,
        redirect_uri=redirect_uri,
        client_secret=secret,
        token_endpoint_auth_method=config.token_endpoint_auth_method,
    )


async def _client_for_connect(
    client: httpx2.AsyncClient, proxy: ExternalMCPProxy, server: ServerMetadata, redirect_uri: str
) -> ClientRegistration:
    configured = _configured_client(proxy, redirect_uri)
    if configured is not None:
        return configured
    try:
        existing = await token_vault.find_client(server.issuer, redirect_uri)
    except token_vault.VaultError:
        # Unreadable (its key was removed): register afresh rather than refuse
        # every connection. Grants issued to the old client fail on their own.
        existing = None
    if existing is not None:
        return existing
    if not server.registration_endpoint:
        raise OAuthConnectError(
            "registration_unsupported",
            f"the gateway for {proxy.name!r} does not support dynamic registration; configure oauth.client_id",
        )
    body: dict[str, Any] = {
        "client_name": "Seizu",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_basic",
    }
    if proxy.oauth and proxy.oauth.scope:
        body["scope"] = proxy.oauth.scope
    try:
        response = await client.post(server.registration_endpoint, json=body, headers={"Accept": "application/json"})
    except httpx2.HTTPError as exc:
        raise GatewayOAuthUnavailable("client registration could not be reached") from exc
    if response.status_code not in (200, 201):
        raise OAuthConnectError(
            "registration_refused", f"the gateway refused client registration ({response.status_code})"
        )
    data = _json_object(response, "the client registration")
    client_id = data.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise GatewayOAuthUnavailable("the client registration has no client_id")
    secret = data.get("client_secret")
    registration = ClientRegistration(
        client_id=client_id,
        redirect_uri=redirect_uri,
        client_secret=secret if isinstance(secret, str) and secret else None,
        token_endpoint_auth_method=str(data.get("token_endpoint_auth_method") or "client_secret_basic")
        if secret
        else "none",
    )
    await token_vault.save_client(server.issuer, registration)
    return registration


async def _client_for_grant(proxy: ExternalMCPProxy, grant: DelegatedGrant) -> ClientRegistration:
    configured = _configured_client(proxy, redirect_uri="")
    if configured is not None:
        if configured.client_id != grant.client_id:
            raise GrantUnavailable("the grant was issued to a client that is no longer configured")
        return configured
    if not grant.issuer:
        raise GrantUnavailable("the grant does not name its authorization server")
    try:
        registration = await token_vault.load_client(grant.issuer, grant.client_id)
    except token_vault.VaultError as exc:
        raise GrantUnavailable("the client registration cannot be read") from exc
    if registration is None:
        raise GrantUnavailable("the client registration is gone")
    return registration


# ── token endpoint ──────────────────────────────────────────────────────────


def _authenticate_client(registration: ClientRegistration, form: dict[str, str], headers: dict[str, str]) -> None:
    """Add the client's credentials to a token or revocation request, as it was registered."""
    method = registration.token_endpoint_auth_method
    if method == "client_secret_basic" and registration.client_secret:
        # Sent unencoded, though RFC 6749 section 2.3.1 asks for form-encoding:
        # servers that decode leave a value without "%" unchanged, and servers
        # that do not (Pomerium among them) reject an encoded "+" or "/".
        pair = f"{registration.client_id}:{registration.client_secret}"
        headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode("ascii")
        return
    form["client_id"] = registration.client_id
    if method == "client_secret_post" and registration.client_secret:
        form["client_secret"] = registration.client_secret


async def _token_request(
    client: httpx2.AsyncClient, token_endpoint: str, registration: ClientRegistration, fields: dict[str, str]
) -> dict[str, Any]:
    headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
    form = dict(fields)
    _authenticate_client(registration, form, headers)
    try:
        response = await client.post(token_endpoint, content=urlencode(form), headers=headers)
    except httpx2.HTTPError as exc:
        raise GatewayOAuthUnavailable("the token endpoint could not be reached") from exc
    if response.status_code in (400, 401):
        try:
            error = response.json().get("error")
        except (ValueError, AttributeError):
            error = None
        if error in {"invalid_grant", "invalid_client", "unauthorized_client"}:
            raise GrantUnavailable(f"the authorization server refused the grant ({error})")
        raise GatewayOAuthUnavailable(f"the token endpoint refused the request ({response.status_code})")
    if response.status_code != 200:
        raise GatewayOAuthUnavailable(f"the token endpoint failed ({response.status_code})")
    data = _json_object(response, "the token response")
    if not isinstance(data.get("access_token"), str) or not data["access_token"]:
        raise GatewayOAuthUnavailable("the token response has no access token")
    if str(data.get("token_type", "")).lower() != "bearer":
        raise GatewayOAuthUnavailable("the token response is not a bearer token")
    return data


def _expires_at(tokens: dict[str, Any]) -> int | None:
    try:
        lifetime = int(tokens["expires_in"])
    except (KeyError, TypeError, ValueError):
        return None
    return int(time.time()) + max(0, lifetime)


# ── connecting ──────────────────────────────────────────────────────────────


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


async def begin_connect(proxy: ExternalMCPProxy, user: CurrentUser, redirect_uri: str) -> ConnectStart:
    """Return where to send the browser, and the cookie that must come back with it."""
    if proxy.auth_mode != ExternalMCPAuthMode.USER_OAUTH or not proxy.enabled:
        raise OAuthConnectError("not_connectable", f"proxy {proxy.name!r} is not a user_oauth gateway")
    server = await discover(proxy)
    async with _http_client(proxy) as client:
        registration = await _client_for_connect(client, proxy, server, redirect_uri)
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(32)
    params = {
        "response_type": "code",
        "client_id": registration.client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
        "state": state,
        "resource": server.resource,
    }
    if proxy.oauth and proxy.oauth.scope:
        params["scope"] = proxy.oauth.scope
    separator = "&" if urlsplit(server.authorization_endpoint).query else "?"
    payload = {
        "state": state,
        "verifier": verifier,
        "user_id": user.user.user_id,
        "proxy": proxy.name,
        "proxy_url": proxy.url,
        "resource": server.resource,
        "issuer": server.issuer,
        "token_endpoint": server.token_endpoint,
        "revocation_endpoint": server.revocation_endpoint,
        "client_id": registration.client_id,
        "redirect_uri": redirect_uri,
        "exp": int(time.time()) + CONNECT_COOKIE_MAX_AGE_SECONDS,
    }
    sealed = await encryption.session_cipher().encrypt(
        json.dumps(payload, separators=(",", ":")).encode(), _CONNECT_AAD
    )
    return ConnectStart(
        authorization_url=server.authorization_endpoint + separator + urlencode(params), cookie=_b64url(sealed)
    )


async def _open_connect_cookie(cookie: str | None) -> dict[str, Any]:
    if not cookie:
        raise OAuthConnectError("connect_expired", "no connection is in progress in this browser")
    try:
        plaintext = await encryption.session_cipher().decrypt(_b64url_decode(cookie), _CONNECT_AAD)
        payload = json.loads(plaintext)
    except (ValueError, encryption.DecryptionError) as exc:
        raise OAuthConnectError("connect_expired", "the connection cookie is invalid") from exc
    if not isinstance(payload, dict) or int(payload.get("exp", 0)) <= time.time():
        raise OAuthConnectError("connect_expired", "the connection attempt expired")
    return payload


async def complete_connect(
    cookie: str | None, *, state: str, code: str, proxies: list[ExternalMCPProxy]
) -> ConnectResult:
    """Exchange the authorization code for the user who started this browser's connection."""
    payload = await _open_connect_cookie(cookie)
    if not state or not hmac.compare_digest(str(payload.get("state", "")), state):
        raise OAuthConnectError("state_mismatch", "the authorization response does not match this connection")
    proxy = next(
        (
            p
            for p in proxies
            if p.name == payload.get("proxy")
            and p.enabled
            and p.auth_mode == ExternalMCPAuthMode.USER_OAUTH
            and p.url == payload.get("proxy_url")
        ),
        None,
    )
    if proxy is None:
        raise OAuthConnectError("not_connectable", "the gateway is no longer configured")
    issuer = str(payload["issuer"])
    client_id = str(payload["client_id"])
    redirect_uri = str(payload["redirect_uri"])
    registration = _configured_client(proxy, redirect_uri)
    if registration is None:
        registration = await token_vault.load_client(issuer, client_id)
    if registration is None or registration.client_id != client_id:
        raise OAuthConnectError("client_missing", "Seizu's client registration with the gateway is gone")
    async with _http_client(proxy) as client:
        try:
            tokens = await _token_request(
                client,
                str(payload["token_endpoint"]),
                registration,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": str(payload["verifier"]),
                    "resource": str(payload["resource"]),
                },
            )
        except GrantUnavailable as exc:
            raise OAuthConnectError("code_rejected", "the gateway rejected the authorization") from exc
    refresh_token = tokens.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise OAuthConnectError("no_refresh_token", "the gateway did not grant offline access")
    grant = DelegatedGrant(
        refresh_token=refresh_token,
        token_endpoint=str(payload["token_endpoint"]),
        client_id=client_id,
        access_token=tokens["access_token"],
        access_token_expires_at=_expires_at(tokens),
        scope=tokens.get("scope") if isinstance(tokens.get("scope"), str) else None,
        issuer=issuer,
        revocation_endpoint=payload.get("revocation_endpoint"),
        resource=str(payload["resource"]),
    )
    user_id = str(payload["user_id"])
    await token_vault.save(user_id, proxy.name, proxy.url, grant)
    return ConnectResult(proxy_name=proxy.name, user_id=user_id)


# ── using a grant ───────────────────────────────────────────────────────────

_refresh_locks: weakref.WeakValueDictionary[tuple[str, str], Lock] = weakref.WeakValueDictionary()


def _fresh(grant: DelegatedGrant) -> bool:
    if not grant.access_token:
        return False
    return (
        grant.access_token_expires_at is None or grant.access_token_expires_at - _REFRESH_MARGIN_SECONDS > time.time()
    )


async def _load(proxy: ExternalMCPProxy, user: CurrentUser) -> StoredGrant:
    try:
        stored = await token_vault.load(user.user.user_id, proxy.name, proxy.url)
    except token_vault.VaultError as exc:
        raise GrantUnavailable("the stored grant cannot be read") from exc
    if stored is None:
        raise GrantUnavailable("the user has not connected this gateway")
    return stored


async def access_token(proxy: ExternalMCPProxy, user: CurrentUser) -> str:
    """Return the user's access token for ``proxy``, refreshing it first if it is about to expire."""
    key = (user.user.user_id, proxy.name)
    lock = _refresh_locks.get(key)
    if lock is None:
        lock = _refresh_locks[key] = Lock()
    async with lock:
        stored = await _load(proxy, user)
        if _fresh(stored.grant):
            return str(stored.grant.access_token)
        return await _refresh(proxy, user, stored)


async def _refresh(proxy: ExternalMCPProxy, user: CurrentUser, stored: StoredGrant) -> str:
    registration = await _client_for_grant(proxy, stored.grant)
    fields = {"grant_type": "refresh_token", "refresh_token": stored.grant.refresh_token}
    if stored.grant.resource:
        fields["resource"] = stored.grant.resource
    async with _http_client(proxy) as client:
        try:
            tokens = await _token_request(client, stored.grant.token_endpoint, registration, fields)
        except GrantUnavailable:
            # Another worker may have spent this refresh token first; its grant
            # is then the live one, and this refusal says nothing about it.
            reloaded = await _load(proxy, user)
            if reloaded.version != stored.version and _fresh(reloaded.grant):
                return str(reloaded.grant.access_token)
            raise
    rotated = dataclasses.replace(
        stored.grant,
        refresh_token=tokens.get("refresh_token") or stored.grant.refresh_token,
        access_token=tokens["access_token"],
        access_token_expires_at=_expires_at(tokens),
        scope=tokens["scope"] if isinstance(tokens.get("scope"), str) else stored.grant.scope,
    )
    version = await token_vault.replace(
        user.user.user_id, proxy.name, proxy.url, rotated, expected_version=stored.version
    )
    if version is None:
        reloaded = await _load(proxy, user)
        if _fresh(reloaded.grant):
            return str(reloaded.grant.access_token)
    return str(tokens["access_token"])


async def forget_access_token(proxy: ExternalMCPProxy, user: CurrentUser, token: str) -> None:
    """Drop an access token the gateway refused, so the next use refreshes instead of retrying it."""
    try:
        stored = await token_vault.load(user.user.user_id, proxy.name, proxy.url)
    except token_vault.VaultError:
        return
    if stored is None or stored.grant.access_token != token:
        return
    await token_vault.replace(
        user.user.user_id,
        proxy.name,
        proxy.url,
        dataclasses.replace(stored.grant, access_token=None, access_token_expires_at=None),
        expected_version=stored.version,
    )


async def disconnect(proxy: ExternalMCPProxy, user: CurrentUser) -> bool:
    """Revoke the user's grant at the gateway (best effort) and delete it; return whether one existed."""
    try:
        stored = await token_vault.load(user.user.user_id, proxy.name, proxy.url)
    except token_vault.VaultError:
        stored = None
    if stored is not None and stored.grant.revocation_endpoint:
        try:
            registration = await _client_for_grant(proxy, stored.grant)
            form = {"token": stored.grant.refresh_token, "token_type_hint": "refresh_token"}
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            _authenticate_client(registration, form, headers)
            async with _http_client(proxy) as client:
                await client.post(stored.grant.revocation_endpoint, content=urlencode(form), headers=headers)
        except (GrantUnavailable, OAuthConnectError, httpx2.HTTPError):
            # Revocation is a courtesy to the gateway; the grant is deleted regardless.
            pass
    return await token_vault.delete(user.user.user_id, proxy.name, proxy.url) > 0
