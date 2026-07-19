"""Scheduled-query config surface for the cartography_sync workflow.

Bridges the temporal action's flat ``action_config`` dict to the
``cartography_sync`` package: renders the workflow's extra UI fields,
validates submitted configs against the module registry (the same allowlist
the sync worker re-checks), and builds the ``CartographySyncInput``.

Two config tiers:
- ``modules`` — a simple list of registry module names; each becomes its own
  sequential stage with default params (mirrors the Makefile's
  one-module-at-a-time syncs and avoids surprise parallel load on Neo4j).
- ``pipeline`` — JSON for the full staged form: ordered stages whose runs
  execute in parallel, each run a module plus allowlisted params.
"""

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from cartography_sync.registry import MODULE_REGISTRY, validate_module_params
from cartography_sync.shared import CartographyModuleRun, CartographyStage, CartographySyncInput
from reporting import settings
from reporting.schema.report_config import ActionConfigFieldDef
from reporting.temporal_workflows import WorkflowInputContext


class CartographyModuleRunConfig(BaseModel):
    module: str
    params: dict[str, Any] = Field(default_factory=dict)


class CartographyStageConfig(BaseModel):
    runs: list[CartographyModuleRunConfig] = Field(min_length=1)


class CartographyPipelineConfig(BaseModel):
    stages: list[CartographyStageConfig] = Field(min_length=1)


def enabled_module_names() -> list[str]:
    """User-selectable registry modules the operator has enabled (sorted).

    ``CARTOGRAPHY_ENABLED_MODULES`` empty → every registered module; otherwise
    only configured names that exist in the registry. Internal stages
    (create-indexes, analysis) are never user-selectable — the input builder
    adds them to every pipeline.
    """
    selectable = {name for name, spec in MODULE_REGISTRY.items() if not spec.internal}
    configured = settings.CARTOGRAPHY_ENABLED_MODULES
    if not configured:
        return sorted(selectable)
    return sorted(name for name in configured if name in selectable)


def config_fields() -> list[ActionConfigFieldDef]:
    module_names = ", ".join(enabled_module_names())
    return [
        ActionConfigFieldDef(
            name="modules",
            label="Modules",
            type="string_list",
            required=False,
            description=(f"Run these modules sequentially in the listed order. Enabled modules: {module_names}."),
        ),
        ActionConfigFieldDef(
            name="pipeline",
            label="Pipeline (JSON)",
            type="text",
            required=False,
            description=(
                "Advanced JSON pipeline. Stages run in order; runs within a "
                "stage run in parallel. Cannot be combined with Modules."
            ),
        ),
        ActionConfigFieldDef(
            name="stop_on_failure",
            label="Stop on failure",
            type="boolean",
            required=False,
            default=False,
            description="Stop before the next stage if any module fails.",
        ),
        ActionConfigFieldDef(
            name="timeout_minutes",
            label="Per-module timeout (minutes)",
            type="number",
            required=False,
            default=settings.CARTOGRAPHY_MODULE_TIMEOUT_SECONDS // 60,
            description="Maximum runtime for each module run.",
        ),
    ]


def _run_errors(position: str, run: CartographyModuleRunConfig) -> list[str]:
    spec = MODULE_REGISTRY.get(run.module)
    if spec is not None and spec.internal:
        return [f"{position}: '{run.module}' is added to every pipeline automatically and cannot be configured"]
    if run.module not in enabled_module_names():
        return [f"{position}: module '{run.module}' is not enabled (enabled: {enabled_module_names()})"]
    return [f"{position}: {error}" for error in validate_module_params(run.module, run.params)]


def _parse_stages(action_config: dict[str, Any]) -> tuple[list[CartographyStage], list[str]]:
    """Parse modules/pipeline config into stages; returns (stages, errors)."""
    modules = action_config.get("modules")
    pipeline = action_config.get("pipeline")
    has_modules = bool(modules)
    has_pipeline = isinstance(pipeline, str) and pipeline.strip() != ""
    if has_modules == has_pipeline:
        return [], ["exactly one of 'modules' or 'pipeline' must be set"]

    if has_modules:
        if not isinstance(modules, list) or not all(isinstance(m, str) for m in modules):
            return [], ["'modules' must be a list of module names"]
        errors = []
        for index, module in enumerate(modules, start=1):
            errors.extend(_run_errors(f"modules entry {index}", CartographyModuleRunConfig(module=module)))
        stages = [CartographyStage(runs=[CartographyModuleRun(module=module)]) for module in modules]
        return stages, errors

    assert isinstance(pipeline, str)
    try:
        parsed = CartographyPipelineConfig.model_validate(json.loads(pipeline))
    except json.JSONDecodeError as exc:
        return [], [f"pipeline: invalid JSON ({exc})"]
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        return [], [f"pipeline: {location}: {first['msg']}"]
    errors = []
    stages = []
    for stage_index, stage in enumerate(parsed.stages, start=1):
        runs = []
        seen_modules: set[str] = set()
        for run_index, run in enumerate(stage.runs, start=1):
            errors.extend(_run_errors(f"pipeline stage {stage_index} run {run_index}", run))
            if run.module in seen_modules:
                # Concurrent runs of one module race on cartography's update
                # tags and can delete each other's data — never allow it.
                errors.append(
                    f"pipeline stage {stage_index}: module '{run.module}' appears more than once in the stage"
                    " (concurrent same-module syncs race; put repeats in separate stages)"
                )
            seen_modules.add(run.module)
            runs.append(CartographyModuleRun(module=run.module, params=dict(run.params)))
        stages.append(CartographyStage(runs=runs))
    return stages, errors


def validate_config(action_config: dict[str, Any]) -> str | None:
    """Validate the cartography_sync fields of a temporal action config."""
    _, errors = _parse_stages(action_config)
    if errors:
        return "; ".join(errors)
    timeout_minutes = action_config.get("timeout_minutes")
    if timeout_minutes is not None:
        if not isinstance(timeout_minutes, (int, float)) or isinstance(timeout_minutes, bool):
            return "'timeout_minutes' must be a number"
        if not 1 <= timeout_minutes <= 24 * 60:
            return "'timeout_minutes' must be between 1 and 1440"
    return None


def build_input(context: WorkflowInputContext) -> CartographySyncInput:
    """Build the workflow input from a validated action config.

    Raises ``ValueError`` when the config no longer validates (e.g. an
    operator narrowed CARTOGRAPHY_ENABLED_MODULES after the schedule was
    saved) — dispatch must fail rather than run a disallowed module.
    """
    stages, errors = _parse_stages(context.action_config)
    if errors:
        raise ValueError("; ".join(errors))
    timeout_minutes = context.action_config.get("timeout_minutes")
    if isinstance(timeout_minutes, (int, float)) and not isinstance(timeout_minutes, bool) and timeout_minutes >= 1:
        timeout_seconds = int(timeout_minutes) * 60
    else:
        timeout_seconds = settings.CARTOGRAPHY_MODULE_TIMEOUT_SECONDS
    # Mirror cartography's own execution model at the pipeline level: indexes
    # once before any ingestion, analysis once after all of it (each
    # subprocess runs exactly one --selected-modules stage).
    stages = [
        CartographyStage(runs=[CartographyModuleRun(module="create-indexes")]),
        *stages,
        CartographyStage(runs=[CartographyModuleRun(module="analysis")]),
    ]
    return CartographySyncInput(
        scheduled_query_id=context.scheduled_query_id,
        stages=stages,
        activity_task_queue=settings.CARTOGRAPHY_TASK_QUEUE,
        module_timeout_seconds=timeout_seconds,
        module_wait_seconds=settings.CARTOGRAPHY_MODULE_WAIT_SECONDS,
        retry_attempts=settings.CARTOGRAPHY_SYNC_RETRY_ATTEMPTS,
        stop_on_failure=context.action_config.get("stop_on_failure") is True,
    )
