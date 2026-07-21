"""Activity config surface for the cartography_sync workflow.

Bridges the cartography activity's flat ``parameters`` dict to the
``cartography_sync`` package: renders the activity's UI fields, validates
submitted configs against the module registry (the same allowlist the sync
worker re-checks), and builds the ``CartographySyncInput``.

The config is an ordered ``module_runs`` list — each entry a registry module
plus its allowlisted params. Runs execute sequentially, one stage per run
(mirrors the Makefile's one-module-at-a-time syncs and avoids surprise
parallel load on Neo4j); parallelism comes from placing multiple cartography
activities in one top-level workflow stage. Structural stages
(create-indexes, analysis) are ordinary selectable modules the user places
explicitly.
"""

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from cartography_sync.registry import (
    ALWAYS_ENABLED_MODULES,
    BOOLEAN,
    ENUM,
    MODULE_REGISTRY,
    NUMBER,
    STRING_LIST,
    FlagSpec,
    validate_module_params,
)
from cartography_sync.shared import CartographyModuleRun, CartographyStage, CartographySyncInput
from reporting import settings
from reporting.schema.report_config import ActionConfigFieldDef
from reporting.temporal_workflows import WorkflowInputContext


class CartographyModuleRunConfig(BaseModel):
    module: str
    params: dict[str, Any] = Field(default_factory=dict)


def enabled_module_names() -> list[str]:
    """Selectable registry modules the operator has enabled (sorted).

    ``CARTOGRAPHY_ENABLED_MODULES`` empty → every registered module; otherwise
    the configured names that exist in the registry. The credential-free
    structural stages (create-indexes, analysis) are always selectable — every
    useful pipeline needs them and operator allowlists predate their
    selectability.
    """
    configured = settings.CARTOGRAPHY_ENABLED_MODULES
    if not configured:
        return sorted(MODULE_REGISTRY)
    names = {name for name in configured if name in MODULE_REGISTRY}
    return sorted(names | ALWAYS_ENABLED_MODULES)


def _field_type(flag: FlagSpec) -> str:
    if flag.type == BOOLEAN:
        return "boolean"
    if flag.type == NUMBER:
        return "number"
    if flag.type == ENUM:
        return "select"
    if flag.type == STRING_LIST:
        return "string_list"
    return "string"


def module_param_fields(module: str) -> list[ActionConfigFieldDef]:
    """Project a module's registry flags as UI field definitions."""
    spec = MODULE_REGISTRY[module]
    return [
        ActionConfigFieldDef(
            name=flag.name,
            label=flag.name.replace("_", " ").capitalize(),
            type=_field_type(flag),
            required=False,
            description=flag.description or None,
            default=flag.default,
            options=list(flag.choices) if flag.type in (ENUM, STRING_LIST) else None,
            minimum=flag.min_value,
            maximum=flag.max_value,
        )
        for flag in spec.flags
    ]


def config_fields() -> list[ActionConfigFieldDef]:
    module_names = enabled_module_names()
    return [
        ActionConfigFieldDef(
            name="module_runs",
            label="Intel modules",
            type="module_runs",
            required=True,
            description=(
                "Ordered module runs, executed sequentially. Place create-indexes"
                " before ingestion modules and analysis after them."
            ),
            options=module_names,
            item_schemas={name: module_param_fields(name) for name in module_names},
        ),
        ActionConfigFieldDef(
            name="stop_on_failure",
            label="Stop on failure",
            type="boolean",
            required=False,
            default=False,
            description="Stop before the next module run if one fails.",
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
    if run.module not in enabled_module_names():
        return [f"{position}: module '{run.module}' is not enabled (enabled: {enabled_module_names()})"]
    return [f"{position}: {error}" for error in validate_module_params(run.module, run.params)]


def _parse_module_runs(action_config: dict[str, Any]) -> tuple[list[CartographyStage], list[str]]:
    """Parse the module_runs config into sequential single-run stages."""
    raw = action_config.get("module_runs")
    if not isinstance(raw, list) or not raw:
        return [], ["'module_runs' must be a non-empty list of module runs"]
    errors: list[str] = []
    stages: list[CartographyStage] = []
    for index, value in enumerate(raw, start=1):
        position = f"module run {index}"
        try:
            run = CartographyModuleRunConfig.model_validate(value)
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(part) for part in first["loc"])
            errors.append(f"{position}: {location}: {first['msg']}" if location else f"{position}: {first['msg']}")
            continue
        errors.extend(_run_errors(position, run))
        # One run per stage: sequential execution, and the per-module mutex
        # (fixed child-workflow id) never contends within one sync.
        stages.append(CartographyStage(runs=[CartographyModuleRun(module=run.module, params=dict(run.params))]))
    return stages, errors


def validate_config(action_config: dict[str, Any]) -> str | None:
    """Validate the cartography_sync fields of an activity config."""
    _, errors = _parse_module_runs(action_config)
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
    stages, errors = _parse_module_runs(context.action_config)
    if errors:
        raise ValueError("; ".join(errors))
    timeout_minutes = context.action_config.get("timeout_minutes")
    if isinstance(timeout_minutes, (int, float)) and not isinstance(timeout_minutes, bool) and timeout_minutes >= 1:
        timeout_seconds = int(timeout_minutes) * 60
    else:
        timeout_seconds = settings.CARTOGRAPHY_MODULE_TIMEOUT_SECONDS
    return CartographySyncInput(
        scheduled_query_id=context.scheduled_query_id,
        stages=stages,
        activity_task_queue=settings.CARTOGRAPHY_TASK_QUEUE,
        module_timeout_seconds=timeout_seconds,
        module_wait_seconds=settings.CARTOGRAPHY_MODULE_WAIT_SECONDS,
        retry_attempts=settings.CARTOGRAPHY_SYNC_RETRY_ATTEMPTS,
        stop_on_failure=context.action_config.get("stop_on_failure") is True,
    )
