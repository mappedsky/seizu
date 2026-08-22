"""Built-in ``plugins__*`` tools for Agent Plugin package management."""

import base64
from typing import Any

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.confirmations import ActionConfirmationTarget
from reporting.services import plugin_packages, report_store
from reporting.services.mcp_builtins.base import BuiltinGroup, BuiltinTool

GROUP = "plugins"


def _user(current: CurrentUser | None) -> CurrentUser:
    if current is None:
        raise RuntimeError("No current user on the request context")
    return current


async def _confirm(args: dict[str, Any], current: CurrentUser | None) -> ActionConfirmationTarget:
    return ActionConfirmationTarget(
        action=str(args.get("action", "update")),
        resource_type="plugin",
        resource_id=str(args.get("plugin_id", "package")),
    )


async def _list(args: dict[str, Any], current: CurrentUser | None) -> dict[str, Any]:
    return {"plugins": [item.model_dump() for item in await report_store.list_plugins()]}


async def _get(args: dict[str, Any], current: CurrentUser | None) -> dict[str, Any]:
    item = await report_store.get_plugin(args["plugin_id"])
    return item.model_dump() if item else {"error": "Plugin not found"}


def _package(args: dict[str, Any]) -> plugin_packages.ParsedPlugin:
    try:
        data = base64.b64decode(args["package_base64"], validate=True)
        files = plugin_packages.files_from_zip(data)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Invalid package: {exc}") from exc
    return plugin_packages.parse_package(files)


async def _validate(args: dict[str, Any], current: CurrentUser | None) -> dict[str, Any]:
    return _package(args).response().model_dump(mode="json")


async def _install(args: dict[str, Any], current: CurrentUser | None) -> dict[str, Any]:
    parsed = _package(args)
    if not parsed.valid:
        return parsed.response().model_dump(mode="json")
    user = _user(current)
    item = await report_store.publish_plugin(
        parsed.plugin_id,
        parsed.manifest,
        parsed.files,
        parsed.skills,
        [diagnostic.model_dump() for diagnostic in parsed.diagnostics],
        parsed.package_digest,
        user.user.user_id,
        args.get("comment"),
    )
    return item.model_dump()


async def _set_enabled(args: dict[str, Any], current: CurrentUser | None) -> dict[str, Any]:
    item = await report_store.set_plugin_enabled(args["plugin_id"], args["enabled"], _user(current).user.user_id)
    return item.model_dump() if item else {"error": "Plugin not found"}


async def _delete(args: dict[str, Any], current: CurrentUser | None) -> dict[str, Any]:
    return {"plugin_id": args["plugin_id"], "deleted": await report_store.delete_plugin(args["plugin_id"])}


async def _skills(args: dict[str, Any], current: CurrentUser | None) -> dict[str, Any]:
    return {"skills": [item.model_dump() for item in await report_store.list_plugin_skills(args["plugin_id"])]}


async def _versions(args: dict[str, Any], current: CurrentUser | None) -> dict[str, Any]:
    return {"versions": [item.model_dump() for item in await report_store.list_plugin_versions(args["plugin_id"])]}


_ID_SCHEMA = {
    "type": "object",
    "properties": {"plugin_id": {"type": "string"}},
    "required": ["plugin_id"],
}
_PACKAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "package_base64": {"type": "string", "description": "A ZIP package encoded as base64."},
        "comment": {"type": "string"},
    },
    "required": ["package_base64"],
}

GROUP_DEF = BuiltinGroup(
    name=GROUP,
    tools=[
        BuiltinTool(
            "plugins__list",
            GROUP,
            "List installed Agent Plugins.",
            {"type": "object", "properties": {}},
            [Permission.PLUGINS_READ.value],
            _list,
            collection_key="plugins",
        ),
        BuiltinTool(
            "plugins__get", GROUP, "Get an installed Agent Plugin.", _ID_SCHEMA, [Permission.PLUGINS_READ.value], _get
        ),
        BuiltinTool(
            "plugins__validate",
            GROUP,
            "Validate an Agent Plugins 1.0.0 ZIP package.",
            _PACKAGE_SCHEMA,
            [Permission.PLUGINS_READ.value],
            _validate,
        ),
        BuiltinTool(
            "plugins__install",
            GROUP,
            "Install or update an Agent Plugin ZIP package.",
            _PACKAGE_SCHEMA,
            [Permission.PLUGINS_WRITE.value],
            _install,
            requires_user=True,
            confirmation=_confirm,
        ),
        BuiltinTool(
            "plugins__set_enabled",
            GROUP,
            "Enable or disable an installed Agent Plugin.",
            {
                "type": "object",
                "properties": {"plugin_id": {"type": "string"}, "enabled": {"type": "boolean"}},
                "required": ["plugin_id", "enabled"],
            },
            [Permission.PLUGINS_WRITE.value],
            _set_enabled,
            requires_user=True,
            confirmation=_confirm,
        ),
        BuiltinTool(
            "plugins__delete",
            GROUP,
            "Delete an installed Agent Plugin.",
            _ID_SCHEMA,
            [Permission.PLUGINS_DELETE.value],
            _delete,
            confirmation=_confirm,
        ),
        BuiltinTool(
            "plugins__list_skills",
            GROUP,
            "List the skills indexed from an Agent Plugin's current revision.",
            _ID_SCHEMA,
            [Permission.PLUGINS_READ.value],
            _skills,
            collection_key="skills",
        ),
        BuiltinTool(
            "plugins__list_versions",
            GROUP,
            "List immutable Agent Plugin revisions.",
            _ID_SCHEMA,
            [Permission.PLUGINS_READ.value],
            _versions,
            collection_key="versions",
        ),
    ],
)
