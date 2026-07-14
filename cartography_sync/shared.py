"""Workflow/activity payload dataclasses for cartography syncs.

Imported by the CartographySyncWorkflow inside the Temporal sandbox and by the
sync image's activity worker — stdlib dataclasses only, no I/O.
"""

from dataclasses import dataclass, field
from typing import Any

# CartographySyncResult.status values.
STATUS_COMPLETED = "completed"
STATUS_COMPLETED_WITH_ERRORS = "completed_with_errors"
STATUS_STOPPED_ON_FAILURE = "stopped_on_failure"


@dataclass
class CartographyModuleRun:
    """One intel-module run: a registry module name plus its allowlisted params."""

    module: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class CartographyStage:
    """Module runs executed in parallel; stages themselves run sequentially."""

    runs: list[CartographyModuleRun] = field(default_factory=list)


@dataclass
class CartographySyncInput:
    scheduled_query_id: str
    stages: list[CartographyStage] = field(default_factory=list)
    # Task queue served by the dedicated cartography sync worker; the workflow
    # dispatches its module activities there (cross-queue).
    activity_task_queue: str = "seizu-cartography"
    module_timeout_seconds: int = 3600
    heartbeat_timeout_seconds: int = 120
    retry_attempts: int = 2
    # When True, a failed module run skips all remaining stages (for pipelines
    # whose later stages depend on earlier data). Default records the failure
    # and continues, matching the other Seizu workflows.
    stop_on_failure: bool = False


@dataclass
class CartographyModuleActivityInput:
    module: str
    params: dict[str, Any] = field(default_factory=dict)
    # Local subprocess watchdog; the activity's start_to_close timeout is set
    # slightly above this by the workflow.
    timeout_seconds: int = 3600


@dataclass
class CartographyModuleResult:
    module: str
    status: str  # completed | failed | skipped
    exit_code: int = 0
    duration_seconds: float = 0.0
    # Byte-capped tail of the merged stdout/stderr (or the failure text when
    # the run never produced output).
    output_tail: str = ""


@dataclass
class CartographyStageResult:
    results: list[CartographyModuleResult] = field(default_factory=list)


@dataclass
class CartographySyncResult:
    stages: list[CartographyStageResult] = field(default_factory=list)
    status: str = STATUS_COMPLETED
