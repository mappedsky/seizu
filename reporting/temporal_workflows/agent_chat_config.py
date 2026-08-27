"""Activity config surface for the agent_chat workflow module.

Renders the activity's UI fields, validates a submitted config, and builds the
``AgentChatInput``. ``_parse_config`` is shared by validation and build so the
two cannot drift, and ``build_input`` re-validates: dispatch must fail rather
than run a misconfigured agent session.
"""

from dataclasses import dataclass
from typing import Any

from reporting import settings
from reporting.schema.report_config import ActionConfigFieldDef
from reporting.temporal_workflows import WorkflowInputContext
from reporting.temporal_workflows.shared import AgentChatInput

_MAX_PROMPT_CHARS = 32000
_MAX_TIMEOUT_MINUTES = 120
# Skill names are "{skillset_id}__{skill_id}" — the same form disclosed_tools
# and the MCP prompt registry use.
_SKILL_SEPARATOR = "__"


@dataclass(frozen=True)
class _ParsedConfig:
    prompt: str
    session_title: str
    timeout_seconds: int
    skill: str | None
    model_profile_id: str | None


def config_fields() -> list[ActionConfigFieldDef]:
    return [
        ActionConfigFieldDef(
            name="prompt",
            label="Prompt",
            type="text",
            required=True,
            description=(
                "Instructions for the agent. It runs headlessly as the workflow's"
                " creator, with their permissions and chat tools. When this"
                " activity references an earlier stage's output, those rows are"
                " supplied to the agent as untrusted evidence."
            ),
        ),
        ActionConfigFieldDef(
            name="session_title",
            label="Session title",
            type="string",
            required=False,
            default="Workflow chat",
            description="Prefix for the chat session each run creates.",
        ),
        ActionConfigFieldDef(
            name="model_profile_id",
            label="Model profile",
            type="string",
            required=False,
            description="Model profile for this activity. Empty follows the current default profile.",
        ),
        ActionConfigFieldDef(
            name="skill",
            label="Skill",
            type="string",
            required=False,
            description=(
                "Optional stored skill to render into the prompt, as"
                " 'skillset__skill'. Its required tools are pre-unlocked for the run."
            ),
        ),
        ActionConfigFieldDef(
            name="timeout_minutes",
            label="Timeout (minutes)",
            type="number",
            required=False,
            default=settings.TEMPORAL_CHAT_ACTIVITY_TIMEOUT_SECONDS // 60,
            description="Maximum runtime for the agent session.",
        ),
        # Declared explicitly: the generic row fields are only injected for
        # specs with requires_rows, and this one's input is optional.
        ActionConfigFieldDef(
            name="max_rows",
            label="Max input rows",
            type="number",
            required=False,
            default=settings.TEMPORAL_WORKFLOW_MAX_RESULT_ROWS,
            description="Cap on rows passed to the agent from the referenced output.",
        ),
        ActionConfigFieldDef(
            name="query_return_attribute",
            label="Row attribute",
            type="string",
            required=False,
            default="details",
            description=(
                "Attribute to unwrap from each referenced row (queries return"
                " '... AS details'). Leave empty to pass rows through as-is."
            ),
        ),
    ]


def _parse_config(action_config: dict[str, Any]) -> tuple[_ParsedConfig | None, list[str]]:
    errors: list[str] = []

    prompt = action_config.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        errors.append("'prompt' is required")
        prompt = ""
    elif len(prompt) > _MAX_PROMPT_CHARS:
        errors.append(f"'prompt' must be at most {_MAX_PROMPT_CHARS} characters")

    session_title = action_config.get("session_title") or "Workflow chat"
    if not isinstance(session_title, str):
        errors.append("'session_title' must be a string")
        session_title = "Workflow chat"

    skill = action_config.get("skill") or None
    if skill is not None:
        if not isinstance(skill, str):
            errors.append("'skill' must be a string")
            skill = None
        elif _SKILL_SEPARATOR not in skill:
            errors.append("'skill' must be in the form 'skillset__skill'")

    model_profile_id = action_config.get("model_profile_id") or None
    if model_profile_id is not None and not isinstance(model_profile_id, str):
        errors.append("'model_profile_id' must be a string")
        model_profile_id = None

    timeout_minutes = action_config.get("timeout_minutes")
    timeout_seconds = settings.TEMPORAL_CHAT_ACTIVITY_TIMEOUT_SECONDS
    if timeout_minutes is not None:
        if not isinstance(timeout_minutes, (int, float)) or isinstance(timeout_minutes, bool):
            errors.append("'timeout_minutes' must be a number")
        elif not 1 <= timeout_minutes <= _MAX_TIMEOUT_MINUTES:
            errors.append(f"'timeout_minutes' must be between 1 and {_MAX_TIMEOUT_MINUTES}")
        else:
            timeout_seconds = int(timeout_minutes) * 60

    # Enforcement lives in build_code_workflow_input, which bounds rows by
    # max_rows and by WORKFLOW_RESULT_MAX_BYTES; only validate it here.
    max_rows = action_config.get("max_rows")
    if max_rows is not None and (not isinstance(max_rows, (int, float)) or isinstance(max_rows, bool) or max_rows < 1):
        errors.append("'max_rows' must be a positive number")

    if errors:
        return None, errors
    return (
        _ParsedConfig(
            prompt=prompt,
            session_title=session_title,
            timeout_seconds=timeout_seconds,
            skill=skill,
            model_profile_id=model_profile_id,
        ),
        [],
    )


def validate_config(action_config: dict[str, Any]) -> str | None:
    _, errors = _parse_config(action_config)
    return "; ".join(errors) if errors else None


def build_input(context: WorkflowInputContext) -> AgentChatInput:
    parsed, errors = _parse_config(context.action_config)
    if parsed is None:
        raise ValueError("; ".join(errors))
    return AgentChatInput(
        workflow_id=context.scheduled_query_id,
        creator_user_id=context.creator_user_id,
        prompt=parsed.prompt,
        session_title=parsed.session_title,
        timeout_seconds=parsed.timeout_seconds,
        skill=parsed.skill,
        model_profile_id=parsed.model_profile_id,
        rows=list(context.rows),
    )
