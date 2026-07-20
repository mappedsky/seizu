import importlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast, runtime_checkable

from reporting import settings
from reporting.schema.report_config import ActionConfigFieldDef
from reporting.schema.reporting_config import ScheduledQueryAction

logger = logging.getLogger(__name__)

_MODULES = {}

# These modules are always available regardless of SCHEDULED_QUERY_MODULES.
_BUILTIN_MODULES = [
    "reporting.scheduled_query_modules.log",
]

# The superseded code-workflow dispatcher: code-defined workflows are now
# top-level activity types. Stale WORKFLOW_ACTIVITY_MODULES env values naming
# it are skipped with a warning instead of crashing the worker at startup.
_REMOVED_MODULES = (
    "reporting.scheduled_query_modules.temporal",
    "reporting.scheduled_query_modules.workflow",
)


def _configured_module_names() -> list[str]:
    names = []
    for module_name in dict.fromkeys(_BUILTIN_MODULES + list(settings.WORKFLOW_ACTIVITY_MODULES)):
        if module_name in _REMOVED_MODULES:
            logger.warning(
                "Ignoring removed workflow activity module '%s': code-defined workflows are now"
                " top-level activity types; drop it from WORKFLOW_ACTIVITY_MODULES",
                module_name,
            )
            continue
        names.append(module_name)
    return names


@runtime_checkable
class ModuleInterface(Protocol):
    """
    Type interface for modules.
    """

    def action_name(self) -> str:
        """
        The name of this action module, which will be referenced from within the
        scheduled query configuration, via the ``action_type`` setting. For example:

        .. highlight:: yaml

              images-with-no-scan:
                name: K8s container images with no vulnerability scans
                cypher: k8s-images-without-scans
                watch_scans:
                  - grouptype: KubernetesCluster
                    syncedtype: KubernetesCluster
                enabled: True
                actions:
                  # this will match an action module with the action_name of sqs
                  - action_type: sqs
                    action_config:
                      sqs_queue: k8s-image-scanner

        """
        ...

    async def setup(self) -> None:
        """
        Called when the scheduled queries worker is started. This function
        can be used for any setup that your module may need to do, like creating
        databases or queues in development, etc.
        """
        ...

    def action_config_schema(self) -> list[ActionConfigFieldDef]:
        """
        Returns a list of field definitions describing the action_config for
        this module. Used by the frontend to render a typed form instead of a
        raw JSON textarea, and by the backend to validate submitted configs.
        """
        ...

    def handle_results(
        self,
        scheduled_query_id: str,
        action: ScheduledQueryAction,
        results: Any,
    ) -> Any | Awaitable[Any]:
        """
        Called when a scheduled query configured to use this module has results.

        Synchronous handlers are executed in a worker thread. Async handlers
        run on the scheduled-query worker's event loop, which is required for
        services that share loop-bound async clients or database pools.
        """
        ...


def _import_module(module_name: str) -> ModuleInterface:
    module = importlib.import_module(module_name)
    if not isinstance(module, ModuleInterface):
        raise TypeError(
            f"Workflow activity module '{module_name}' must define "
            "action_name, setup, action_config_schema, and handle_results"
        )
    return cast(ModuleInterface, module)


async def load_modules() -> None:
    global _MODULES

    for module_name in _configured_module_names():
        module = _import_module(module_name)
        await module.setup()
        _MODULES[module.action_name()] = module


def get_module_names() -> list[str]:
    return list(_MODULES.keys())


def get_configured_action_names() -> list[str]:
    """Return the action_name() for all available modules.

    Includes built-in modules plus those listed in WORKFLOW_ACTIVITY_MODULES.
    Imports without calling setup(), so this is safe to call from the web process.
    """
    names = []
    for module_name in _configured_module_names():
        try:
            module = _import_module(module_name)
            names.append(module.action_name())
        except Exception:
            pass
    return names


def get_module(action_name: str) -> ModuleInterface:
    global _MODULES

    if action_name in _MODULES:
        return _MODULES[action_name]
    for module_name in _configured_module_names():
        module = _import_module(module_name)
        if module.action_name() == action_name:
            return module
    raise KeyError(action_name)


def get_action_schemas() -> dict[str, list[ActionConfigFieldDef]]:
    """Return action_config_schema() for all available modules.

    Includes built-in modules plus those listed in WORKFLOW_ACTIVITY_MODULES.
    Imports without calling setup(), so this is safe to call from the web process.
    """
    schemas: dict[str, list[ActionConfigFieldDef]] = {}
    for module_name in _configured_module_names():
        try:
            module = _import_module(module_name)
            schemas[module.action_name()] = module.action_config_schema()
        except Exception:
            pass
    return schemas


def get_action_validators() -> dict[str, Callable[[dict[str, Any]], str | None]]:
    """Return validate_action_config for available modules that define one.

    Imports without calling setup(), so this is safe to call from the web process.
    """
    validators: dict[str, Callable[[dict[str, Any]], str | None]] = {}
    for module_name in _configured_module_names():
        try:
            module = _import_module(module_name)
            validator = getattr(module, "validate_action_config", None)
            if validator is not None:
                validators[module.action_name()] = validator
        except Exception:
            pass
    return validators


def get_activity_retry_attempts(action_name: str) -> int:
    """Return an activity's explicit retry count, defaulting side effects to once.

    Temporal activities are at-least-once. Modules must opt into automatic
    retries only after making their handler idempotent for a stable activity id.
    """

    module = get_module(action_name)
    value = getattr(module, "activity_retry_attempts", None)
    attempts = value() if callable(value) else 1
    return max(1, min(int(attempts), 10))
