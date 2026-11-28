"""BFF auth routes.

Browser-only auth flow that keeps the IDP refresh token out of JavaScript:

- ``GET  /api/v1/auth/login``     — start an Authorization Code + PKCE flow
- ``GET  /api/v1/auth/callback``  — IDP redirects here; we exchange code, set cookie
- ``POST /api/v1/auth/refresh``   — silent renewal; returns a new access token
- ``POST /api/v1/auth/logout``    — clear cookie + best-effort refresh-token revocation

The session cookie itself is the entire session state; see
``reporting/services/session_cookie.py``. The browser only ever holds the
short-lived access token in memory.
"""

from __future__ import annotations

import base64
import logging
import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from reporting import settings
from reporting.services import oauth_client, oauth_state_cookie, session_cookie

logger = logging.getLogger(__name__)

router = APIRouter()


class LoginResponse(BaseModel):
    """Body returned by GET /api/v1/auth/login — SPA reads ``authorize_url`` and navigates to it."""

    authorize_url: str


class RefreshResponse(BaseModel):
    """Body returned by POST /api/v1/auth/refresh — new short-lived access token."""

    access_token: str
    expires_in: int | None
    token_type: str


class LogoutResponse(BaseModel):
    """Body returned by POST /api/v1/auth/logout."""

    post_logout_url: str | None = None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _generate_pkce_verifier() -> str:
    """Return a high-entropy PKCE code verifier."""
    return _b64url(secrets.token_bytes(64))


def _build_callback_url(request: Request) -> str:
    """Compute the redirect_uri to register with the IDP for this request.

    Uses settings.OIDC_REDIRECT_URI when set (production / explicit config);
    otherwise derives ``<scheme>://<host>/api/v1/auth/callback`` from the
    incoming request URL, which is what dev environments need.
    """
    if settings.OIDC_REDIRECT_URI:
        return settings.OIDC_REDIRECT_URI
    return str(request.url_for("auth_callback"))


# Cookie path scoped to where the cookies are actually consumed. Narrowing
# from "/" to "/api/v1/auth" keeps the cookies off every regular API call
# (query, reports, etc.) — those use Bearer auth and would otherwise drag
# the cookie along, tripping the CSRF middleware. Browser cookie-path rules:
# this prefix matches every /api/v1/auth/* path (refresh, logout, callback).
_AUTH_COOKIE_PATH = "/api/v1/auth"


async def _set_session_cookie(response: Response, payload: session_cookie.SessionPayload) -> None:
    max_age = session_cookie.compute_cookie_max_age(payload.abs_exp)
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=await session_cookie.encrypt(payload),
        max_age=max_age,
        httponly=True,
        secure=settings.TALISMAN_FORCE_HTTPS,
        samesite="strict",
        path=_AUTH_COOKIE_PATH,
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path=_AUTH_COOKIE_PATH,
        httponly=True,
        secure=settings.TALISMAN_FORCE_HTTPS,
        samesite="strict",
    )


async def _set_state_cookie(response: Response, payload: oauth_state_cookie.OAuthStatePayload) -> None:
    response.set_cookie(
        key=oauth_state_cookie.STATE_COOKIE_NAME,
        value=await oauth_state_cookie.encrypt(payload),
        max_age=oauth_state_cookie.STATE_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.TALISMAN_FORCE_HTTPS,
        # Lax is required: the IDP redirects back via a top-level cross-site
        # navigation, and Strict would suppress the cookie on that hop.
        samesite="lax",
        path=_AUTH_COOKIE_PATH,
    )


def _clear_state_cookie(response: Response) -> None:
    response.delete_cookie(
        key=oauth_state_cookie.STATE_COOKIE_NAME,
        path=_AUTH_COOKIE_PATH,
        httponly=True,
        secure=settings.TALISMAN_FORCE_HTTPS,
        samesite="lax",
    )


def _safe_return_to(value: str | None) -> str:
    """Coerce ``value`` to a same-origin path or fall back to ``/``."""
    if value and oauth_state_cookie.is_safe_return_to(value):
        return value
    return "/"


@router.get("/api/v1/auth/login", response_model=LoginResponse)
async def auth_login(
    request: Request,
    response: Response,
    return_to: Annotated[str | None, Query()] = None,
) -> LoginResponse:
    """Begin a PKCE-protected Authorization Code flow.

    Sets a short-lived ``seizu_oauth_state`` cookie carrying the PKCE
    verifier, the OAuth ``state`` value, and the validated ``return_to`` URL.
    Returns the IDP's authorize URL; the SPA top-level-navigates to it.
    """
    if not settings.OIDC_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC is not configured on this server",
        )

    try:
        metadata = await oauth_client.get_metadata()
    except oauth_client.OAuthClientError as exc:
        logger.error("OIDC discovery failed in /auth/login: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC provider unavailable",
        ) from exc

    verifier = _generate_pkce_verifier()
    state = _b64url(secrets.token_bytes(32))
    nonce = _b64url(secrets.token_bytes(32))
    safe_return_to = _safe_return_to(return_to)
    redirect_uri = _build_callback_url(request)

    state_payload = oauth_state_cookie.OAuthStatePayload(
        state=state,
        verifier=verifier,
        return_to=safe_return_to,
        exp=int(time.time()) + oauth_state_cookie.STATE_COOKIE_MAX_AGE_SECONDS,
        nonce=nonce,
    )
    await _set_state_cookie(response, state_payload)

    authorize_url = await oauth_client.build_authorize_url(
        authorization_endpoint=metadata.authorization_endpoint,
        state=state,
        code_verifier=verifier,
        redirect_uri=redirect_uri,
        nonce=nonce,
    )
    return LoginResponse(authorize_url=authorize_url)


@router.get("/api/v1/auth/callback", name="auth_callback", include_in_schema=False)
async def auth_callback(
    request: Request,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
    error_description: Annotated[str | None, Query()] = None,
    seizu_oauth_state: Annotated[str | None, Cookie()] = None,
) -> Response:
    """Handle the IDP's redirect back to us.

    Verifies ``state`` against the state cookie, exchanges ``code`` (with
    PKCE verifier) for tokens at the IDP, sets the session cookie, and 302s
    to the validated ``return_to``. The state cookie is cleared in all
    outcomes (success or error) so it can't be replayed.
    """
    if error:
        logger.warning("IDP returned error on callback: %s (%s)", error, error_description)
        response = RedirectResponse(
            url="/?auth_error=1",
            status_code=status.HTTP_302_FOUND,
        )
        _clear_state_cookie(response)
        return response

    if not code or not state:
        return _callback_error_response("Missing code or state")

    if not seizu_oauth_state:
        return _callback_error_response("Missing OAuth state cookie")

    try:
        state_payload = await oauth_state_cookie.decrypt(seizu_oauth_state)
    except oauth_state_cookie.OAuthStateCookieError as exc:
        logger.warning("Invalid state cookie on callback: %s", exc)
        return _callback_error_response("Invalid OAuth state")

    if not secrets.compare_digest(state_payload.state, state):
        logger.warning("State mismatch on OAuth callback")
        return _callback_error_response("OAuth state mismatch")

    try:
        token_response = await oauth_client.exchange_code(
            code=code,
            code_verifier=state_payload.verifier,
            redirect_uri=_build_callback_url(request),
        )
    except oauth_client.OAuthClientError as exc:
        logger.warning("Token exchange failed: %s", exc)
        return _callback_error_response("Token exchange failed")

    # Validate the ID token (signature, audience, issuer, nonce) before trusting
    # the token response. PKCE already binds the code to this client; the nonce
    # check additionally binds the ID token to *this* login request.
    if settings.OIDC_VALIDATE_ID_TOKEN:
        if not token_response.id_token:
            logger.warning("IDP token response did not include an ID token")
            return _callback_error_response("Missing ID token")
        try:
            await oauth_client.validate_id_token(
                id_token=token_response.id_token,
                nonce=state_payload.nonce,
            )
        except oauth_client.OAuthClientError as exc:
            logger.warning("ID token validation failed: %s", exc)
            return _callback_error_response("Invalid ID token")

    if not token_response.refresh_token:
        # offline_access scope wasn't honored, or the IDP refused to issue
        # a refresh token. We can't run a BFF session without one.
        logger.error("IDP did not return a refresh token; check OIDC_SCOPE includes offline_access")
        return _callback_error_response("IDP did not return a refresh token")

    now = int(time.time())
    refresh_expires_in = token_response.refresh_expires_in or settings.OIDC_REFRESH_TOKEN_FALLBACK_TTL_SECONDS
    abs_exp = now + refresh_expires_in
    session_payload = session_cookie.SessionPayload(
        refresh_token=token_response.refresh_token,
        iat=now,
        abs_exp=abs_exp,
        id_token=token_response.id_token,
    )

    response = RedirectResponse(url=state_payload.return_to, status_code=status.HTTP_302_FOUND)
    await _set_session_cookie(response, session_payload)
    _clear_state_cookie(response)
    return response


def _unauthenticated_response(detail: str, *, clear_cookie: bool) -> JSONResponse:
    """Return a 401 JSONResponse, optionally clearing the session cookie.

    Returning a Response directly bypasses the global HTTPException handler,
    which otherwise rebuilds the response and would drop our Set-Cookie.
    """
    response = JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"error": detail})
    if clear_cookie:
        _clear_session_cookie(response)
    return response


def _callback_error_response(detail: str, status_code: int = status.HTTP_400_BAD_REQUEST) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content={"error": detail})
    _clear_state_cookie(response)
    return response


@router.post("/api/v1/auth/refresh", response_model=RefreshResponse)
async def auth_refresh(
    response: Response,
    request: Request,
) -> RefreshResponse | JSONResponse:
    """Exchange the cookie's refresh token for a new access token.

    Re-issues the session cookie with a rolling Max-Age. Returns 401 (and
    clears the cookie) if the cookie is invalid or the IDP rejects the
    refresh token — that's the path where the user is logged out and must
    re-authenticate.
    """
    seizu_session = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not seizu_session:
        return _unauthenticated_response("No session", clear_cookie=False)

    try:
        session_payload = await session_cookie.decrypt(seizu_session)
    except session_cookie.SessionCookieError as exc:
        logger.info("Invalid session cookie on refresh: %s", exc)
        return _unauthenticated_response("Invalid session", clear_cookie=True)

    try:
        token_response = await oauth_client.refresh_tokens(refresh_token=session_payload.refresh_token)
    except oauth_client.OAuthClientError as exc:
        logger.info("IDP rejected refresh token: %s", exc)
        return _unauthenticated_response("Session expired", clear_cookie=True)

    new_refresh_token = token_response.refresh_token or session_payload.refresh_token
    # IDPs that rotate refresh tokens (Authentik, Okta, …) typically reset the
    # token's lifetime on each rotation. When the refresh response advertises a
    # fresh refresh_expires_in, roll the session's absolute cap forward to match
    # rather than freezing it at login — but never shorten it below the existing
    # cap on a transient/smaller value.
    new_abs_exp = session_payload.abs_exp
    if token_response.refresh_expires_in:
        new_abs_exp = max(new_abs_exp, int(time.time()) + token_response.refresh_expires_in)
    new_payload = session_cookie.SessionPayload(
        refresh_token=new_refresh_token,
        iat=session_payload.iat,
        abs_exp=new_abs_exp,
        id_token=token_response.id_token or session_payload.id_token,
    )
    await _set_session_cookie(response, new_payload)

    return RefreshResponse(
        access_token=token_response.access_token,
        expires_in=token_response.expires_in,
        token_type=token_response.token_type,
    )


@router.post("/api/v1/auth/logout", response_model=LogoutResponse)
async def auth_logout(
    response: Response,
    request: Request,
) -> LogoutResponse:
    """Drop the session cookie and (optionally) revoke the IDP refresh token.

    Always succeeds from the user's perspective. Best-effort IDP logout
    failures are logged but never block the local logout.
    """
    refresh_token: str | None = None
    id_token: str | None = None
    seizu_session = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if seizu_session:
        try:
            session_payload = await session_cookie.decrypt(seizu_session)
            refresh_token = session_payload.refresh_token
            id_token = session_payload.id_token
        except session_cookie.SessionCookieError:
            refresh_token = None

    post_logout_url = await oauth_client.build_post_logout_url(
        id_token_hint=id_token,
        post_logout_redirect_uri=str(request.url_for("spa_fallback", full_path="logged-out")),
    )

    if settings.OIDC_REVOKE_REFRESH_TOKEN_ON_LOGOUT and refresh_token:
        try:
            await oauth_client.revoke_refresh_token(refresh_token=refresh_token)
        except Exception as exc:  # noqa: BLE001 — defensive: never let logout fail
            logger.warning("IDP refresh-token revocation failed (continuing logout): %s", exc)

    _clear_session_cookie(response)
    return LogoutResponse(post_logout_url=post_logout_url)
