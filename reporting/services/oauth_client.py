"""OAuth/OIDC client for the BFF auth flow.

Thin facade over authlib's ``AsyncOAuth2Client`` plus a hand-rolled
discovery cache. Authlib handles the RFC 6749 / 7636 / 7009 / 7662 mechanics
(PKCE, multiple client-auth styles, token revocation and introspection,
error parsing per RFC 6749 §5.2).
We keep two things outside authlib:

- **Discovery fetching** — so we can use ``OIDC_INTERNAL_AUTHORITY`` for
  the server-side fetch while exposing the public ``OIDC_AUTHORITY`` to
  the browser.
- **``rewrite_to_external_origin``** — for IDPs that base discovery URLs
  on the request Host header and lack an explicit "browser host" setting.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from authlib.common.errors import AuthlibBaseError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from jwt import PyJWKClient

from reporting import settings

logger = logging.getLogger(__name__)

_OIDC_DISCOVERY_TIMEOUT = 10.0
_OIDC_REQUEST_TIMEOUT = 30.0


class OAuthClientError(RuntimeError):
    """Raised when an interaction with the IDP fails."""


@dataclass(frozen=True)
class OIDCMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    end_session_endpoint: str | None
    revocation_endpoint: str | None
    introspection_endpoint: str | None
    jwks_uri: str | None


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    refresh_expires_in: int | None
    id_token: str | None
    token_type: str
    scope: str | None


_metadata_cache: OIDCMetadata | None = None
_metadata_cached_at: float = 0.0
_metadata_lock = asyncio.Lock()


def _authority_for_discovery() -> str:
    authority = (settings.OIDC_INTERNAL_AUTHORITY or settings.OIDC_AUTHORITY).rstrip("/")
    if not authority:
        raise OAuthClientError("OIDC_AUTHORITY (or OIDC_INTERNAL_AUTHORITY) must be configured")
    return authority


def _normalize_issuer(value: str) -> str:
    return value.rstrip("/")


def _expected_issuers() -> set[str]:
    issuers = {
        _normalize_issuer(value)
        for value in (
            settings.OIDC_AUTHORITY,
            settings.OIDC_INTERNAL_AUTHORITY,
            settings.JWT_ISSUER,
        )
        if value
    }
    return issuers


def rewrite_to_external_origin(url: str) -> str:
    """Swap an internal-authority origin for the externally-reachable one.

    Authentik (and some other IDPs) base discovery-document URLs on the
    request's Host header. In split-hostname docker-dev setups, the server
    fetches discovery from ``OIDC_INTERNAL_AUTHORITY`` (e.g.
    ``http://authentik-server:9000``) and gets back URLs with that
    docker-internal hostname — which the browser can't reach. For any URL
    we hand to the browser (currently just the authorize URL), rewrite the
    scheme+host+port to match ``OIDC_AUTHORITY``. URLs already on the
    external origin, or with no internal/external split configured, are
    returned unchanged.
    """
    internal_raw = settings.OIDC_INTERNAL_AUTHORITY
    external_raw = settings.OIDC_AUTHORITY
    if not internal_raw or not external_raw or internal_raw == external_raw:
        return url
    internal = urlparse(internal_raw)
    external = urlparse(external_raw)
    internal_origin = f"{internal.scheme}://{internal.netloc}"
    external_origin = f"{external.scheme}://{external.netloc}"
    if url.startswith(internal_origin):
        return external_origin + url[len(internal_origin) :]
    return url


async def _fetch_discovery_document(authority: str) -> dict[str, Any]:
    url = f"{authority.rstrip('/')}/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=_OIDC_DISCOVERY_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            doc = resp.json()
    except httpx.HTTPError as exc:
        raise OAuthClientError(f"Failed to fetch OIDC discovery from {url}: {exc}") from exc
    if not isinstance(doc, dict):
        raise OAuthClientError(f"OIDC discovery document at {url} is not a JSON object")
    return doc


async def _discovery_issuer(authority: str) -> str:
    doc = await _fetch_discovery_document(authority)
    issuer = doc.get("issuer")
    if not isinstance(issuer, str) or not issuer:
        raise OAuthClientError(f"OIDC discovery document at {authority} advertises no issuer")
    return issuer


async def verify_issuer_consistency() -> None:
    """Check that the internal and external authorities advertise one issuer.

    Durable Seizu identity is ``(iss, sub)``, so an IDP that derives its issuer
    from the request Host header forks one human into two user records: the
    browser BFF authenticates against ``OIDC_INTERNAL_AUTHORITY`` while MCP and
    CLI clients authenticate against ``OIDC_AUTHORITY``. Nothing downstream can
    tell those identities apart, so the divergence is silent — see AUTH-001.

    Best-effort by design: the server frequently *cannot* reach the external
    hostname (that being the reason ``OIDC_INTERNAL_AUTHORITY`` exists), and an
    unverifiable check must not take the app down. A confirmed mismatch is
    logged loudly, and raises only under ``OIDC_REQUIRE_CONSISTENT_ISSUER``.
    """
    if not settings.DEVELOPMENT_ONLY_REQUIRE_AUTH:
        return
    external = settings.OIDC_AUTHORITY.rstrip("/")
    internal = settings.OIDC_INTERNAL_AUTHORITY.rstrip("/")
    if not external or not internal or internal == external:
        return

    results = await asyncio.gather(
        _discovery_issuer(internal),
        _discovery_issuer(external),
        return_exceptions=True,
    )
    issuers: list[str] = []
    for authority, result in zip((internal, external), results, strict=True):
        if isinstance(result, BaseException):
            logger.warning(
                "Could not verify that OIDC_INTERNAL_AUTHORITY and OIDC_AUTHORITY share an issuer "
                "(discovery for %s failed: %s). If the identity provider derives its issuer from "
                "the request host, each authentication path creates a separate Seizu user for the "
                "same person.",
                authority,
                result,
            )
            return
        issuers.append(result)

    internal_issuer, external_issuer = issuers
    if _normalize_issuer(internal_issuer) == _normalize_issuer(external_issuer):
        return

    message = (
        f"OIDC issuer mismatch: OIDC_INTERNAL_AUTHORITY ({internal}) advertises issuer "
        f"{internal_issuer!r} but OIDC_AUTHORITY ({external}) advertises {external_issuer!r}. "
        "Seizu identifies users by (iss, sub), so the same person becomes two user records — one "
        "per authentication path — and private reports, query history, chat threads, scheduled "
        "chats and MCP action confirmations diverge between them. Configure the identity provider "
        "to emit one stable issuer for both hostnames, then pin JWT_ISSUER to it."
    )
    if settings.OIDC_REQUIRE_CONSISTENT_ISSUER:
        raise OAuthClientError(message)
    logger.error("%s Set OIDC_REQUIRE_CONSISTENT_ISSUER=true to make this fatal.", message)


def _metadata_is_fresh() -> bool:
    return (
        _metadata_cache is not None
        and (time.monotonic() - _metadata_cached_at) < settings.OIDC_DISCOVERY_CACHE_TTL_SECONDS
    )


async def get_metadata() -> OIDCMetadata:
    """Fetch and cache the IDP's OIDC discovery document.

    The cache expires after ``OIDC_DISCOVERY_CACHE_TTL_SECONDS`` so rotated
    endpoints or JWKS recover without a process restart.
    """
    global _metadata_cache, _metadata_cached_at
    cached = _metadata_cache
    if cached is not None and _metadata_is_fresh():
        return cached
    async with _metadata_lock:
        cached = _metadata_cache
        if cached is not None and _metadata_is_fresh():
            return cached
        doc = await _fetch_discovery_document(_authority_for_discovery())

        try:
            metadata = OIDCMetadata(
                issuer=doc["issuer"],
                authorization_endpoint=doc["authorization_endpoint"],
                token_endpoint=doc["token_endpoint"],
                end_session_endpoint=doc.get("end_session_endpoint"),
                revocation_endpoint=doc.get("revocation_endpoint"),
                introspection_endpoint=doc.get("introspection_endpoint"),
                jwks_uri=doc.get("jwks_uri"),
            )
        except KeyError as exc:
            raise OAuthClientError(f"OIDC discovery document missing required field: {exc}") from exc
        expected_issuers = _expected_issuers()
        if expected_issuers and _normalize_issuer(metadata.issuer) not in expected_issuers:
            raise OAuthClientError(
                f"OIDC discovery issuer mismatch: got {metadata.issuer!r}, expected one of {sorted(expected_issuers)!r}"
            )
        _metadata_cache = metadata
        _metadata_cached_at = time.monotonic()
        return metadata


def reset_metadata_cache() -> None:
    """Clear the cached discovery document and ID-token JWKS client (tests)."""
    global _metadata_cache, _metadata_cached_at, _idtoken_jwks_client, _idtoken_jwks_uri
    _metadata_cache = None
    _metadata_cached_at = 0.0
    _idtoken_jwks_client = None
    _idtoken_jwks_uri = None


_idtoken_jwks_client: PyJWKClient | None = None
_idtoken_jwks_uri: str | None = None


def _get_idtoken_jwks_client(jwks_uri: str) -> PyJWKClient:
    """Return a cached ``PyJWKClient`` for the discovery ``jwks_uri``.

    Keyed by URI so a rotated discovery document (different ``jwks_uri``)
    transparently rebuilds the client.
    """
    global _idtoken_jwks_client, _idtoken_jwks_uri
    if _idtoken_jwks_client is None or _idtoken_jwks_uri != jwks_uri:
        _idtoken_jwks_client = PyJWKClient(jwks_uri, cache_keys=True, timeout=settings.JWKS_FETCH_TIMEOUT)
        _idtoken_jwks_uri = jwks_uri
    return _idtoken_jwks_client


async def validate_id_token(*, id_token: str, nonce: str | None) -> dict[str, Any]:
    """Validate the OIDC ID token returned by the code exchange.

    Verifies the signature against the discovery JWKS, the audience
    (``OIDC_CLIENT_ID``), the issuer (against the configured authorities, the
    same set used for discovery), and — when a ``nonce`` is supplied — that
    the token echoes it. Returns the claims, or raises ``OAuthClientError``.
    """
    metadata = await get_metadata()
    if not metadata.jwks_uri:
        raise OAuthClientError("IDP advertises no jwks_uri; cannot validate ID token")
    if not settings.OIDC_CLIENT_ID:
        raise OAuthClientError("OIDC_CLIENT_ID must be configured to validate the ID token")

    client = _get_idtoken_jwks_client(metadata.jwks_uri)
    try:
        signing_key = await asyncio.to_thread(client.get_signing_key_from_jwt, id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=settings.ALLOWED_JWT_ALGORITHMS,
            audience=settings.OIDC_CLIENT_ID,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise OAuthClientError(f"ID token validation failed: {exc}") from exc

    expected_issuers = _expected_issuers()
    token_issuer = _normalize_issuer(str(claims.get("iss", "")))
    if expected_issuers and token_issuer not in expected_issuers:
        raise OAuthClientError(
            f"ID token issuer mismatch: got {claims.get('iss')!r}, expected one of {sorted(expected_issuers)!r}"
        )

    azp = claims.get("azp")
    if azp is not None and azp != settings.OIDC_CLIENT_ID:
        raise OAuthClientError("ID token authorized party mismatch")
    aud = claims.get("aud")
    if isinstance(aud, list) and len(aud) > 1 and azp != settings.OIDC_CLIENT_ID:
        raise OAuthClientError("ID token with multiple audiences must identify this client as azp")

    if nonce is not None:
        token_nonce = claims.get("nonce")
        if not isinstance(token_nonce, str) or not secrets.compare_digest(token_nonce, nonce):
            raise OAuthClientError("ID token nonce mismatch")

    return claims


def _claim_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return []


def _validate_introspection_claims(data: dict[str, Any], metadata: OIDCMetadata) -> dict[str, Any]:
    """Validate and normalize an RFC 7662 introspection response.

    Introspection proves the token is active at the configured IDP, but Seizu
    still needs to ensure the token is intended for this client/API and carries
    the identity claims the rest of the auth stack requires.
    """
    normalized = dict(data)

    expected_issuer_claim = settings.JWT_ISS_CLAIM
    if expected_issuer_claim not in normalized:
        normalized[expected_issuer_claim] = normalized.get("iss", metadata.issuer)

    issuer = normalized.get(expected_issuer_claim) or normalized.get("iss")
    if not isinstance(issuer, str) or not issuer:
        raise OAuthClientError(f"Introspection response missing required claim: {expected_issuer_claim}")
    expected_issuers = _expected_issuers()
    if expected_issuers and _normalize_issuer(issuer) not in expected_issuers:
        raise OAuthClientError(f"Token issuer mismatch: got {issuer!r}, expected one of {sorted(expected_issuers)!r}")

    expected_audiences = {value for value in (settings.JWT_AUDIENCE, settings.OIDC_CLIENT_ID) if value}
    audiences = set(_claim_list(normalized.get("aud")))
    client_id = normalized.get("client_id")
    if expected_audiences and not audiences.intersection(expected_audiences) and client_id not in expected_audiences:
        raise OAuthClientError("Token introspection response is not intended for this client")

    subject = normalized.get(settings.JWT_SUB_CLAIM)
    if not isinstance(subject, str) or not subject:
        raise OAuthClientError(f"Introspection response missing required claim: {settings.JWT_SUB_CLAIM}")
    for claim_name in (settings.JWT_EMAIL_CLAIM, settings.JWT_USERNAME_CLAIM):
        claim_value = normalized.get(claim_name)
        if claim_value is not None and not isinstance(claim_value, str):
            raise OAuthClientError(f"Introspection response has invalid claim: {claim_name}")

    return normalized


def _coerce_token(data: Any) -> TokenResponse:
    """Convert authlib's OAuth2Token (a dict subclass) into our TokenResponse."""
    try:
        access_token = data["access_token"]
    except (KeyError, TypeError) as exc:
        raise OAuthClientError("IDP token response missing access_token") from exc
    return TokenResponse(
        access_token=access_token,
        refresh_token=data.get("refresh_token"),
        expires_in=data.get("expires_in"),
        refresh_expires_in=data.get("refresh_expires_in"),
        id_token=data.get("id_token"),
        token_type=data.get("token_type", "Bearer"),
        scope=data.get("scope"),
    )


def _build_oauth_client(*, redirect_uri: str | None = None) -> AsyncOAuth2Client:
    if not settings.OIDC_CLIENT_ID:
        raise OAuthClientError("OIDC_CLIENT_ID must be configured")
    return AsyncOAuth2Client(
        client_id=settings.OIDC_CLIENT_ID,
        client_secret=settings.OIDC_CLIENT_SECRET or None,
        token_endpoint_auth_method=settings.OIDC_TOKEN_ENDPOINT_AUTH_METHOD,
        revocation_endpoint_auth_method=settings.OIDC_REVOCATION_ENDPOINT_AUTH_METHOD,
        redirect_uri=redirect_uri,
        scope=settings.OIDC_SCOPE,
        code_challenge_method="S256",
        timeout=_OIDC_REQUEST_TIMEOUT,
    )


async def build_authorize_url(
    *,
    authorization_endpoint: str,
    state: str,
    code_verifier: str,
    redirect_uri: str,
    nonce: str | None = None,
) -> str:
    """Build a PKCE authorization URL using Authlib's URL constructor.

    ``nonce`` is added as the OIDC ``nonce`` parameter when provided; the
    callback later checks it against the ID token to bind the token response
    to this login request. ``settings.OIDC_AUTHORIZE_EXTRA_PARAMS`` are merged
    in as additional query parameters — this is how provider-specific knobs
    (e.g. Google's ``access_type=offline`` / ``prompt=consent``) reach the
    authorize request.
    """
    browser_authorize_endpoint = rewrite_to_external_origin(authorization_endpoint)
    extra_params = dict(settings.OIDC_AUTHORIZE_EXTRA_PARAMS)
    if nonce is not None:
        extra_params["nonce"] = nonce
    async with _build_oauth_client(redirect_uri=redirect_uri) as client:
        authorize_url, _ = client.create_authorization_url(
            browser_authorize_endpoint,
            state=state,
            code_verifier=code_verifier,
            **extra_params,
        )
    return authorize_url


async def build_post_logout_url(
    *,
    id_token_hint: str | None,
    post_logout_redirect_uri: str,
) -> str | None:
    """Build the browser-facing RP-initiated logout URL when available."""
    try:
        metadata = await get_metadata()
    except OAuthClientError as exc:
        logger.warning("Skipping RP-initiated logout URL — discovery unavailable: %s", exc)
        return None
    if not metadata.end_session_endpoint:
        logger.info("IDP advertises no end_session_endpoint; skipping RP-initiated logout")
        return None

    params = {
        "client_id": settings.OIDC_CLIENT_ID,
        "post_logout_redirect_uri": post_logout_redirect_uri,
    }
    if id_token_hint:
        params["id_token_hint"] = id_token_hint

    browser_end_session_endpoint = rewrite_to_external_origin(metadata.end_session_endpoint)
    return f"{browser_end_session_endpoint}?{urlencode(params)}"


async def exchange_code(*, code: str, code_verifier: str, redirect_uri: str) -> TokenResponse:
    """Exchange an authorization code (with PKCE verifier) for tokens."""
    metadata = await get_metadata()
    async with _build_oauth_client(redirect_uri=redirect_uri) as client:
        try:
            token = await client.fetch_token(
                url=metadata.token_endpoint,
                grant_type="authorization_code",
                code=code,
                code_verifier=code_verifier,
            )
        except AuthlibBaseError as exc:
            raise OAuthClientError(f"Token exchange failed: {exc}") from exc
        except httpx.HTTPError as exc:
            raise OAuthClientError(f"Token endpoint request failed: {exc}") from exc
    return _coerce_token(token)


async def refresh_tokens(*, refresh_token: str) -> TokenResponse:
    """Exchange a refresh token for a new access (and possibly refresh) token."""
    metadata = await get_metadata()
    async with _build_oauth_client() as client:
        try:
            token = await client.refresh_token(
                url=metadata.token_endpoint,
                refresh_token=refresh_token,
            )
        except AuthlibBaseError as exc:
            raise OAuthClientError(f"Token refresh failed: {exc}") from exc
        except httpx.HTTPError as exc:
            raise OAuthClientError(f"Token endpoint request failed: {exc}") from exc
    return _coerce_token(token)


async def revoke_refresh_token(*, refresh_token: str) -> None:
    """Revoke the session refresh token when the IDP supports RFC 7009.

    This is the server-to-server logout operation that matches Seizu's BFF
    session model. Authentik's ``end_session_endpoint`` is intended for a
    browser redirect, so posting to it from the backend can fail with 403
    because the request has no user's authentik browser session.
    """
    try:
        metadata = await get_metadata()
    except OAuthClientError as exc:
        logger.warning("Skipping refresh-token revocation — discovery unavailable: %s", exc)
        return
    if not metadata.revocation_endpoint:
        logger.info("IDP advertises no revocation_endpoint; skipping refresh-token revocation")
        return

    body = ""
    if settings.OIDC_REVOCATION_ENDPOINT_AUTH_METHOD == "none" and settings.OIDC_CLIENT_ID:
        body = urlencode({"client_id": settings.OIDC_CLIENT_ID})

    try:
        async with _build_oauth_client() as client:
            resp = await client.revoke_token(
                metadata.revocation_endpoint,
                token=refresh_token,
                token_type_hint="refresh_token",
                body=body,
            )
            resp.raise_for_status()
    except (AuthlibBaseError, httpx.HTTPError) as exc:
        logger.warning("refresh-token revocation failed (continuing logout): %s", exc)


async def introspect_token(*, token: str) -> dict[str, Any]:
    """Validate an opaque access token via RFC 7662 introspection.

    Returns the introspection response (a claims dict) when the token is
    active. Raises ``OAuthClientError`` if the IDP advertises no introspection
    endpoint, the request fails, or the token is inactive. This is the
    fallback path for IDPs that issue non-JWT access tokens; callers use it
    only after JWT validation has failed.
    """
    metadata = await get_metadata()
    if not metadata.introspection_endpoint:
        raise OAuthClientError("IDP advertises no introspection_endpoint")

    method = settings.OIDC_INTROSPECTION_ENDPOINT_AUTH_METHOD
    body = ""
    if method == "none" and settings.OIDC_CLIENT_ID:
        body = urlencode({"client_id": settings.OIDC_CLIENT_ID})

    try:
        async with _build_oauth_client() as client:
            resp = await client.introspect_token(
                metadata.introspection_endpoint,
                token=token,
                token_type_hint="access_token",
                body=body,
                auth=client.client_auth(method),
            )
            resp.raise_for_status()
            data = resp.json()
    except (AuthlibBaseError, httpx.HTTPError) as exc:
        raise OAuthClientError(f"Token introspection failed: {exc}") from exc

    if not isinstance(data, dict) or not data.get("active"):
        raise OAuthClientError("Token is inactive or introspection returned no claims")
    return _validate_introspection_claims(data, metadata)
