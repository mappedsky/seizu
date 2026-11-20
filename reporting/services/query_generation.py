"""Generate a Cypher query from a plain-text question, for the query console.

A one-shot model call, not a chat turn: no session, no checkpoint, no tools.
The model comes from the model profile's ``query_console`` settings (or the
``QUERY_GENERATION_*`` settings when no profile is enabled). The prompt
carries the graph schema, the model returns one structured
``{query, explanation}``, and the result goes through the same validator an
ad-hoc query does. A query the validator rejects or
warns about is sent back once with the diagnostics for a repair; whatever
that returns is final.

Nothing here executes the query. The caller decides whether a validated
query is recorded and run.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field, replace
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from reporting import settings
from reporting.schema.model_profiles import SelectableReasoningEffort
from reporting.services import chat_models, model_profiles, reporting_neo4j
from reporting.services.chat_budget import BudgetController, initial_budget_ledger
from reporting.services.query_validator import validate_query

logger = logging.getLogger(__name__)

# Names the call in budget estimates and traces.
_PHASE = "query_generation"
# A query and a sentence or two about it. Reasoning models spend part of this
# on thinking, so it is not the size of the answer alone.
_MAX_OUTPUT_TOKENS = 4096
# The schema is names only, but a large graph has thousands of property keys;
# past this the list is cut rather than crowding out the question.
_MAX_SCHEMA_CHARS = 24_000

_MOCK_QUERY = "MATCH (n) RETURN n LIMIT 25"

_SYSTEM_PROMPT = """\
You write Neo4j Cypher queries for a security graph built by Cartography.
Translate the user's question into exactly one read-only Cypher query.

Rules:
- Read-only: never CREATE, MERGE, SET, DELETE, REMOVE, LOAD CSV, or USE.
- Do not call procedures (CALL ...) unless the question cannot be answered without one.
- Use only labels, relationship types and property keys that appear in the schema below.
  When the schema does not say which way a relationship points, prefer an undirected pattern.
- Bound the result: end with a LIMIT (25 unless the question asks for more) unless the
  query returns aggregates only.
- The console draws nodes, relationships and paths as a graph, and anything else as a
  table. Return nodes or paths when the question is about what is connected to what,
  and named scalar columns when it asks for counts, lists of names, or properties.
- Never interpolate the user's text as a literal unless it is a value they asked to match.

The explanation is one or two plain sentences saying what the query matches.
Mention any assumption you had to make about the schema."""


class GeneratedCypher(BaseModel):
    """One read-only Cypher query answering the user's question."""

    query: str = Field(description="A single read-only Cypher query, with no surrounding prose or code fences.")
    explanation: str = Field(
        description="One or two sentences describing what the query matches, and any schema assumption made."
    )


class QueryGenerationUnavailable(Exception):
    """No model is configured to generate queries with."""


class QueryGenerationTimeout(Exception):
    """The model did not answer within ``QUERY_GENERATION_TIMEOUT_SECONDS``."""


@dataclass(frozen=True)
class QueryGeneration:
    query: str
    explanation: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # What answered: the model, the reasoning effort sent (empty when the
    # provider's own default applied), and the profile it came from, if any.
    model_id: str = ""
    reasoning_effort: str = ""
    profile_name: str = ""

    @property
    def valid(self) -> bool:
        return bool(self.query) and not self.errors


async def generate_query(
    question: str,
    *,
    current_query: str = "",
    model_profile_id: str | None = None,
    reasoning_effort: SelectableReasoningEffort | None = None,
) -> QueryGeneration:
    """Return a validated Cypher query for ``question``.

    ``current_query`` is what the console editor holds, offered as context so
    a follow-up such as "only the public ones" refines it rather than starting
    over. ``model_profile_id`` and ``reasoning_effort`` pick among the
    profiles and levels enabled for the console (its starting profile and
    level when omitted).

    Raises :class:`QueryGenerationUnavailable` when the profile cannot be
    resolved and :class:`QueryGenerationTimeout` when the first answer does
    not arrive in time; any other model or provider failure propagates.

    The repair call runs only when the time left is at least what the first
    call took; otherwise the first query is returned with its diagnostics.
    """
    from reporting.services import chat_graph

    if chat_graph._chat_provider() == chat_graph._MOCK_PROVIDER:
        mock = await _validated(
            GeneratedCypher(
                query=_MOCK_QUERY,
                explanation="The mock chat provider always returns this query.",
            )
        )
        return replace(mock, model_id="mock")

    try:
        choice = await model_profiles.resolve_query_console(model_profile_id, reasoning_effort)
    except model_profiles.ModelProfileUnavailable as exc:
        raise QueryGenerationUnavailable(str(exc)) from exc

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1, settings.QUERY_GENERATION_TIMEOUT_SECONDS)
    spec = choice.spec
    model = chat_graph.build_chat_model(spec)
    controller = BudgetController(
        initial_budget_ledger(
            cost_limit_usd=choice.cost_budget_usd,
            model_specs=[spec.to_payload()],
        )
    )
    config: RunnableConfig = {"configurable": {"budget_controller": controller}}
    logger.info(
        "Generating query",
        extra={"model": spec.model_id, "reasoning_effort": spec.reasoning_effort, "profile_id": spec.profile_id},
    )
    messages = _initial_messages(question, current_query, await _schema_text())
    started = loop.time()
    try:
        async with asyncio.timeout_at(deadline):
            generated = await _invoke(chat_graph, model, messages, config)
    except TimeoutError as exc:
        raise QueryGenerationTimeout from exc
    result = _answered_by(await _validated(generated), spec)
    if result.valid and not result.warnings:
        return result
    if deadline - loop.time() < loop.time() - started:
        logger.info("Skipping generated query repair: not enough time left")
        return result

    logger.info(
        "Generated query needs repair",
        extra={"error_count": len(result.errors), "warning_count": len(result.warnings)},
    )
    try:
        async with asyncio.timeout_at(deadline):
            repaired = await _invoke(chat_graph, model, [*messages, *_repair_messages(result)], config)
    except TimeoutError:
        logger.info("Generated query repair timed out; returning the first query")
        return result
    final = _answered_by(await _validated(repaired), spec)
    # A repair that broke a query which only had warnings is worse than the
    # original: keep the runnable one.
    if result.valid and not final.valid:
        return result
    return final


def _answered_by(result: QueryGeneration, spec: chat_models.ModelSpec) -> QueryGeneration:
    return replace(
        result,
        model_id=spec.model_id,
        reasoning_effort=spec.reasoning_effort,
        profile_name=spec.profile_name,
    )


async def _invoke(
    chat_graph: Any,
    model: Any,
    messages: list[BaseMessage],
    config: RunnableConfig,
) -> GeneratedCypher:
    output = await chat_graph._invoke_structured_output(
        model,
        GeneratedCypher,
        messages,
        config,
        phase=_PHASE,
        max_output_tokens=_MAX_OUTPUT_TOKENS,
    )
    if isinstance(output, GeneratedCypher):
        return output
    return GeneratedCypher.model_validate(output.model_dump())


async def _validated(generated: GeneratedCypher) -> QueryGeneration:
    query = _strip_fences(generated.query)
    explanation = generated.explanation.strip()
    if not query:
        return QueryGeneration(query="", explanation=explanation, errors=["The model did not return a query."])
    validation = await validate_query(query)
    return QueryGeneration(
        query=query,
        explanation=explanation,
        errors=[str(error) for error in validation.errors],
        warnings=[str(warning) for warning in validation.warnings],
    )


def _strip_fences(text: str) -> str:
    """Remove a Markdown code fence the model wrapped the query in anyway."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


async def _schema_text() -> str:
    try:
        schema = await reporting_neo4j.fetch_graph_schema()
    except Exception:
        # The question is still answerable without it, just less reliably.
        logger.warning("Failed to fetch the graph schema for query generation", exc_info=True)
        return "(The graph schema is unavailable.)"
    text = json.dumps(
        {
            "labels": schema.get("labels", []),
            "relationship_types": schema.get("relationship_types", []),
            "property_keys": schema.get("property_keys", []),
        },
        separators=(",", ":"),
    )
    if len(text) > _MAX_SCHEMA_CHARS:
        text = text[:_MAX_SCHEMA_CHARS] + " …(truncated)"
    return text


def _initial_messages(question: str, current_query: str, schema_text: str) -> list[BaseMessage]:
    parts = [f"Graph schema (names only):\n{schema_text}"]
    if current_query.strip():
        parts.append(
            "The query currently in the editor, which the question may be refining:\n"
            f"```cypher\n{current_query.strip()}\n```"
        )
    parts.append(f"Question:\n{question.strip()}")
    return [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content="\n\n".join(parts))]


def _repair_messages(result: QueryGeneration) -> list[BaseMessage]:
    problems = [*(f"Error: {error}" for error in result.errors), *(f"Warning: {w}" for w in result.warnings)]
    return [
        HumanMessage(
            content=(
                "You returned this query:\n"
                f"```cypher\n{result.query}\n```\n\n"
                "The validator reported:\n"
                + "\n".join(f"- {problem}" for problem in problems)
                + "\n\nReturn a corrected query. Warnings about labels, relationships or properties "
                "mean they do not exist in this graph; use names from the schema instead."
            )
        )
    ]
