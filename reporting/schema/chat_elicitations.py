"""Bounded, owner-facing external MCP input requests."""

import json
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from reporting import settings


class ElicitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["accept", "decline", "cancel"]
    content: dict[str, Any] | None = None


class ChatElicitation(BaseModel):
    elicitation_id: str
    group_id: str
    thread_id: str
    turn_id: str
    proxy_name: str
    proxy_fingerprint: str
    tool_name: str
    kind: Literal["form", "url"]
    message: str
    requested_schema: dict[str, Any] | None = None
    url: str | None = None
    status: Literal["pending", "accepted", "declined", "cancelled", "expired", "consumed"] = "pending"
    created_at: str
    expires_at: str


class ChatElicitationsResponse(BaseModel):
    elicitations: list[ChatElicitation] = Field(default_factory=list)


def validate_value(value: Any, field: dict[str, Any]) -> None:
    kind = field["type"]
    if kind == "string":
        valid = isinstance(value, str) and field.get("minLength", 0) <= len(value) <= field.get("maxLength", 4096)
    elif kind == "boolean":
        valid = type(value) is bool
    else:
        valid = (
            type(value) in ((int,) if kind == "integer" else (int, float))
            and abs(value) <= 1e308
            and math.isfinite(value)
        )
        if valid:
            valid = field.get("minimum", -math.inf) <= value <= field.get("maximum", math.inf)
    if not valid or ("enum" in field and value not in field["enum"]):
        raise ValueError("Invalid field value")


def validate_schema(schema: Any) -> dict[str, Any]:
    """Reject schemas outside the flat, bounded UI subset."""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("Expected a flat object schema")
    if set(schema) - {"type", "properties", "required", "title", "description"}:
        raise ValueError("Unsupported object schema keyword")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict) or len(properties) > settings.CHAT_ELICITATION_MAX_FIELDS:
        raise ValueError("Too many form fields")
    required = schema.get("required", [])
    if (
        not isinstance(required, list)
        or len(required) > len(properties)
        or any(not isinstance(key, str) or key not in properties for key in required)
    ):
        raise ValueError("Invalid required fields")
    for key, field in properties.items():
        if not isinstance(key, str) or not 1 <= len(key) <= 128 or key in {"__proto__", "constructor", "prototype"}:
            raise ValueError("Invalid field name")
        if not isinstance(field, dict) or field.get("type") not in {"string", "number", "integer", "boolean"}:
            raise ValueError("Unsupported field type")
        if set(field) - {
            "type",
            "title",
            "description",
            "enum",
            "enumNames",
            "default",
            "minLength",
            "maxLength",
            "minimum",
            "maximum",
        }:
            raise ValueError("Unsupported field schema keyword")
        for name in ("minLength", "maxLength"):
            if name in field and (type(field[name]) is not int or not 0 <= field[name] <= 4096):
                raise ValueError("Invalid string bound")
        for name in ("minimum", "maximum"):
            if name in field and (
                type(field[name]) not in (int, float) or abs(field[name]) > 1e308 or not math.isfinite(field[name])
            ):
                raise ValueError("Invalid number bound")
        if field.get("minLength", 0) > field.get("maxLength", 4096) or field.get("minimum", -math.inf) > field.get(
            "maximum", math.inf
        ):
            raise ValueError("Inverted field bounds")
        if "enum" in field:
            if not isinstance(field["enum"], list) or not 1 <= len(field["enum"]) <= 64:
                raise ValueError("Invalid enum")
            for value in field["enum"]:
                validate_value(value, {k: v for k, v in field.items() if k != "enum"})
        if "enumNames" in field:
            names = field["enumNames"]
            if (
                not isinstance(names, list)
                or len(names) != len(field.get("enum", []))
                or any(not isinstance(n, str) or len(n) > 256 for n in names)
            ):
                raise ValueError("Invalid enum labels")
        if "default" in field:
            validate_value(field["default"], field)
    for item in [schema, *properties.values()]:
        for name in ("title", "description"):
            if name in item and (not isinstance(item[name], str) or len(item[name]) > 1000):
                raise ValueError("Oversized form text")
    if len(json.dumps(schema, ensure_ascii=False).encode()) > 32768:
        raise ValueError("Oversized form schema")
    return schema


def validate_response(schema: dict[str, Any] | None, response: ElicitationResponse) -> None:
    if response.action != "accept" or schema is None:
        if response.content:
            raise ValueError("This action does not accept form content")
        return
    validate_schema(schema)
    content = response.content or {}
    properties = schema.get("properties", {})
    if set(content) - set(properties) or set(schema.get("required", [])) - set(content):
        raise ValueError("Missing or unknown form fields")
    for key, value in content.items():
        validate_value(value, properties[key])
