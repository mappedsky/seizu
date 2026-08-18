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
from reporting.services import chat_schedules, session_reaper_schedule, telemetry, workflow_schedules
from reporting.temporal_workflows.activities import (
    build_code_workflow_input,
    check_configured_workflow_watch,
    check_scheduled_chat_watch,
    execute_configured_activity,
    execute_configured_query,
    finalize_chat_turn,
    get_pr_ci_status,
    load_configured_workflow,
    load_scheduled_chat,
    normalize_code_workflow_output,
    reap_idle_sessions,
    record_configured_workflow_result,
    record_scheduled_chat_run_result,
    run_agent_chat_session,
    run_chat_turn,
    run_chat_worker_step,
    run_dependency_ci_fix,
    run_dependency_remediation,
    run_repo_cve_chat,
    run_scheduled_chat_session,
    trigger_configured_workflows,
)
from reporting.temporal_workflows.agent_chat import AgentChatWorkflow
from reporting.temporal_workflows.cartography_sync import CartographyModuleWorkflow, CartographySyncWorkflow
from reporting.temporal_workflows.chat_step_fanout import ChatStepFanoutWorkflow
from reporting.temporal_workflows.chat_turn import ChatTurnWorkflow
from reporting.temporal_workflows.configured_workflow import (
    ConfiguredWorkflow,
    ConfiguredWorkflowExecution,
    ConfiguredWorkflowWaitingSlot,
    ConfiguredWorkflowWatchPoll,
)
from reporting.temporal_workflows.cve_dependency_remediation import CveDependencyRemediationWorkflow
from reporting.temporal_workflows.cve_repo_report import CveRepoReportWorkflow
from reporting.temporal_workflows.scheduled_chat import ScheduledChatWatchPoll, ScheduledChatWorkflow
from reporting.temporal_workflows.session_reap import SessionReapWorkflow
from reporting.worker_bootstrap import chat_worker_resources, install_shutdown_handlers

logger = logging.getLogger(__name__)

_shutdown_event: asyncio.Event = asyncio.Event()


def _bootstrap() -> None:
    install_shutdown_handlers(_shutdown_event, logger)


async def _run_worker() -> None:
    _bootstrap()
    async with chat_worker_resources():
        await scheduled_query_modules.load_modules()
        telemetry.configure()
        client = await Client.connect(
            settings.TEMPORAL_ADDRESS,
            namespace=settings.TEMPORAL_NAMESPACE,
            interceptors=telemetry.temporal_interceptors(),
        )
        worker = Worker(
            client,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
            # The cluster-wide bound on concurrent work, and so on distributed
            # chat plan steps (AGT-018): per-turn limits stop one conversation
            # fanning out too wide, slots stop many conversations doing it at
            # once. Temporal queues the overflow rather than dropping it.
            max_concurrent_activities=max(1, settings.TEMPORAL_MAX_CONCURRENT_ACTIVITIES),
            workflows=[
                CveRepoReportWorkflow,
                CveDependencyRemediationWorkflow,
                CartographySyncWorkflow,
                CartographyModuleWorkflow,
                ConfiguredWorkflow,
                ConfiguredWorkflowExecution,
                ConfiguredWorkflowWaitingSlot,
                ConfiguredWorkflowWatchPoll,
                ChatTurnWorkflow,
                ChatStepFanoutWorkflow,
                ScheduledChatWorkflow,
                ScheduledChatWatchPoll,
                AgentChatWorkflow,
                SessionReapWorkflow,
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
                finalize_chat_turn,
                run_chat_turn,
                run_chat_worker_step,
                run_scheduled_chat_session,
                record_scheduled_chat_run_result,
                run_agent_chat_session,
                reap_idle_sessions,
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
            # A Temporal Schedule, not a task in this process: worker replicas
            # are ordinary, and a local timer in each of them would run the
            # sweep N times over and race over the same deletions. Reconciling
            # it from every replica is safe -- the id is fixed and the call is
            # idempotent.
            await session_reaper_schedule.reconcile()
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


def main() -> None:
    if settings.TEMPORAL_WORKER_ENABLED:
        asyncio.run(_run_worker())


if __name__ == "__main__":
    main()
