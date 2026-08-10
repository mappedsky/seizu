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
from reporting.services import chat_schedules, sandbox_reaper, workflow_schedules
from reporting.temporal_workflows.activities import (
    build_code_workflow_input,
    check_configured_workflow_watch,
    check_scheduled_chat_watch,
    execute_configured_activity,
    execute_configured_query,
    get_pr_ci_status,
    load_configured_workflow,
    load_scheduled_chat,
    normalize_code_workflow_output,
    record_configured_workflow_result,
    record_scheduled_chat_run_result,
    run_agent_chat_session,
    run_dependency_ci_fix,
    run_dependency_remediation,
    run_repo_cve_chat,
    run_scheduled_chat_session,
    trigger_configured_workflows,
)
from reporting.temporal_workflows.agent_chat import AgentChatWorkflow
from reporting.temporal_workflows.cartography_sync import CartographyModuleWorkflow, CartographySyncWorkflow
from reporting.temporal_workflows.configured_workflow import (
    ConfiguredWorkflow,
    ConfiguredWorkflowExecution,
    ConfiguredWorkflowWaitingSlot,
    ConfiguredWorkflowWatchPoll,
)
from reporting.temporal_workflows.cve_dependency_remediation import CveDependencyRemediationWorkflow
from reporting.temporal_workflows.cve_repo_report import CveRepoReportWorkflow
from reporting.temporal_workflows.scheduled_chat import ScheduledChatWatchPoll, ScheduledChatWorkflow
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
                ConfiguredWorkflowExecution,
                ConfiguredWorkflowWaitingSlot,
                ConfiguredWorkflowWatchPoll,
                ScheduledChatWorkflow,
                ScheduledChatWatchPoll,
                AgentChatWorkflow,
            ],
            activities=[
                load_configured_workflow,
                check_configured_workflow_watch,
                execute_configured_query,
                execute_configured_activity,
                build_code_workflow_input,
                normalize_code_workflow_output,
                record_configured_workflow_result,
                trigger_configured_workflows,
                run_repo_cve_chat,
                run_dependency_remediation,
                get_pr_ci_status,
                run_dependency_ci_fix,
                load_scheduled_chat,
                check_scheduled_chat_watch,
                run_scheduled_chat_session,
                record_scheduled_chat_run_result,
                run_agent_chat_session,
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
            # Two loops rather than one: reconciliation runs every minute or so
            # and the sandbox sweep every quarter hour, and folding the sweep
            # into the reconcile loop would tie its cadence to a setting that
            # means something else.
            background = [asyncio.create_task(_reconcile_loop()), asyncio.create_task(_reap_loop())]
            try:
                await _shutdown_event.wait()
            finally:
                for task in background:
                    task.cancel()
                await asyncio.gather(*background, return_exceptions=True)


async def _reconcile_loop() -> None:
    while not _shutdown_event.is_set():
        try:
            await workflow_schedules.reconcile_all()
        except Exception:
            logger.exception("Workflow Schedule reconciliation pass failed")
        if settings.CHAT_ENABLED and settings.CHAT_SCHEDULES_ENABLED:
            # Separate try/except: a chat-side failure must not stop workflow
            # schedules from reconciling.
            try:
                await chat_schedules.reconcile_all()
            except Exception:
                logger.exception("Scheduled chat Schedule reconciliation pass failed")
        try:
            await asyncio.wait_for(
                _shutdown_event.wait(),
                timeout=settings.WORKFLOW_RECONCILE_SECONDS,
            )
        except TimeoutError:
            pass


async def _reap_loop() -> None:
    """Sweep up suspended sandboxes no conversation is coming back for.

    Here rather than in the web app because this is one process: every gunicorn
    worker running the same account-wide sweep would multiply the provider calls
    and race each other over the same kills. A deployment that runs no Temporal
    worker therefore does not reap -- documented in the sandbox install docs
    alongside ``SANDBOX_SESSION_PERSIST=false``, which is the alternative.
    """
    if not sandbox_reaper.reaping_configured():
        return
    while not _shutdown_event.is_set():
        try:
            await sandbox_reaper.reap_abandoned_sandboxes()
        except Exception:
            logger.exception("Sandbox reap pass failed")
        try:
            await asyncio.wait_for(
                _shutdown_event.wait(),
                timeout=settings.SANDBOX_REAP_INTERVAL_SECONDS,
            )
        except TimeoutError:
            pass


def main() -> None:
    if settings.TEMPORAL_WORKER_ENABLED:
        asyncio.run(_run_worker())


if __name__ == "__main__":
    main()
