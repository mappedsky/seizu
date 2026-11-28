"""Durable, owner-scoped observations of per-user gateway access."""

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

from mcp.types import ElicitRequestURLParams

from reporting.authnz import CurrentUser
from reporting.schema.external_mcp import (
    ConnectionStatus,
    ExternalMCPAuthMode,
    ExternalMCPConnection,
    ExternalMCPElicitation,
    ExternalMCPProxy,
)
from reporting.services import report_store, token_vault
from reporting.services.external_mcp_elicitation import MAX_ELICITATIONS, safe_elicitation

logger = logging.getLogger(__name__)


def fingerprint(proxy: ExternalMCPProxy) -> str:
    return hashlib.sha256(json.dumps(proxy.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


async def observe(
    proxy: ExternalMCPProxy,
    user: CurrentUser,
    status: ConnectionStatus,
    *,
    elicitations: list[ExternalMCPElicitation] | None = None,
) -> None:
    """Record bounded status and recovery data; storage failure never repeats an operation."""
    if not proxy.per_user:
        return
    observation = {
        "user_id": user.user.user_id,
        "proxy_name": proxy.name,
        "fingerprint": fingerprint(proxy),
        "status": status,
        "observed_at": datetime.now(UTC).isoformat(),
        "error_code": None if status == "connected" else status,
        "elicitations_json": json.dumps([item.model_dump() for item in (elicitations or [])[:MAX_ELICITATIONS]])
        if status == "interaction_required"
        else None,
    }
    try:
        await report_store.record_external_mcp_connection(observation)
    except Exception:
        logger.error("Could not persist external MCP connection status for proxy %s", proxy.name)


async def list_connections(proxies: list[ExternalMCPProxy], user: CurrentUser) -> list[ExternalMCPConnection]:
    observations = await report_store.list_external_mcp_connections(user.user.user_id)
    by_key = {(row["proxy_name"], row["fingerprint"]): row for row in observations}
    granted = await _granted(user)
    result = []
    for proxy in proxies:
        if not proxy.enabled or not proxy.per_user:
            continue
        row = by_key.get((proxy.name, fingerprint(proxy)), {})
        result.append(
            ExternalMCPConnection(
                proxy_name=proxy.name,
                status=row.get("status", "unknown"),
                observed_at=row.get("observed_at"),
                error_code=row.get("error_code"),
                reauthorize_url=proxy.user_authorization.reauthorize_url if proxy.user_authorization else None,
                elicitations=_recovery_links(proxy, row),
                connectable=proxy.auth_mode == ExternalMCPAuthMode.USER_OAUTH,
                has_grant=(proxy.name, proxy.url) in granted,
            )
        )
    return result


async def _granted(user: CurrentUser) -> set[tuple[str, str]]:
    """``(proxy name, proxy URL)`` pairs this user holds a grant for; empty if the vault is unreadable."""
    try:
        return {(grant.proxy_name, grant.resource) for grant in await token_vault.list_grants(user.user.user_id)}
    except Exception:
        logger.error("Could not list delegated grants for the connections page")
        return set()


def _recovery_links(proxy: ExternalMCPProxy, row: dict) -> list[ExternalMCPElicitation]:
    """Revalidate stored URLs and hide them after one hour; status remains visible."""
    try:
        if row.get("status") != "interaction_required":
            return []
        observed = datetime.fromisoformat(row["observed_at"])
        if datetime.now(UTC) - observed > timedelta(hours=1):
            return []
        values = json.loads(row.get("elicitations_json") or "[]")
        if not isinstance(values, list) or len(values) > MAX_ELICITATIONS:
            return []
        return [
            safe for value in values if (safe := safe_elicitation(proxy, ElicitRequestURLParams(**value))) is not None
        ]
    except (KeyError, TypeError, ValueError):
        return []
