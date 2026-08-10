"""The Temporal Schedule that drives session reaping.

Separate from :mod:`reporting.services.schedule_reconciler`, which reconciles
*user-defined* schedules from stored records. This one is a fixture of the
deployment: a single Schedule with a fixed id, derived from settings rather
than from a record, reconciled by every worker replica at startup because doing
so is idempotent.
"""

import logging
from datetime import timedelta

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdate,
)
from temporalio.common import RetryPolicy

from reporting import settings
from reporting.services import session_reaper
from reporting.temporal_workflows.session_reap import SCHEDULE_ID, WORKFLOW_ID, WORKFLOW_TYPE

logger = logging.getLogger(__name__)


def build_schedule() -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            WORKFLOW_TYPE,
            id=WORKFLOW_ID,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
            retry_policy=RetryPolicy(maximum_attempts=1),
        ),
        spec=ScheduleSpec(
            intervals=[ScheduleIntervalSpec(every=timedelta(seconds=settings.CHAT_SESSION_REAP_INTERVAL_SECONDS))],
        ),
        policy=SchedulePolicy(
            # SKIP, not BUFFER_ONE: a sweep that outran its interval has more
            # work than one pass can do, and queueing another behind it only
            # doubles the provider calls for the same backlog. The next tick
            # picks up whatever is left.
            overlap=ScheduleOverlapPolicy.SKIP,
            catchup_window=timedelta(minutes=5),
            pause_on_failure=False,
        ),
        state=ScheduleState(note="Managed by Seizu"),
    )


async def reconcile(client: Client | None = None) -> None:
    """Create or update the sweep's Schedule; delete it when reaping is off.

    Best effort: a worker that cannot reach Temporal must still start and serve
    workflows. The next replica to boot, or the next restart, reconciles again.
    """
    from reporting.services.schedule_reconciler import get_client

    try:
        temporal = client or await get_client()
    except Exception:
        logger.warning("could not reach Temporal to reconcile the session reap schedule", exc_info=True)
        return
    handle = temporal.get_schedule_handle(SCHEDULE_ID)
    if not session_reaper.reaping_configured():
        # Delete rather than pause: the setting is off, and a paused schedule
        # left behind reads as "something disabled this by hand".
        try:
            await handle.delete()
            logger.info("Session reaping is disabled; removed its schedule")
        except Exception:
            logger.debug("no session reap schedule to remove", exc_info=True)
        return
    schedule = build_schedule()
    try:
        await temporal.create_schedule(SCHEDULE_ID, schedule)
        logger.info(
            "Session reap schedule created",
            extra={"interval_seconds": settings.CHAT_SESSION_REAP_INTERVAL_SECONDS},
        )
    except ScheduleAlreadyRunningError:
        # Every replica after the first reaches this, and the settings may have
        # changed since the schedule was created; updating is how an interval
        # change takes effect without an operator touching Temporal.
        try:
            await handle.update(lambda _input: ScheduleUpdate(schedule=schedule))
        except Exception:
            logger.warning("could not update the session reap schedule", exc_info=True)
