from collections.abc import Awaitable, Callable
from typing import Any, cast

from reporting import settings
from reporting.schema.report_config import ActionConfigFieldDef
from reporting.schema.reporting_config import ScheduledQueryAction

_MODULES = {}

# These modules are always available regardless of SCHEDULED_QUERY_MODULES.
_BUILTIN_MODULES = [
    "reporting.scheduled_query_modules.log",
]


class ModuleInterface:
    """
    Type interface for modules.
    """

    @staticmethod
    def action_name() -> str:
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
        return ""

    @staticmethod
    async def setup() -> None:
        """
        Called when the scheduled queries worker is started. This function
        can be used for any setup that your module may need to do, like creating
        databases or queues in development, etc.
        """
        return None

    @staticmethod
    def action_config_schema() -> list[ActionConfigFieldDef]:
        """
        Returns a list of field definitions describing the action_config for
        this module. Used by the frontend to render a typed form instead of a
        raw JSON textarea, and by the backend to validate submitted configs.
        """
        return []

    @staticmethod
    def validate_action_config(action_config: dict[str, Any]) -> str | None:
        """
        Optional module-specific validation beyond the generic required-field
        checks (e.g. cross-field rules or nested structures). Returns an error
        message string, or None when the config is valid. Called on scheduled
        query create/update.
        """
        return None

    @staticmethod
    def handle_results(
        scheduled_query_id: str,
        action: ScheduledQueryAction,
        results: list[dict[str, Any]],
    ) -> None | Awaitable[None]:
        """
        Called when a scheduled query configured to use this module has results.

        Synchronous handlers are executed in a worker thread. Async handlers
        run on the scheduled-query worker's event loop, which is required for
        services that share loop-bound async clients or database pools.
        """
        return None


async def load_modules() -> None:
    global _MODULES

    for module_name in dict.fromkeys(_BUILTIN_MODULES + list(settings.WORKFLOW_ACTIVITY_MODULES)):
        # fromlist is required here, or the module will not be loaded.
        # The actual valud of fromlist doesn't matter. We're using this rather
        # than importlib to be able to handle the type checking properly.
        module: ModuleInterface = cast(ModuleInterface, __import__(module_name, fromlist=["_fake"]))
        await module.setup()
        _MODULES[module.action_name()] = module


def get_module_names() -> list[str]:
    return list(_MODULES.keys())


def get_configured_action_names() -> list[str]:
    """Return the action_name() for all available modules.

    Includes built-in modules plus those listed in WORKFLOW_ACTIVITY_MODULES.
    Imports without calling setup(), so this is safe to call from the web process.
    """
    seen = set()
    names = []
    for module_name in _BUILTIN_MODULES + list(settings.WORKFLOW_ACTIVITY_MODULES):
        if module_name in seen:
            continue
        seen.add(module_name)
        try:
            module: ModuleInterface = cast(ModuleInterface, __import__(module_name, fromlist=["_fake"]))
            names.append(module.action_name())
        except Exception:
            pass
    return names


def get_module(action_name: str) -> ModuleInterface:
    global _MODULES

    if action_name == "temporal" and "workflow" in _MODULES:
        return _MODULES["workflow"]
    return _MODULES[action_name]


def get_action_schemas() -> dict[str, list[ActionConfigFieldDef]]:
    """Return action_config_schema() for all available modules.

    Includes built-in modules plus those listed in WORKFLOW_ACTIVITY_MODULES.
    Imports without calling setup(), so this is safe to call from the web process.
    """
    seen: set = set()
    schemas: dict[str, list[ActionConfigFieldDef]] = {}
    for module_name in _BUILTIN_MODULES + list(settings.WORKFLOW_ACTIVITY_MODULES):
        if module_name in seen:
            continue
        seen.add(module_name)
        try:
            module: ModuleInterface = cast(ModuleInterface, __import__(module_name, fromlist=["_fake"]))
            schemas[module.action_name()] = module.action_config_schema()
        except Exception:
            pass
    if "workflow" in schemas:
        schemas.setdefault("temporal", schemas["workflow"])
    return schemas


def get_action_validators() -> dict[str, Callable[[dict[str, Any]], str | None]]:
    """Return validate_action_config for available modules that define one.

    Imports without calling setup(), so this is safe to call from the web process.
    """
    seen: set = set()
    validators: dict[str, Callable[[dict[str, Any]], str | None]] = {}
    for module_name in _BUILTIN_MODULES + list(settings.WORKFLOW_ACTIVITY_MODULES):
        if module_name in seen:
            continue
        seen.add(module_name)
        try:
            module: ModuleInterface = cast(ModuleInterface, __import__(module_name, fromlist=["_fake"]))
            validator = getattr(module, "validate_action_config", None)
            if validator is not None:
                validators[module.action_name()] = validator
        except Exception:
            pass
    if "workflow" in validators:
        validators.setdefault("temporal", validators["workflow"])
    return validators
