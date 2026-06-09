import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

LOWER_SNAKE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
MCP_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*__[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")

# The MCP name is "{parent}__{child}" (toolset__tool / skillset__skill) and the
# provider tool-call APIs cap names at 64 chars. Capping each component at 31
# keeps every combination provider-safe (31 + len("__") + 31 == 64) without
# coupling the parent and child budgets, so a long toolset id can't silently
# push a tool past the limit and into an opaque hashed name.
MAX_SLUG_COMPONENT_LEN = 31


def validate_lower_snake_id(value: str) -> str:
    """Validate lower_snake_case identifiers such as parameter names."""
    if not LOWER_SNAKE_ID_RE.fullmatch(value):
        raise ValueError("must be lower_snake_case matching ^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
    return value


def validate_mcp_slug_component(value: str) -> str:
    """Validate immutable user-supplied IDs used as MCP name components."""
    validate_lower_snake_id(value)
    if len(value) > MAX_SLUG_COMPONENT_LEN:
        raise ValueError(f"must be at most {MAX_SLUG_COMPONENT_LEN} characters so the full MCP name stays under 64")
    return value


def validate_string_list(values: list[str], field_name: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        stripped = value.strip()
        if not stripped:
            raise ValueError(f"{field_name} entries must not be empty")
        if stripped in seen:
            raise ValueError(f"{field_name} entries must be unique")
        seen.add(stripped)
        result.append(stripped)
    return result


def validate_mcp_tool_refs(values: list[str]) -> list[str]:
    result = validate_string_list(values, "tools_required")
    for value in result:
        if not MCP_TOOL_NAME_RE.fullmatch(value):
            raise ValueError("tools_required entries must use MCP tool names like toolset_id__tool_id")
    return result


def _coerce_argument(param: "ToolParamDef", value: Any) -> tuple[Any | None, str | None]:
    if param.type == "string":
        if isinstance(value, str):
            return value, None
        return None, f"Parameter '{param.name}' must be a string, got {type(value).__name__}"
    if param.type == "boolean":
        if isinstance(value, bool):
            return value, None
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "1", "yes", "on"):
                return True, None
            if lowered in ("false", "0", "no", "off"):
                return False, None
        return None, f"Parameter '{param.name}' must be a boolean, got {type(value).__name__}"
    if param.type == "integer":
        if isinstance(value, int) and not isinstance(value, bool):
            return value, None
        if isinstance(value, str):
            try:
                return int(value, 10), None
            except ValueError:
                pass
        return None, f"Parameter '{param.name}' must be an integer, got {type(value).__name__}"
    if param.type == "float":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value, None
        if isinstance(value, str):
            try:
                return float(value), None
            except ValueError:
                pass
        return None, f"Parameter '{param.name}' must be a number, got {type(value).__name__}"
    return value, None


def _coerce_decimal(value: Any) -> Any:
    """Recursively convert Decimal to int/float."""
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {k: _coerce_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce_decimal(v) for v in value]
    return value


class ToolParamDef(BaseModel):
    """Definition of a single parameter accepted by an MCP tool."""

    name: str
    type: Literal["string", "integer", "float", "boolean"]
    description: str = ""
    required: bool = True
    default: Any | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return validate_lower_snake_id(v)

    @field_validator("default", mode="before")
    @classmethod
    def coerce_default(cls, v: Any) -> Any:
        return _coerce_decimal(v)


def _skip_cypher_quoted(cypher: str, start: int, quote: str) -> int:
    """Return the first index after a quoted Cypher string or identifier."""
    index = start + 1
    while index < len(cypher):
        char = cypher[index]
        if char == "\\" and quote != "`":
            index += 2
            continue
        if char == quote:
            if index + 1 < len(cypher) and cypher[index + 1] == quote:
                index += 2
                continue
            return index + 1
        index += 1
    return len(cypher)


def _is_cypher_parameter_start(char: str) -> bool:
    return char == "_" or "A" <= char <= "Z" or "a" <= char <= "z"


def _is_cypher_parameter_char(char: str) -> bool:
    return _is_cypher_parameter_start(char) or "0" <= char <= "9"


def _read_quoted_cypher_parameter(cypher: str, start: int) -> tuple[str | None, int]:
    """Read a parameter after ``$``, unescaping doubled identifier backticks."""
    characters: list[str] = []
    index = start + 1
    while index < len(cypher):
        char = cypher[index]
        if char != "`":
            characters.append(char)
            index += 1
            continue
        if index + 1 < len(cypher) and cypher[index + 1] == "`":
            characters.append("`")
            index += 2
            continue
        return ("".join(characters) or None), index + 1
    return None, len(cypher)


def cypher_parameter_names(cypher: str) -> set[str]:
    """The parameter names a Cypher query references ($name / $`name`).

    A single-pass scan ignores string literals, quoted identifiers, and comments.
    This keeps runtime linear for user-provided queries.
    """
    names: set[str] = set()
    index = 0

    while index < len(cypher):
        char = cypher[index]
        next_char = cypher[index + 1] if index + 1 < len(cypher) else ""

        if char == "/" and next_char == "*":
            close_index = cypher.find("*/", index + 2)
            index = len(cypher) if close_index == -1 else close_index + 2
            continue
        if char == "/" and next_char == "/":
            newline_index = cypher.find("\n", index + 2)
            index = len(cypher) if newline_index == -1 else newline_index + 1
            continue
        if char in ("'", '"', "`"):
            index = _skip_cypher_quoted(cypher, index, char)
            continue
        if char != "$" or not next_char:
            index += 1
            continue

        if next_char == "`":
            name, index = _read_quoted_cypher_parameter(cypher, index + 1)
            if name is not None:
                names.add(name)
            continue

        if _is_cypher_parameter_start(next_char):
            name_end = index + 2
            while name_end < len(cypher) and _is_cypher_parameter_char(cypher[name_end]):
                name_end += 1
            names.add(cypher[index + 1 : name_end])
            index = name_end
            continue

        index += 1

    return names


def undeclared_cypher_parameters(cypher: str, parameters: list[ToolParamDef]) -> list[str]:
    """Cypher ``$parameters`` not declared in the tool's parameter list.

    A tool whose query references ``$x`` without declaring ``x`` fails at call
    time with a Neo4j ParameterMissing error, so this must be rejected when the
    tool is created or updated.
    """
    declared = {param.name for param in parameters}
    return sorted(name for name in cypher_parameter_names(cypher) if name not in declared)


class ToolsetListItem(BaseModel):
    """Lightweight summary of a toolset for list views."""

    toolset_id: str
    name: str
    description: str = ""
    enabled: bool = True
    current_version: int = 0
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None

    @field_validator("current_version", mode="before")
    @classmethod
    def coerce_version(cls, v: Any) -> int:
        if isinstance(v, Decimal):
            return int(v)
        return int(v) if v is not None else 0


class ToolsetVersion(BaseModel):
    """A point-in-time snapshot of a toolset's metadata."""

    toolset_id: str
    name: str
    description: str = ""
    enabled: bool = True
    version: int
    created_at: str
    created_by: str
    comment: str | None = None

    @field_validator("version", mode="before")
    @classmethod
    def coerce_version(cls, v: Any) -> int:
        if isinstance(v, Decimal):
            return int(v)
        return int(v)


class ToolItem(BaseModel):
    """A tool record stored in the database."""

    tool_id: str
    toolset_id: str
    name: str
    description: str = ""
    cypher: str
    parameters: list[ToolParamDef] = Field(default_factory=list)
    enabled: bool = True
    current_version: int = 0
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None
    effective_enabled: bool | None = None
    disabled_reason: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.effective_enabled is None:
            self.effective_enabled = self.enabled

    @field_validator("current_version", mode="before")
    @classmethod
    def coerce_version(cls, v: Any) -> int:
        if isinstance(v, Decimal):
            return int(v)
        return int(v) if v is not None else 0

    @field_validator("parameters", mode="before")
    @classmethod
    def coerce_parameters(cls, v: Any) -> list[dict[str, Any]]:
        return _coerce_decimal(v) if v is not None else []


class ToolVersion(BaseModel):
    """A point-in-time snapshot of a tool's configuration."""

    tool_id: str
    toolset_id: str
    name: str
    description: str = ""
    cypher: str
    parameters: list[ToolParamDef] = Field(default_factory=list)
    enabled: bool = True
    version: int
    created_at: str
    created_by: str
    comment: str | None = None

    @field_validator("version", mode="before")
    @classmethod
    def coerce_version(cls, v: Any) -> int:
        if isinstance(v, Decimal):
            return int(v)
        return int(v)

    @field_validator("parameters", mode="before")
    @classmethod
    def coerce_parameters(cls, v: Any) -> list[dict[str, Any]]:
        return _coerce_decimal(v) if v is not None else []


class ToolsetListResponse(BaseModel):
    toolsets: list[ToolsetListItem]


class ToolsetVersionListResponse(BaseModel):
    versions: list[ToolsetVersion]


class ToolsetIdResponse(BaseModel):
    toolset_id: str


class ToolListResponse(BaseModel):
    tools: list[ToolItem]


class ToolVersionListResponse(BaseModel):
    versions: list[ToolVersion]


class ToolIdResponse(BaseModel):
    tool_id: str


def validate_tool_arguments(parameters: list["ToolParamDef"], arguments: dict[str, Any]) -> list[str]:
    """Validate *arguments* against the tool's parameter definitions.

    Returns a list of error strings; empty means the arguments are valid.
    Checks:
    - All required parameters (with no default) are present.
    - Each supplied value is the correct type.
    """
    errors: list[str] = []
    for param in parameters:
        value = arguments.get(param.name)
        if value is None:
            if param.required and param.default is None:
                errors.append(f"Required parameter '{param.name}' is missing")
            continue
        _, error = _coerce_argument(param, value)
        if error:
            errors.append(error)
    return errors


class CallToolRequest(BaseModel):
    """Request body for POST /api/v1/toolsets/<id>/tools/<id>/call."""

    arguments: dict[str, Any] = Field(default_factory=dict)


class CallToolResponse(BaseModel):
    """Response returned by a tool call."""

    results: list[Any]


class CreateToolsetRequest(BaseModel):
    """Request body for POST /api/v1/toolsets."""

    toolset_id: str
    name: str
    description: str = ""
    enabled: bool = True

    @field_validator("toolset_id")
    @classmethod
    def validate_toolset_id(cls, v: str) -> str:
        return validate_mcp_slug_component(v)


class UpdateToolsetRequest(BaseModel):
    """Request body for PUT /api/v1/toolsets/<id>."""

    name: str
    description: str = ""
    enabled: bool = True
    comment: str | None = None


class CreateToolRequest(BaseModel):
    """Request body for POST /api/v1/toolsets/<id>/tools."""

    tool_id: str
    name: str
    description: str = ""
    cypher: str
    parameters: list[ToolParamDef] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("tool_id")
    @classmethod
    def validate_tool_id(cls, v: str) -> str:
        return validate_mcp_slug_component(v)


class UpdateToolRequest(BaseModel):
    """Request body for PUT /api/v1/toolsets/<id>/tools/<id>."""

    name: str
    description: str = ""
    cypher: str
    parameters: list[ToolParamDef] = Field(default_factory=list)
    enabled: bool = True
    comment: str | None = None


class SkillsetListItem(BaseModel):
    """Lightweight summary of a skillset for list views."""

    skillset_id: str
    name: str
    description: str = ""
    enabled: bool = True
    current_version: int = 0
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None

    @field_validator("current_version", mode="before")
    @classmethod
    def coerce_version(cls, v: Any) -> int:
        if isinstance(v, Decimal):
            return int(v)
        return int(v) if v is not None else 0


class SkillsetVersion(BaseModel):
    """A point-in-time snapshot of a skillset's metadata."""

    skillset_id: str
    name: str
    description: str = ""
    enabled: bool = True
    version: int
    created_at: str
    created_by: str
    comment: str | None = None

    @field_validator("version", mode="before")
    @classmethod
    def coerce_version(cls, v: Any) -> int:
        if isinstance(v, Decimal):
            return int(v)
        return int(v)


class SkillItem(BaseModel):
    """A user-defined MCP prompt template stored in the database."""

    skill_id: str
    skillset_id: str
    name: str
    description: str = ""
    template: str
    parameters: list[ToolParamDef] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    tools_required: list[str] = Field(default_factory=list)
    enabled: bool = True
    current_version: int = 0
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None
    effective_enabled: bool | None = None
    disabled_reason: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.effective_enabled is None:
            self.effective_enabled = self.enabled

    @field_validator("current_version", mode="before")
    @classmethod
    def coerce_version(cls, v: Any) -> int:
        if isinstance(v, Decimal):
            return int(v)
        return int(v) if v is not None else 0

    @field_validator("parameters", mode="before")
    @classmethod
    def coerce_parameters(cls, v: Any) -> list[dict[str, Any]]:
        return _coerce_decimal(v) if v is not None else []

    @field_validator("triggers")
    @classmethod
    def validate_triggers(cls, v: list[str]) -> list[str]:
        return validate_string_list(v, "triggers")

    @field_validator("tools_required")
    @classmethod
    def validate_tools_required(cls, v: list[str]) -> list[str]:
        return validate_mcp_tool_refs(v)


class SkillVersion(BaseModel):
    """A point-in-time snapshot of a skill prompt template."""

    skill_id: str
    skillset_id: str
    name: str
    description: str = ""
    template: str
    parameters: list[ToolParamDef] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    tools_required: list[str] = Field(default_factory=list)
    enabled: bool = True
    version: int
    created_at: str
    created_by: str
    comment: str | None = None

    @field_validator("version", mode="before")
    @classmethod
    def coerce_version(cls, v: Any) -> int:
        if isinstance(v, Decimal):
            return int(v)
        return int(v)

    @field_validator("parameters", mode="before")
    @classmethod
    def coerce_parameters(cls, v: Any) -> list[dict[str, Any]]:
        return _coerce_decimal(v) if v is not None else []

    @field_validator("triggers")
    @classmethod
    def validate_triggers(cls, v: list[str]) -> list[str]:
        return validate_string_list(v, "triggers")

    @field_validator("tools_required")
    @classmethod
    def validate_tools_required(cls, v: list[str]) -> list[str]:
        return validate_mcp_tool_refs(v)


class SkillsetListResponse(BaseModel):
    skillsets: list[SkillsetListItem]


class SkillsetVersionListResponse(BaseModel):
    versions: list[SkillsetVersion]


class SkillsetIdResponse(BaseModel):
    skillset_id: str


class SkillListResponse(BaseModel):
    skills: list[SkillItem]


class SkillVersionListResponse(BaseModel):
    versions: list[SkillVersion]


class SkillIdResponse(BaseModel):
    skill_id: str


class CreateSkillsetRequest(BaseModel):
    """Request body for POST /api/v1/skillsets."""

    skillset_id: str
    name: str
    description: str = ""
    enabled: bool = True

    @field_validator("skillset_id")
    @classmethod
    def validate_skillset_id(cls, v: str) -> str:
        return validate_mcp_slug_component(v)


class UpdateSkillsetRequest(BaseModel):
    """Request body for PUT /api/v1/skillsets/<id>."""

    name: str
    description: str = ""
    enabled: bool = True
    comment: str | None = None


class CreateSkillRequest(BaseModel):
    """Request body for POST /api/v1/skillsets/<id>/skills."""

    skill_id: str
    name: str
    description: str = ""
    template: str
    parameters: list[ToolParamDef] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    tools_required: list[str] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("skill_id")
    @classmethod
    def validate_skill_id(cls, v: str) -> str:
        return validate_mcp_slug_component(v)

    @field_validator("triggers")
    @classmethod
    def validate_triggers(cls, v: list[str]) -> list[str]:
        return validate_string_list(v, "triggers")

    @field_validator("tools_required")
    @classmethod
    def validate_tools_required(cls, v: list[str]) -> list[str]:
        return validate_mcp_tool_refs(v)


class UpdateSkillRequest(BaseModel):
    """Request body for PUT /api/v1/skillsets/<id>/skills/<id>."""

    name: str
    description: str = ""
    template: str
    parameters: list[ToolParamDef] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    tools_required: list[str] = Field(default_factory=list)
    enabled: bool = True
    comment: str | None = None

    @field_validator("triggers")
    @classmethod
    def validate_triggers(cls, v: list[str]) -> list[str]:
        return validate_string_list(v, "triggers")

    @field_validator("tools_required")
    @classmethod
    def validate_tools_required(cls, v: list[str]) -> list[str]:
        return validate_mcp_tool_refs(v)


class RenderSkillRequest(BaseModel):
    """Request body for rendering a skill template."""

    arguments: dict[str, Any] = Field(default_factory=dict)


class RenderSkillResponse(BaseModel):
    text: str


# Matches {% $param_name %} variable references.  A preceding backslash
# (\{% $name %}) marks a literal/escaped tag that must not be validated or
# substituted; the negative lookbehind skips those, and _unescape_skill_vars
# strips the backslash after substitution is complete.
_SKILL_VAR_RE = re.compile(r"(?<!\\)\{%\s*\$([a-z][a-z0-9_]*)\s*%\}")
_SKILL_VAR_ESCAPED_RE = re.compile(r"\\(\{%\s*\$[a-z][a-z0-9_]*\s*%\})")


def _unescape_skill_vars(text: str) -> str:
    """Strip the leading backslash from escaped variable tags after substitution."""
    return _SKILL_VAR_ESCAPED_RE.sub(r"\1", text)


def template_placeholders(template: str) -> set[str]:
    """Return ``{% $param_name %}`` placeholders used by a skill template.

    Escaped tags (``\\{% $name %}``) are excluded — they are literal text,
    not variable references.
    """
    return {m.group(1) for m in _SKILL_VAR_RE.finditer(template)}


def validate_skill_template(parameters: list[ToolParamDef], template: str) -> list[str]:
    """Validate placeholder references for a skill template."""
    param_names = {p.name for p in parameters}
    placeholders = template_placeholders(template)
    errors: list[str] = []
    for placeholder in sorted(placeholders):
        if not LOWER_SNAKE_ID_RE.fullmatch(placeholder):
            errors.append(f"Placeholder '{placeholder}' must be lower_snake_case")
        elif placeholder not in param_names:
            errors.append(f"Placeholder '{placeholder}' does not match a declared parameter")
    return errors


def render_skill_template(
    parameters: list[ToolParamDef],
    template: str,
    arguments: dict[str, Any],
) -> tuple[str | None, list[str]]:
    """Render a skill template after applying defaults and coercing argument types."""
    errors = validate_skill_template(parameters, template)
    values: dict[str, Any] = {}
    for param in parameters:
        raw_value = arguments.get(param.name, param.default)
        if raw_value is None:
            if param.required:
                errors.append(f"Required parameter '{param.name}' is missing")
            continue
        coerced, error = _coerce_argument(param, raw_value)
        if error:
            errors.append(error)
            continue
        values[param.name] = coerced

    if errors:
        return None, errors

    rendered = _SKILL_VAR_RE.sub(lambda m: str(values.get(m.group(1), "")), template)
    rendered = _unescape_skill_vars(rendered)
    return rendered, []


def _quote_frontmatter_value(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def add_skill_frontmatter(text: str, triggers: list[str], tools_required: list[str]) -> str:
    """Prefix rendered skill text with generated frontmatter when metadata exists."""
    if not triggers and not tools_required:
        return text

    lines = ["---"]
    if triggers:
        lines.append("triggers:")
        lines.extend(f"  - {_quote_frontmatter_value(trigger)}" for trigger in triggers)
    if tools_required:
        lines.append("tools_required:")
        lines.extend(f"  - {_quote_frontmatter_value(tool)}" for tool in tools_required)
    lines.append("---")
    lines.append(text)
    return "\n".join(lines)


def render_skill_prompt(
    parameters: list[ToolParamDef],
    template: str,
    arguments: dict[str, Any],
    triggers: list[str],
    tools_required: list[str],
) -> tuple[str | None, list[str]]:
    rendered, errors = render_skill_template(parameters, template, arguments)
    if rendered is None:
        return None, errors
    return add_skill_frontmatter(rendered, triggers, tools_required), []
