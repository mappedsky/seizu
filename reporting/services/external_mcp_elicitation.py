"""Negotiation and bounded URL recovery for detached MCP clients (AGT-048/049)."""

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from mcp import ClientSession as SDKClientSession
from mcp.client._probe import negotiate_auto
from mcp.shared.exceptions import MCPError
from mcp.types import ClientCapabilities, ElicitationRequiredErrorData, ElicitRequestURLParams
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS
from pydantic import ValidationError

from reporting.schema.external_mcp import ExternalMCPElicitation, ExternalMCPProxy

MAX_ELICITATIONS = 8


class ClientSession(SDKClientSession):
    """Constrain SDK negotiation fallback and advertise only URL elicitation."""

    _discovery_guard: Callable[[], int | None] | None = None

    async def negotiate(self, guard: Callable[[], int | None]) -> None:
        """Negotiate with the SDK policy, without downgrading authentication failures."""
        self._discovery_guard = guard
        try:
            await negotiate_auto(self)
        finally:
            self._discovery_guard = None

    async def send_discover(self, version: str) -> dict[str, Any]:
        try:
            result = await super().send_discover(version)
            supported = result.get("supportedVersions")
            if isinstance(supported, list) and not any(
                version in supported for version in (*HANDSHAKE_PROTOCOL_VERSIONS, *MODERN_PROTOCOL_VERSIONS)
            ):
                raise RuntimeError("External MCP protocol versions are incompatible")
            return result
        except MCPError as exc:
            status = self._discovery_guard() if self._discovery_guard else None
            # Only protocol-compatibility errors may reach the SDK fallback.
            if exc.code not in {-32600, -32601, -32022} and not (exc.code == -32603 and status in {400, 405}):
                raise RuntimeError("External MCP discovery failed") from exc
            raise
        finally:
            if self._discovery_guard is not None:
                self._discovery_guard()

    def _build_capabilities(self, version: str) -> ClientCapabilities:
        capabilities = super()._build_capabilities(version)
        if capabilities.elicitation is not None:
            capabilities.elicitation.form = None
        return capabilities


def safe_elicitation(proxy: ExternalMCPProxy, params: ElicitRequestURLParams) -> ExternalMCPElicitation | None:
    """Allow only credential-free URLs on the configured recovery page's origin."""
    if not proxy.user_authorization:
        return None
    try:
        url = params.url
        if any(ord(char) <= 32 or ord(char) == 127 for char in url) or "\\" in url:
            return None
        target = urlsplit(url)
        allowed = urlsplit(proxy.user_authorization.reauthorize_url)
        target_port = target.port if target.port is not None else (443 if target.scheme == "https" else 80)
        allowed_port = allowed.port if allowed.port is not None else (443 if allowed.scheme == "https" else 80)
        if (
            target.scheme not in {"https", "http"}
            or not target.hostname
            or target.username is not None
            or target.password is not None
            or target.fragment
            or (target.scheme, target.hostname, target_port) != (allowed.scheme, allowed.hostname, allowed_port)
        ):
            return None
        return ExternalMCPElicitation(elicitation_id=params.elicitation_id, url=url, message=params.message[:1000])
    except (ValueError, ValidationError):
        return None


def required_elicitations(exc: BaseException) -> list[ElicitRequestURLParams] | None:
    """Read the 2025-11-25 protocol error, including transport exception groups."""
    if isinstance(exc, MCPError) and exc.code == -32042:
        try:
            data: Any = exc.data
            if not isinstance(data, dict) or not isinstance(data.get("elicitations"), list):
                return []
            if not 1 <= len(data["elicitations"]) <= MAX_ELICITATIONS:
                return []
            parsed = ElicitationRequiredErrorData.model_validate(data).elicitations
            return parsed if all(item.elicitation_id for item in parsed) else []
        except ValidationError:
            return []
    if isinstance(exc, BaseExceptionGroup):
        result = None
        for child in exc.exceptions:
            items = required_elicitations(child)
            if items is not None:
                result = (result or []) + items
        return result[:MAX_ELICITATIONS] if result is not None else None
    return None
