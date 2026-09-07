"""Durable, owner-scoped observations of per-user gateway access."""

import hashlib
import json
import logging
from datetime import UTC, datetime

from reporting.authnz import CurrentUser
from reporting.schema.external_mcp import ConnectionStatus, ExternalMCPConnection, ExternalMCPProxy
from reporting.services import report_store

logger = logging.getLogger(__name__)


def fingerprint(proxy: ExternalMCPProxy) -> str:
    return hashlib.sha256(json.dumps(proxy.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


async def observe(proxy: ExternalMCPProxy, user: CurrentUser, status: ConnectionStatus) -> None:
    """Record only a bounded outcome; storage failure never repeats an operation."""
    if not proxy.user_authorization:
        return
    observation = {
        "user_id": user.user.user_id,
        "proxy_name": proxy.name,
        "fingerprint": fingerprint(proxy),
        "status": status,
        "observed_at": datetime.now(UTC).isoformat(),
        "error_code": None if status == "connected" else status,
    }
    try:
        await report_store.record_external_mcp_connection(observation)
    except Exception:
        logger.error("Could not persist external MCP connection status for proxy %s", proxy.name)


async def list_connections(proxies: list[ExternalMCPProxy], user: CurrentUser) -> list[ExternalMCPConnection]:
    observations = await report_store.list_external_mcp_connections(user.user.user_id)
    by_key = {(row["proxy_name"], row["fingerprint"]): row for row in observations}
    result = []
    for proxy in proxies:
        if not proxy.enabled or not proxy.user_authorization:
            continue
        row = by_key.get((proxy.name, fingerprint(proxy)), {})
        result.append(
            ExternalMCPConnection(
                proxy_name=proxy.name,
                status=row.get("status", "unknown"),
                observed_at=row.get("observed_at"),
                error_code=row.get("error_code"),
                reauthorize_url=proxy.user_authorization.reauthorize_url,
            )
        )
    return result
