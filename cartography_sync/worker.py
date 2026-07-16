"""Thin Temporal activity worker for the cartography sync image.

``python -m cartography_sync.worker``. Runs inside the dedicated cartography
image (upstream cartography + temporalio), so it reads plain env vars instead
of ``reporting.settings`` and registers activities only — the
CartographySyncWorkflow itself runs in the main seizu-temporal-worker and
dispatches here cross-queue.

Env vars:
- ``TEMPORAL_ADDRESS`` (default ``localhost:7233``)
- ``TEMPORAL_NAMESPACE`` (default ``default``)
- ``CARTOGRAPHY_TASK_QUEUE`` (default ``seizu-cartography``)
- ``CARTOGRAPHY_ENABLED_MODULES`` (worker-side module allowlist; empty → all)
- ``CARTOGRAPHY_NEO4J_URI`` (required; e.g. ``bolt://neo4j:7687``)
- ``CARTOGRAPHY_NEO4J_USER`` / ``NEO4J_PASSWORD`` (optional Neo4j auth)
- ``CARTOGRAPHY_BIN`` (default ``cartography``)
- intel-module credentials per the registry (``GITHUB_TOKEN``, …)
"""

import asyncio
import logging
import os
import signal

from temporalio.client import Client
from temporalio.worker import Worker

from cartography_sync.activities import run_cartography_module

logger = logging.getLogger(__name__)


def _install_shutdown_handlers(shutdown_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def _request_shutdown(sig: signal.Signals) -> None:
        logger.info("Received %s; shutting down", sig.name)
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_shutdown, sig)


async def _run_worker() -> None:
    if not os.environ.get("CARTOGRAPHY_NEO4J_URI"):
        raise SystemExit("CARTOGRAPHY_NEO4J_URI must be set")
    shutdown_event = asyncio.Event()
    _install_shutdown_handlers(shutdown_event)
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    task_queue = os.environ.get("CARTOGRAPHY_TASK_QUEUE", "seizu-cartography")
    client = await Client.connect(address, namespace=namespace)
    worker = Worker(client, task_queue=task_queue, activities=[run_cartography_module])
    logger.info(
        "Cartography sync worker started: address=%s namespace=%s task_queue=%s enabled_modules=%s",
        address,
        namespace,
        task_queue,
        os.environ.get("CARTOGRAPHY_ENABLED_MODULES") or "(all)",
    )
    async with worker:
        await shutdown_event.wait()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    asyncio.run(_run_worker())


if __name__ == "__main__":
    main()
