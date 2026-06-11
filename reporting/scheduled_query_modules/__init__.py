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
        return

    @staticmethod
    def action_config_schema() -> list[ActionConfigFieldDef]:
        """
        Returns a list of field definitions describing the action_config for
        this module. Used by the frontend to render a typed form instead of a
        raw JSON textarea, and by the backend to validate submitted configs.
        """
        return []

    @staticmethod
    def handle_results(
        scheduled_query_id: str,
        action: ScheduledQueryAction,
        results: list[dict[str, Any]],
    ) -> None:
        """
        Called when a schedule query configured to use this module has results.
        """
        return


async def load_modules() -> None:
    global _MODULES

    for module_name in settings.SCHEDULED_QUERY_MODULES:
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

    Includes built-in modules plus those listed in SCHEDULED_QUERY_MODULES.
    Imports without calling setup(), so this is safe to call from the web process.
    """
    seen = set()
    names = []
    for module_name in _BUILTIN_MODULES + list(settings.SCHEDULED_QUERY_MODULES):
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

    return _MODULES[action_name]


def get_action_schemas() -> dict[str, list[ActionConfigFieldDef]]:
    """Return action_config_schema() for all available modules.

    Includes built-in modules plus those listed in SCHEDULED_QUERY_MODULES.
    Imports without calling setup(), so this is safe to call from the web process.
    """
    seen: set = set()
    schemas: dict[str, list[ActionConfigFieldDef]] = {}
    for module_name in _BUILTIN_MODULES + list(settings.SCHEDULED_QUERY_MODULES):
        if module_name in seen:
            continue
        seen.add(module_name)
        try:
            module: ModuleInterface = cast(ModuleInterface, __import__(module_name, fromlist=["_fake"]))
            schemas[module.action_name()] = module.action_config_schema()
        except Exception:
            pass
    return schemas
