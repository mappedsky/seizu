"""Pydantic-backed validation for workflow activity configuration fields."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    create_model,
)

from reporting.schema.report_config import ActionConfigFieldDef


def _annotation(field: ActionConfigFieldDef) -> Any:
    if field.type in ("string", "text"):
        return StrictStr
    if field.type == "number":
        return StrictInt | StrictFloat
    if field.type == "boolean":
        return Literal[True] if field.required else StrictBool
    if field.type == "string_list":
        return list[StrictStr]
    if field.type == "select":
        if field.options:
            return Literal.__getitem__(tuple(field.options))
        return StrictStr
    if field.type == "parameters":
        return list[dict[str, Any]]
    raise ValueError(f"Unsupported activity config field type: {field.type}")


def config_model(
    fields: list[ActionConfigFieldDef],
    *,
    name: str = "ActivityConfig",
) -> type[BaseModel]:
    """Build a strict Pydantic model from the UI field definitions.

    Keeping one field declaration as the source of truth prevents the frontend
    schema and backend validation rules from drifting apart.
    """

    definitions: dict[str, Any] = {}
    for field in fields:
        annotation = _annotation(field)
        if not field.required:
            annotation = annotation | None
        default = ... if field.required else field.default
        definitions[field.name] = (
            annotation,
            Field(
                default=default,
                title=field.label,
                description=field.description,
                ge=field.minimum,
                le=field.maximum,
                json_schema_extra={"ui_type": field.type, "warning": field.warning},
            ),
        )
    return create_model(name, __config__=ConfigDict(extra="forbid"), **definitions)


def validate_config(
    value: dict[str, Any],
    fields: list[ActionConfigFieldDef],
    *,
    name: str = "ActivityConfig",
) -> str | None:
    """Validate configuration and return one concise, location-aware error."""

    # ``Literal[True]`` accepts integer ``1`` because ``True == 1`` in Python.
    # Check identity before Pydantic validation so acknowledgement checkboxes
    # remain genuinely boolean and required acknowledgements cannot be forged
    # with a truthy value from a non-browser client.
    for field in fields:
        if field.type == "boolean" and field.required and value.get(field.name) is not True:
            return f"{field.name}: Input should be True"

    try:
        config_model(fields, name=name).model_validate(value)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        return f"{location}: {first['msg']}" if location else str(first["msg"])
    return None


def config_json_schema(
    fields: list[ActionConfigFieldDef],
    *,
    name: str = "ActivityConfig",
) -> dict[str, Any]:
    return config_model(fields, name=name).model_json_schema()
