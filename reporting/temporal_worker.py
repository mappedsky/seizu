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
    scheduled_query_modules,
    settings,
    setup_logging,  # noqa:F401
)
from reporting.services import workflow_schedules
from reporting.temporal_workflows.activities import (
    build_code_workflow_input,
    execute_configured_activity,
    execute_configured_query,
    get_pr_ci_status,
    load_configured_workflow,
    normalize_code_workflow_output,
    record_configured_workflow_result,
    run_dependency_ci_fix,
    run_dependency_remediation,
    run_repo_cve_chat,
)
from reporting.temporal_workflows.cartography_sync import CartographyModuleWorkflow, CartographySyncWorkflow
from reporting.temporal_workflows.configured_workflow import ConfiguredWorkflow
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
        await scheduled_query_modules.load_modules()
        client = await Client.connect(settings.TEMPORAL_ADDRESS, namespace=settings.TEMPORAL_NAMESPACE)
        worker = Worker(
            client,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
            workflows=[
                CveRepoReportWorkflow,
                CveDependencyRemediationWorkflow,
                CartographySyncWorkflow,
                CartographyModuleWorkflow,
                ConfiguredWorkflow,
            ],
            activities=[
                load_configured_workflow,
                execute_configured_query,
                execute_configured_activity,
                build_code_workflow_input,
                normalize_code_workflow_output,
                record_configured_workflow_result,
                run_repo_cve_chat,
                run_dependency_remediation,
                get_pr_ci_status,
                run_dependency_ci_fix,
            ],
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
            reconcile_task = asyncio.create_task(_reconcile_loop())
            try:
                await _shutdown_event.wait()
            finally:
                reconcile_task.cancel()
                await asyncio.gather(reconcile_task, return_exceptions=True)


async def _reconcile_loop() -> None:
    while not _shutdown_event.is_set():
        try:
            await workflow_schedules.reconcile_all()
        except Exception:
            logger.exception("Workflow Schedule reconciliation pass failed")
        try:
            await asyncio.wait_for(
                _shutdown_event.wait(),
                timeout=settings.WORKFLOW_RECONCILE_SECONDS,
            )
        except TimeoutError:
            pass


def main() -> None:
    if settings.TEMPORAL_WORKER_ENABLED:
        asyncio.run(_run_worker())


if __name__ == "__main__":
    main()
