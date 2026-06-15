import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from reporting import settings
from reporting.schema.report_config import User

logger = logging.getLogger(__name__)

_jwks_client: PyJWKClient | None = None

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    user: User
    jwt_claims: dict
    permissions: frozenset[str] = field(default_factory=frozenset)


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(settings.JWKS_URL, cache_keys=True, timeout=settings.JWKS_FETCH_TIMEOUT)
    return _jwks_client


async def _get_jwt_payload(token: str) -> dict:
    """
    Get the JWT payload from a Bearer token string.

    Makes a potentially blocking HTTP call (JWKS fetch) in a thread pool.
    """
    client = _get_jwks_client()
    signing_key = await asyncio.to_thread(client.get_signing_key_from_jwt, token)

    decode_kwargs: dict = {
        "algorithms": settings.ALLOWED_JWT_ALGORITHMS,
    }
    if settings.JWT_ISSUER:
        decode_kwargs["issuer"] = settings.JWT_ISSUER
    if settings.JWT_AUDIENCE:
        decode_kwargs["audience"] = settings.JWT_AUDIENCE

    logger.debug("Decoding JWT")
    return jwt.decode(token, signing_key.key, **decode_kwargs)


async def validate_bearer_token(token: str) -> dict:
    """Validate a Bearer token and return its claims.

    Tries local JWT verification first (the common case for OIDC providers
    that issue JWT access tokens). When that fails and
    ``OIDC_ENABLE_TOKEN_INTROSPECTION`` is set, falls back to RFC 7662
    introspection — the path for IDPs (e.g. Google) that issue opaque access
    tokens. Introspection failures are surfaced as ``jwt.InvalidTokenError``
    so callers can keep a single ``jwt.PyJWTError`` handler.
    """
    try:
        return await _get_jwt_payload(token)
    except jwt.PyJWTError:
        if not settings.OIDC_ENABLE_TOKEN_INTROSPECTION:
            raise
        from reporting.services import oauth_client

        try:
            return await oauth_client.introspect_token(token=token)
        except oauth_client.OAuthClientError as exc:
            raise jwt.InvalidTokenError(f"Token introspection failed: {exc}") from exc


def get_email() -> str:
    if not settings.DEVELOPMENT_ONLY_REQUIRE_AUTH:
        email = settings.DEVELOPMENT_ONLY_AUTH_USER_EMAIL
        logger.warning(
            "Authentication is disabled",
            extra={"type": "AUDIT", "user": email},
        )
        return email
    raise RuntimeError(
        "get_email() called in auth-required mode without a request context. Use get_current_user() instead."
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> CurrentUser:
    """Validate the JWT and return a CurrentUser.

    In development mode (auth disabled) a synthetic dev user is returned.
    """
    from reporting.authnz.permissions import ALL_PERMISSIONS, resolve_permissions
    from reporting.services import report_store

    if not settings.DEVELOPMENT_ONLY_REQUIRE_AUTH:
        dev_email = settings.DEVELOPMENT_ONLY_AUTH_USER_EMAIL
        logger.warning(
            "Authentication is disabled",
            extra={"type": "AUDIT", "user": dev_email},
        )
        user = await report_store.get_or_create_user(
            sub=dev_email,
            iss="dev",
            email=dev_email,
            display_name=None,
            preferred_username=None,
        )
        return CurrentUser(
            user=user,
            jwt_claims={
                "email": dev_email,
                "display_name": None,
                "preferred_username": None,
                "token_iat": None,
                "token_exp": None,
            },
            permissions=ALL_PERMISSIONS,
        )

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        payload = await validate_bearer_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    raw_iat = payload.get("iat")
    token_iat = datetime.fromtimestamp(raw_iat, tz=UTC) if raw_iat is not None else None
    raw_exp = payload.get("exp")
    token_exp = datetime.fromtimestamp(raw_exp, tz=UTC) if raw_exp is not None else None
    sub = payload.get(settings.JWT_SUB_CLAIM)
    if not isinstance(sub, str) or not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing or invalid {settings.JWT_SUB_CLAIM} claim",
            headers={"WWW-Authenticate": "Bearer"},
        )
    iss = payload.get(settings.JWT_ISS_CLAIM)
    if not isinstance(iss, str) or not iss:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing or invalid {settings.JWT_ISS_CLAIM} claim",
            headers={"WWW-Authenticate": "Bearer"},
        )
    email = payload.get(settings.JWT_EMAIL_CLAIM)
    if email is not None and not isinstance(email, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid {settings.JWT_EMAIL_CLAIM} claim",
            headers={"WWW-Authenticate": "Bearer"},
        )
    preferred_username = payload.get(settings.JWT_USERNAME_CLAIM)
    if preferred_username is not None and not isinstance(preferred_username, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid {settings.JWT_USERNAME_CLAIM} claim",
            headers={"WWW-Authenticate": "Bearer"},
        )
    jwt_claims = {
        "email": email,
        "display_name": payload.get("name"),
        "preferred_username": preferred_username,
        "token_iat": token_iat,
        "token_exp": token_exp,
    }

    role_claim = payload.get(settings.RBAC_ROLE_CLAIM)
    user = await report_store.get_or_create_user(
        sub=sub,
        iss=iss,
        email=email,
        display_name=payload.get("name"),
        preferred_username=preferred_username,
        role=role_claim if isinstance(role_claim, str) else None,
    )

    permissions = await resolve_permissions(payload)

    return CurrentUser(user=user, jwt_claims=jwt_claims, permissions=permissions)


def require_permission(*perms: str) -> Callable:
    """Return a FastAPI dependency that checks the current user has all of the
    specified permissions.  Authentication is still enforced first.

    Usage::

        @router.get("/api/v1/reports")
        async def list_reports(
            current: CurrentUser = Depends(require_permission("reports:read")),
        ) -> ...:
    """

    async def _check(
        current: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        missing = set(perms) - current.permissions
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permissions: {', '.join(sorted(missing))}",
            )
        return current

    return _check


async def sync_user_profile(
    current: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Update mutable profile fields for an already-authenticated user.

    Should be called only from routes where a profile sync is appropriate
    (e.g. ``GET /api/v1/me``).  Delegates to the store, which skips the
    write entirely if nothing has changed.
    """
    from reporting.services import report_store

    claims = current.jwt_claims
    updated_user = await report_store.update_user_profile(
        user_id=current.user.user_id,
        email=claims["email"],
        display_name=claims.get("display_name"),
        preferred_username=claims.get("preferred_username"),
        token_iat=claims.get("token_iat"),
    )
    return CurrentUser(user=updated_user, jwt_claims=claims, permissions=current.permissions)
