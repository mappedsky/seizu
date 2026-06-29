"""Common types for MCP built-in tools.

A *built-in* is a tool that ships with Seizu (as opposed to a user-defined
toolset stored in the report store).  Each built-in has:

* a namespaced ``name`` (``<group>__<action>``, e.g. ``reports__create``),
* a short human-readable ``description``,
* a JSON Schema ``input_schema`` the MCP client validates arguments against,
* a list of permission strings the caller must hold, and
* an async ``handler`` that runs the tool.

Groups live in their own submodule (``reports.py``, ``toolsets.py`` …) and
expose a module-level ``BUILTINS: List[BuiltinTool]`` plus a ``GROUP`` name so
operators can enable/disable whole groups via ``MCP_ENABLED_BUILTINS``.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from reporting.authnz import CurrentUser
from reporting.schema.confirmations import ActionConfirmationTarget

BuiltinHandler = Callable[[dict[str, Any], CurrentUser | None], Awaitable[Any]]
ConfirmationResolver = Callable[[dict[str, Any], CurrentUser | None], Awaitable[ActionConfirmationTarget | None]]


@dataclass
class BuiltinTool:
    """A single built-in MCP tool."""

    name: str
    group: str
    description: str
    input_schema: dict[str, Any]
    required_permissions: list[str]
    handler: BuiltinHandler
    # Whether the tool needs a resolved CurrentUser (writes that record
    # created_by/updated_by).  Handlers that need a user raise if it's None.
    requires_user: bool = False
    confirmation: ConfirmationResolver | None = None
    chat_safe_without_confirmation: bool = False
    # When True the tool is only callable from the chat agent, not via the MCP
    # server endpoint.  External MCP clients never see it in tool listings.
    chat_only: bool = False
    # When True the tool is always included in the chat agent's callable set,
    # even under progressive disclosure (where most tools are gated behind skill
    # rendering).  Use this for general-purpose execution tools (e.g. sandbox
    # delegation) that the model should be able to reach without a skill unlock.
    always_disclosed: bool = False


def model_input_schema(
    model: type[BaseModel],
    *,
    extra_properties: dict[str, Any] | None = None,
    extra_required: list[str] | None = None,
) -> dict[str, Any]:
    """Convert a Pydantic model's JSON schema into an MCP input schema.

    MCP clients expect a self-contained JSON Schema object (no external
    ``$ref``s).  Pydantic emits ``$defs`` when the model has nested
    BaseModels; we inline them here so the schema travels over the wire
    intact.

    ``extra_properties`` / ``extra_required`` let callers inject
    path-parameter style fields (e.g. ``report_id``) that aren't part of the
    request body model.
    """
    schema = model.model_json_schema()
    _inline_refs(schema, schema.get("$defs", {}))
    schema.pop("$defs", None)
    schema.pop("title", None)
    properties: dict[str, Any] = dict(schema.get("properties", {}))
    required: list[str] = list(schema.get("required", []))
    if extra_properties:
        # Caller-supplied path params come first so the schema reads
        # naturally when rendered by an MCP client.
        properties = {**extra_properties, **properties}
    if extra_required:
        required = list(extra_required) + [r for r in required if r not in extra_required]
    result: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


def _inline_refs(node: Any, defs: dict[str, Any]) -> None:
    """Recursively replace ``$ref: "#/$defs/X"`` with the referenced schema."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = defs.get(ref.split("/")[-1], {})
            # Copy the referenced schema's keys into this node, then drop $ref.
            del node["$ref"]
            for k, v in target.items():
                node.setdefault(k, v)
        for v in node.values():
            _inline_refs(v, defs)
    elif isinstance(node, list):
        for v in node:
            _inline_refs(v, defs)


@dataclass
class BuiltinGroup:
    """A collection of related built-in tools, filterable as a unit."""

    name: str
    tools: list[BuiltinTool] = field(default_factory=list)
