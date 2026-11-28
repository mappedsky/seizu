"""Configuration models for MCP servers reached through an identity proxy."""

import json
import re
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ExternalMCPTransport(StrEnum):
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"


class ExternalMCPAuthMode(StrEnum):
    BEARER = "bearer"
    HEADER_DELEGATION = "header_delegation"
    M2M_JWT = "m2m_jwt"
    # Each user authorizes Seizu through the gateway's MCP OAuth flow, and every
    # request carries that user's own access token (AGT-059).
    USER_OAUTH = "user_oauth"


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


def _endpoint(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("must be an http(s) URL without embedded credentials or a fragment")
    return value


class ExternalMCPClientCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_url: str
    client_id: str = Field(min_length=1)
    client_secret_env: str
    scope: str | None = None
    audience: str | None = None
    token_endpoint_auth_method: Literal["client_secret_basic", "client_secret_post"] = "client_secret_basic"

    _valid_url = field_validator("token_url")(_endpoint)

    @field_validator("client_secret_env")
    @classmethod
    def valid_secret_env(cls, value: str) -> str:
        if not _ENV_RE.fullmatch(value):
            raise ValueError("must be an uppercase environment-variable name")
        return value


class ExternalMCPOAuthClient(BaseModel):
    """How Seizu identifies itself to a ``user_oauth`` gateway's authorization server.

    Without ``client_id`` Seizu registers itself dynamically (RFC 7591) and
    keeps the registration encrypted (STO-016). With it, the client was
    registered out of band; ``client_secret_env`` names its secret, and omitting
    it makes Seizu a public client that relies on PKCE alone.
    """

    model_config = ConfigDict(extra="forbid")

    client_id: str | None = Field(default=None, min_length=1)
    client_secret_env: str | None = None
    scope: str | None = None
    token_endpoint_auth_method: Literal["client_secret_basic", "client_secret_post"] = "client_secret_basic"

    @field_validator("client_secret_env")
    @classmethod
    def valid_secret_env(cls, value: str | None) -> str | None:
        if value is not None and not _ENV_RE.fullmatch(value):
            raise ValueError("must be an uppercase environment-variable name")
        return value

    @model_validator(mode="after")
    def secret_needs_client(self) -> "ExternalMCPOAuthClient":
        if self.client_secret_env and not self.client_id:
            raise ValueError("client_secret_env is only meaningful with a pre-registered client_id")
        return self


class ExternalMCPUserAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reauthorize_url: str

    _valid_url = field_validator("reauthorize_url")(_endpoint)


ConnectionStatus = Literal[
    "unknown",
    "connected",
    "authorization_required",
    "interaction_required",
    "service_authentication_failed",
    "permission_denied",
    "unavailable",
]


class ExternalMCPElicitation(BaseModel):
    elicitation_id: str | None = Field(default=None, max_length=256)
    url: str = Field(max_length=4096)
    message: str = Field(max_length=1000)


class ExternalMCPConnection(BaseModel):
    proxy_name: str
    status: ConnectionStatus = "unknown"
    observed_at: str | None = None
    error_code: ConnectionStatus | None = None
    reauthorize_url: str | None = None
    elicitations: list[ExternalMCPElicitation] = Field(default_factory=list)
    # True for user_oauth gateways, which the user connects from Seizu itself.
    connectable: bool = False
    # Whether Seizu holds this user's grant for the gateway; says nothing about
    # whether the gateway still accepts it, which only ``status`` reports.
    has_grant: bool = False


class ExternalMCPAuthorizationStart(BaseModel):
    authorization_url: str


class ExternalMCPConnectionsResponse(BaseModel):
    connections: list[ExternalMCPConnection]


class ExternalMCPElicitationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    form: bool = False
    url: bool = False


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
    protocol_mode: Literal["auto", "legacy"] = "auto"
    auth_mode: ExternalMCPAuthMode = ExternalMCPAuthMode.HEADER_DELEGATION
    header_mappings: dict[ExternalMCPHeaderSource, str] = Field(default_factory=dict)
    token_env: str | None = None
    client_credentials: ExternalMCPClientCredentials | None = None
    oauth: ExternalMCPOAuthClient | None = None
    user_authorization: ExternalMCPUserAuthorization | None = None
    elicitation: ExternalMCPElicitationOptions = Field(default_factory=ExternalMCPElicitationOptions)
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

    @property
    def per_user(self) -> bool:
        """Whether access is each user's own, so it has a Chat Connections status."""
        return self.user_authorization is not None or self.auth_mode == ExternalMCPAuthMode.USER_OAUTH

    @model_validator(mode="after")
    def auth_requirements(self) -> "ExternalMCPProxy":
        if self.auth_mode == ExternalMCPAuthMode.USER_OAUTH:
            if self.token_env or self.client_credentials:
                raise ValueError("user_oauth sends the user's own token and takes no service credential")
            if self.header_mappings:
                raise ValueError("user_oauth identifies the user by their token, so it takes no header mappings")
            return self
        if self.oauth is not None:
            raise ValueError("oauth configures a user_oauth client and requires auth_mode user_oauth")
        mapped = set(self.header_mappings)
        mapped_headers = {header.casefold() for header in self.header_mappings.values()}
        if self.auth_mode == ExternalMCPAuthMode.BEARER and not self.token_env:
            if ExternalMCPHeaderSource.ACCESS_TOKEN not in mapped:
                raise ValueError("bearer auth requires token_env or an access_token header mapping")
        if self.client_credentials and (self.auth_mode != ExternalMCPAuthMode.M2M_JWT or self.token_env):
            raise ValueError("client_credentials requires m2m_jwt and is mutually exclusive with token_env")
        if self.auth_mode == ExternalMCPAuthMode.M2M_JWT and not (self.token_env or self.client_credentials):
            raise ValueError("m2m_jwt auth requires token_env or client_credentials")
        if (self.token_env or self.client_credentials) and "authorization" in mapped_headers:
            raise ValueError("Authorization cannot be a header mapping when service credentials supply it")
        if self.user_authorization:
            if self.auth_mode == ExternalMCPAuthMode.BEARER or ExternalMCPHeaderSource.ACCESS_TOKEN in mapped:
                raise ValueError("user_authorization requires M2M or trusted header delegation without browser tokens")
            if self.auth_mode == ExternalMCPAuthMode.HEADER_DELEGATION and (
                self.token_env or "authorization" in mapped_headers
            ):
                raise ValueError("per-user header_delegation uses mesh authentication without Authorization")
        if self.user_authorization or self.auth_mode == ExternalMCPAuthMode.M2M_JWT:
            for source, header in self.header_mappings.items():
                if header.casefold() in {"x-target-user-id", "x-target-user-issuer"} and source in {
                    ExternalMCPHeaderSource.USER_ID,
                    ExternalMCPHeaderSource.ACCESS_TOKEN,
                }:
                    raise ValueError(f"{header} may only carry an identity-provider claim")
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
