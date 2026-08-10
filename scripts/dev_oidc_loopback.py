"""Dev-only loopback forwarder that makes `localhost:9000` reach Authentik.

Authentik derives the OIDC issuer from the request `Host` header, so the
backend has to reach it under the same name the browser, MCP clients and the
CLI use, or the same person becomes two Seizu users -- see AUTH-001 in
docs/root/dev/decisions/authentication.md. This listens on the container's
loopback and forwards to the IDP's real address, which is split-horizon DNS in
miniature: one name and port, resolved differently inside than outside.

It runs *inside* the backend container rather than as a sidecar sharing its
network namespace, because a sidecar survives `up --force-recreate seizu`
still reporting healthy while attached to the dead namespace, leaving the new
backend with nothing on `localhost:9000` (AUTH-002). Here it lives and dies
with the process it serves.

Configured by `DEV_OIDC_LOOPBACK_TARGET` (`host:port`); exits quietly when
unset, so it is inert outside the compose `auth` profile. Never enabled in the
production image.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

logging.basicConfig(format="dev-oidc-loopback: %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

_DEFAULT_LISTEN_PORT = 9000


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


async def _handle(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    target_host: str,
    target_port: int,
) -> None:
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(target_host, target_port)
    except OSError as exc:
        logger.warning("cannot reach %s:%s (%s)", target_host, target_port, exc)
        client_writer.close()
        return
    await asyncio.gather(
        _pump(client_reader, upstream_writer),
        _pump(upstream_reader, client_writer),
        return_exceptions=True,
    )


async def main() -> int:
    target = os.environ.get("DEV_OIDC_LOOPBACK_TARGET", "").strip()
    if not target:
        return 0
    host, _, port = target.rpartition(":")
    if not host or not port.isdigit():
        logger.error("DEV_OIDC_LOOPBACK_TARGET must be host:port, got %r", target)
        return 1
    listen_port = int(os.environ.get("DEV_OIDC_LOOPBACK_PORT", _DEFAULT_LISTEN_PORT))

    # Loopback only -- `localhost` resolves to ::1 first in the container, so
    # both families have to be bound or half the lookups get connection-refused.
    server = await asyncio.start_server(
        lambda r, w: _handle(r, w, host, int(port)),
        host=["127.0.0.1", "::1"],
        port=listen_port,
    )
    logger.info("localhost:%s -> %s", listen_port, target)
    async with server:
        await server.serve_forever()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
