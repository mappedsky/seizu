"""Temporal worker entrypoint: ``python -m reporting.temporal_worker``.

Hosts Seizu's Temporal workflows and activities. Initializes the report store
and the chat checkpointer (the activities run headless chat sessions) before
polling the task queue.
"""

import asyncio
import logging
import signal
from typing import Any

from temporalio.client import Client
from temporalio.worker import Worker

from reporting import (
    settings,
    setup_logging,  # noqa:F401
)
from reporting.services import report_store
from reporting.services.chat_graph import close_chat_checkpoints, initialize_chat_checkpoints
from reporting.temporal_workflows.activities import run_repo_cve_chat
from reporting.temporal_workflows.cve_repo_report import CveRepoReportWorkflow

logger = logging.getLogger(__name__)

_shutdown_event: asyncio.Event = asyncio.Event()


def _bootstrap() -> None:
    def finalizer(sig: int, frame: Any) -> None:
        logger.info("SIGTERM caught, shutting down")
        _shutdown_event.set()

    signal.signal(signal.SIGTERM, finalizer)


async def _run_worker() -> None:
    _bootstrap()
    should_init = settings.DYNAMODB_CREATE_TABLE or (settings.REPORT_STORE_BACKEND == "sqlmodel")
    if should_init:
        await report_store.initialize()
    await initialize_chat_checkpoints()
    try:
        client = await Client.connect(settings.TEMPORAL_ADDRESS, namespace=settings.TEMPORAL_NAMESPACE)
        worker = Worker(
            client,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
            workflows=[CveRepoReportWorkflow],
            activities=[run_repo_cve_chat],
        )
        logger.info(
            "Temporal worker started",
            extra={
                "address": settings.TEMPORAL_ADDRESS,
                "namespace": settings.TEMPORAL_NAMESPACE,
                "task_queue": settings.TEMPORAL_TASK_QUEUE,
            },
        )
        async with worker:
            await _shutdown_event.wait()
    finally:
        await close_chat_checkpoints()


def main() -> None:
    if settings.TEMPORAL_WORKER_ENABLED:
        asyncio.run(_run_worker())


if __name__ == "__main__":
    main()
