"""Configuration models for MCP servers reached through an identity proxy."""

import json
import re
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ExternalMCPTransport(StrEnum):
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"


class ExternalMCPAuthMode(StrEnum):
    BEARER = "bearer"
    HEADER_DELEGATION = "header_delegation"
    M2M_JWT = "m2m_jwt"


class ExternalMCPHeaderSource(StrEnum):
    USER_ID = "user_id"
    SUBJECT = "subject"
    ISSUER = "issuer"
    EMAIL = "email"
    DISPLAY_NAME = "display_name"
    PREFERRED_USERNAME = "preferred_username"
    ACCESS_TOKEN = "access_token"


_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
# RFC 9110 token, used for field names by RFC 9110 section 5.1.
_HEADER_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class ExternalMCPProxy(BaseModel):
    """One operator-configured external MCP proxy.

    Secrets are referenced by environment-variable name rather than embedded in
    the JSON configuration, keeping settings dumps and Compose interpolation
    from turning a credential into ordinary configuration data.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    # Portable Agent Plugin mcp.json endpoints represented by this proxy. The
    # proxy URL remains the only address Seizu connects to.
    upstream_urls: list[str] = Field(default_factory=list)
    transport: ExternalMCPTransport = ExternalMCPTransport.SSE
    auth_mode: ExternalMCPAuthMode = ExternalMCPAuthMode.HEADER_DELEGATION
    header_mappings: dict[ExternalMCPHeaderSource, str] = Field(default_factory=dict)
    token_env: str | None = None
    require_confirmation: bool = True
    enabled: bool = True
    connect_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    read_timeout_seconds: float = Field(default=300.0, gt=0, le=3600)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not _NAME_RE.fullmatch(value) or "__" in value:
            raise ValueError("must start with a lowercase letter and contain only lowercase letters, digits, _ or -")
        return value

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("must be an http(s) URL without embedded credentials")
        return value

    @field_validator("upstream_urls")
    @classmethod
    def valid_upstream_urls(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("upstream_urls entries must be unique")
        for value in values:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError("upstream_urls entries must be http(s) URLs without embedded credentials")
        return values

    @field_validator("token_env")
    @classmethod
    def valid_token_env(cls, value: str | None) -> str | None:
        if value is not None and not _ENV_RE.fullmatch(value):
            raise ValueError("must be an uppercase environment-variable name")
        return value

    @field_validator("header_mappings")
    @classmethod
    def valid_header_names(cls, value: dict[ExternalMCPHeaderSource, str]) -> dict[ExternalMCPHeaderSource, str]:
        seen: set[str] = set()
        for header in value.values():
            if not _HEADER_RE.fullmatch(header):
                raise ValueError(f"invalid HTTP header name: {header!r}")
            folded = header.casefold()
            if folded in seen:
                raise ValueError(f"multiple identity values map to the same header: {header}")
            seen.add(folded)
        return value

    @model_validator(mode="after")
    def auth_requirements(self) -> "ExternalMCPProxy":
        mapped = set(self.header_mappings)
        mapped_headers = {header.casefold() for header in self.header_mappings.values()}
        if self.auth_mode == ExternalMCPAuthMode.BEARER and not self.token_env:
            if ExternalMCPHeaderSource.ACCESS_TOKEN not in mapped:
                raise ValueError("bearer auth requires token_env or an access_token header mapping")
        if self.auth_mode == ExternalMCPAuthMode.M2M_JWT and not self.token_env:
            raise ValueError("m2m_jwt auth requires token_env")
        if self.token_env and "authorization" in mapped_headers:
            raise ValueError("Authorization cannot be a header mapping when token_env supplies it")
        return self


def parse_external_mcp_proxies(raw: str) -> list[ExternalMCPProxy]:
    """Parse ``MCP_EXTERNAL_PROXIES`` and reject ambiguous proxy names."""
    if not raw.strip():
        return []
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"MCP_EXTERNAL_PROXIES is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise RuntimeError("MCP_EXTERNAL_PROXIES must be a JSON array")
    proxies = [ExternalMCPProxy.model_validate(item) for item in data]
    names = [proxy.name for proxy in proxies]
    if len(names) != len(set(names)):
        raise RuntimeError("MCP_EXTERNAL_PROXIES contains duplicate proxy names")
    return proxies
