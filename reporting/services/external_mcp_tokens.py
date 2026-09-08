"""Process-local service credentials for external MCP gateways."""

import asyncio
import hashlib
import math
import os
import time
from dataclasses import dataclass

from authlib.integrations.httpx_client import AsyncOAuth2Client

from reporting.schema.external_mcp import ExternalMCPProxy


class ServiceCredentialError(RuntimeError):
    """The configured service credential could not be acquired."""


@dataclass
class _Credential:
    lock: asyncio.Lock
    token: str = ""
    renew_at: float = 0


_credentials: dict[str, _Credential] = {}


def _key(proxy: ExternalMCPProxy) -> str:
    config = proxy.client_credentials
    assert config is not None
    secret = os.environ.get(config.client_secret_env, "")
    return hashlib.sha256((config.model_dump_json() + "\0" + secret).encode()).hexdigest()


def invalidate_service_token(proxy: ExternalMCPProxy, rejected_token: str | None = None) -> None:
    if proxy.client_credentials:
        entry = _credentials.get(_key(proxy))
        if entry and (rejected_token is None or entry.token == rejected_token):
            entry.token = ""
            entry.renew_at = 0


async def service_token(proxy: ExternalMCPProxy) -> str:
    """Acquire a bearer token, coalescing concurrent requests for the same grant."""
    config = proxy.client_credentials
    assert config is not None
    entry = _credentials.setdefault(_key(proxy), _Credential(asyncio.Lock()))
    async with entry.lock:
        if entry.token and time.monotonic() < entry.renew_at:
            return entry.token
        secret = os.environ.get(config.client_secret_env, "")
        try:
            if not secret:
                raise ValueError("missing client secret")
            started = time.monotonic()
            async with AsyncOAuth2Client(
                client_id=config.client_id,
                client_secret=secret,
                scope=config.scope,
                token_endpoint_auth_method=config.token_endpoint_auth_method,
                timeout=proxy.connect_timeout_seconds,
                follow_redirects=False,
            ) as client:
                token = await client.fetch_token(
                    config.token_url,
                    grant_type="client_credentials",
                    **({"audience": config.audience} if config.audience else {}),
                )
            value = token.get("access_token")
            lifetime = float(token.get("expires_in", 0))
            if (
                not isinstance(value, str)
                or not value.strip()
                or any(c.isspace() for c in value)
                or str(token.get("token_type", "")).casefold() != "bearer"
                or not math.isfinite(lifetime)
                or lifetime <= 0
            ):
                raise ValueError("invalid token response")
            renew_at = started + lifetime - min(30, lifetime * 0.1)
            if renew_at <= time.monotonic():
                raise ValueError("token expired during acquisition")
        except Exception:
            entry.token = ""
            entry.renew_at = 0
            raise ServiceCredentialError(f"Service authentication for proxy '{proxy.name}' failed") from None
        entry.token = value
        entry.renew_at = renew_at
        return value
