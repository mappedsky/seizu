"""Temporal worker entrypoint: ``python -m reporting.temporal_worker``.

Hosts Seizu's Temporal workflows and activities. Initializes the report store
and the chat checkpointer (the activities run headless chat sessions) before
polling the task queue.
"""

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from reporting import (
    settings,
    setup_logging,  # noqa:F401
)
from reporting.temporal_workflows.activities import run_dependency_remediation_chat, run_repo_cve_chat
from reporting.temporal_workflows.cve_dependency_remediation import CveDependencyRemediationWorkflow
from reporting.temporal_workflows.cve_repo_report import CveRepoReportWorkflow
from reporting.worker_bootstrap import chat_worker_resources, install_shutdown_handlers

logger = logging.getLogger(__name__)

_shutdown_event: asyncio.Event = asyncio.Event()


def _bootstrap() -> None:
    install_shutdown_handlers(_shutdown_event, logger)


async def _run_worker() -> None:
    _bootstrap()
    async with chat_worker_resources():
        client = await Client.connect(settings.TEMPORAL_ADDRESS, namespace=settings.TEMPORAL_NAMESPACE)
        worker = Worker(
            client,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
            workflows=[CveRepoReportWorkflow, CveDependencyRemediationWorkflow],
            activities=[run_repo_cve_chat, run_dependency_remediation_chat],
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


def main() -> None:
    if settings.TEMPORAL_WORKER_ENABLED:
        asyncio.run(_run_worker())


if __name__ == "__main__":
    main()
