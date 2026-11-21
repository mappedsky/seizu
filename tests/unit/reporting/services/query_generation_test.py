import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from reporting.services import model_profiles, query_generation
from reporting.services.chat_budget import BudgetExceeded
from reporting.services.chat_models import ModelSpec
from reporting.services.query_generation import (
    GeneratedCypher,
    QueryGenerationTimeout,
    QueryGenerationUnavailable,
    generate_query,
)
from reporting.services.query_validator import ValidationResult


@pytest.fixture
def real_provider(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "litellm")
    mocker.patch("reporting.settings.CHAT_LLM_MODEL", "openai/gpt-4o-mini")
    mocker.patch(
        "reporting.services.model_profiles.report_store.list_model_profiles",
        AsyncMock(return_value=[]),
    )
    mocker.patch("reporting.services.chat_graph.build_chat_model", return_value=MagicMock())
    mocker.patch(
        "reporting.services.query_generation.reporting_neo4j.fetch_graph_schema",
        AsyncMock(return_value={"labels": ["AWSAccount"], "relationship_types": ["RESOURCE"], "property_keys": ["id"]}),
    )


def _model_returns(mocker, *outputs: GeneratedCypher) -> AsyncMock:
    invoke = AsyncMock(side_effect=list(outputs))
    mocker.patch("reporting.services.chat_graph._invoke_structured_output", invoke)
    return invoke


def _validator(mocker, *results: ValidationResult) -> AsyncMock:
    validate = AsyncMock(side_effect=list(results))
    mocker.patch("reporting.services.query_generation.validate_query", validate)
    return validate


async def test_mock_provider_returns_a_fixed_query_without_a_model(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "mock")
    build = mocker.patch("reporting.services.chat_graph.build_chat_model")
    _validator(mocker, ValidationResult())

    result = await generate_query("show me everything")

    assert result.query == query_generation._MOCK_QUERY
    assert result.valid
    build.assert_not_called()


async def test_valid_first_answer_is_not_repaired(mocker, real_provider):
    invoke = _model_returns(mocker, GeneratedCypher(query="MATCH (a:AWSAccount) RETURN a LIMIT 25", explanation="x"))
    _validator(mocker, ValidationResult())

    result = await generate_query("list aws accounts")

    assert result.query == "MATCH (a:AWSAccount) RETURN a LIMIT 25"
    assert result.valid
    assert invoke.await_count == 1
    prompt = invoke.await_args.args[2][-1].content
    assert "AWSAccount" in prompt
    assert "list aws accounts" in prompt


async def test_current_query_is_offered_as_context(mocker, real_provider):
    invoke = _model_returns(mocker, GeneratedCypher(query="MATCH (n) RETURN n LIMIT 5", explanation="x"))
    _validator(mocker, ValidationResult())

    await generate_query("only five", current_query="MATCH (n) RETURN n LIMIT 25")

    assert "MATCH (n) RETURN n LIMIT 25" in invoke.await_args.args[2][-1].content


async def test_invalid_query_is_repaired_once_with_the_diagnostics(mocker, real_provider):
    invoke = _model_returns(
        mocker,
        GeneratedCypher(query="MATCH (a:Acount) RETURN a", explanation="first"),
        GeneratedCypher(query="MATCH (a:AWSAccount) RETURN a LIMIT 25", explanation="second"),
    )
    _validator(mocker, ValidationResult(errors=["Invalid input 'Acount'"]), ValidationResult())

    result = await generate_query("list aws accounts")

    assert invoke.await_count == 2
    repair_prompt = invoke.await_args_list[1].args[2][-1].content
    assert "Invalid input 'Acount'" in repair_prompt
    assert "MATCH (a:Acount) RETURN a" in repair_prompt
    assert result.query == "MATCH (a:AWSAccount) RETURN a LIMIT 25"
    assert result.explanation == "second"
    assert result.valid


async def test_failed_repair_is_returned_with_its_errors(mocker, real_provider):
    _model_returns(
        mocker,
        GeneratedCypher(query="MATCH (a RETURN a", explanation="first"),
        GeneratedCypher(query="MATCH (a RETURN b", explanation="second"),
    )
    _validator(mocker, ValidationResult(errors=["syntax"]), ValidationResult(errors=["still syntax"]))

    result = await generate_query("list aws accounts")

    assert not result.valid
    assert result.query == "MATCH (a RETURN b"
    assert result.errors == ["still syntax"]


async def test_repair_that_breaks_a_warned_query_keeps_the_original(mocker, real_provider):
    _model_returns(
        mocker,
        GeneratedCypher(query="MATCH (a:AWSAccount) RETURN a.nope LIMIT 25", explanation="first"),
        GeneratedCypher(query="MATCH (a RETURN", explanation="second"),
    )
    _validator(
        mocker,
        ValidationResult(warnings=["Property nope does not exist"]),
        ValidationResult(errors=["syntax"]),
    )

    result = await generate_query("list aws accounts")

    assert result.query == "MATCH (a:AWSAccount) RETURN a.nope LIMIT 25"
    assert result.valid
    assert result.warnings == ["Property nope does not exist"]


async def test_code_fences_are_stripped(mocker, real_provider):
    _model_returns(mocker, GeneratedCypher(query="```cypher\nMATCH (n) RETURN n LIMIT 1\n```", explanation="x"))
    validate = _validator(mocker, ValidationResult())

    result = await generate_query("one node")

    assert result.query == "MATCH (n) RETURN n LIMIT 1"
    validate.assert_awaited_once_with("MATCH (n) RETURN n LIMIT 1")


async def test_empty_query_is_an_error_without_validating(mocker, real_provider):
    _model_returns(mocker, GeneratedCypher(query="  ", explanation=""), GeneratedCypher(query="", explanation=""))
    validate = _validator(mocker)

    result = await generate_query("nonsense")

    assert not result.valid
    assert result.errors
    validate.assert_not_awaited()


async def test_unavailable_model_profile_raises(mocker, real_provider):
    mocker.patch(
        "reporting.services.query_generation.model_profiles.resolve_query_console",
        AsyncMock(side_effect=model_profiles.ModelProfileUnavailable("none")),
    )

    with pytest.raises(QueryGenerationUnavailable):
        await generate_query("anything")


async def test_schema_failure_does_not_stop_generation(mocker, real_provider):
    mocker.patch(
        "reporting.services.query_generation.reporting_neo4j.fetch_graph_schema",
        AsyncMock(side_effect=RuntimeError("neo4j down")),
    )
    invoke = _model_returns(mocker, GeneratedCypher(query="MATCH (n) RETURN n LIMIT 1", explanation="x"))
    _validator(mocker, ValidationResult())

    result = await generate_query("one node")

    assert result.valid
    assert "unavailable" in invoke.await_args.args[2][-1].content


async def test_first_answer_past_the_deadline_raises_timeout(mocker, real_provider):
    mocker.patch("reporting.settings.QUERY_GENERATION_TIMEOUT_SECONDS", 1)

    async def slow(*_args, **_kwargs):
        await asyncio.sleep(5)

    mocker.patch("reporting.services.chat_graph._invoke_structured_output", side_effect=slow)

    with pytest.raises(QueryGenerationTimeout):
        await generate_query("anything")


async def test_repair_past_the_deadline_returns_the_first_query(mocker, real_provider):
    mocker.patch("reporting.settings.QUERY_GENERATION_TIMEOUT_SECONDS", 1)
    calls = 0

    async def first_fast_then_slow(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return GeneratedCypher(query="MATCH (a:Nope) RETURN a LIMIT 1", explanation="first")
        await asyncio.sleep(5)

    mocker.patch("reporting.services.chat_graph._invoke_structured_output", side_effect=first_fast_then_slow)
    _validator(mocker, ValidationResult(warnings=["Label Nope does not exist"]))

    result = await generate_query("anything")

    assert calls == 2
    assert result.query == "MATCH (a:Nope) RETURN a LIMIT 1"
    assert result.warnings == ["Label Nope does not exist"]


async def test_repair_is_skipped_when_the_first_call_used_most_of_the_time(mocker, real_provider):
    mocker.patch("reporting.settings.QUERY_GENERATION_TIMEOUT_SECONDS", 2)

    async def slowish(*_args, **_kwargs):
        await asyncio.sleep(1.2)
        return GeneratedCypher(query="MATCH (a RETURN a", explanation="first")

    invoke = mocker.patch("reporting.services.chat_graph._invoke_structured_output", side_effect=slowish)
    _validator(mocker, ValidationResult(errors=["syntax"]))

    result = await generate_query("anything")

    assert invoke.await_count == 1
    assert result.errors == ["syntax"]


async def test_slow_validation_is_bounded_by_the_deadline(mocker, real_provider):
    mocker.patch("reporting.settings.QUERY_GENERATION_TIMEOUT_SECONDS", 1)
    invoke = _model_returns(mocker, GeneratedCypher(query="MATCH (n) RETURN n LIMIT 1", explanation="x"))

    async def slow_validation(*_args, **_kwargs):
        await asyncio.sleep(5)

    mocker.patch("reporting.services.query_generation.validate_query", side_effect=slow_validation)
    loop = asyncio.get_running_loop()
    started = loop.time()

    result = await generate_query("one node")

    assert loop.time() - started < 2
    assert invoke.await_count == 1
    assert result.query == "MATCH (n) RETURN n LIMIT 1"
    assert not result.valid
    assert "did not finish in time" in result.errors[0]


async def test_slow_schema_fetch_is_bounded_by_the_deadline(mocker, real_provider):
    mocker.patch("reporting.settings.QUERY_GENERATION_TIMEOUT_SECONDS", 1)

    async def slow_schema(*_args, **_kwargs):
        await asyncio.sleep(5)

    mocker.patch("reporting.services.query_generation.reporting_neo4j.fetch_graph_schema", side_effect=slow_schema)
    invoke = _model_returns(mocker)

    with pytest.raises(QueryGenerationTimeout):
        await generate_query("anything")
    invoke.assert_not_awaited()


async def test_unaffordable_repair_keeps_the_validated_first_query(mocker, real_provider):
    calls = 0

    async def first_then_over_budget(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return GeneratedCypher(query="MATCH (a:Nope) RETURN a LIMIT 1", explanation="first")
        raise BudgetExceeded("cost")

    mocker.patch("reporting.services.chat_graph._invoke_structured_output", side_effect=first_then_over_budget)
    _validator(mocker, ValidationResult(warnings=["Label Nope does not exist"]))

    result = await generate_query("anything")

    assert calls == 2
    assert result.valid
    assert result.query == "MATCH (a:Nope) RETURN a LIMIT 1"
    assert result.warnings == ["Label Nope does not exist"]


async def test_generation_uses_the_query_console_choice_and_reports_it(mocker, real_provider):
    resolve = mocker.patch(
        "reporting.services.query_generation.model_profiles.resolve_query_console",
        AsyncMock(
            return_value=model_profiles.StandaloneModelChoice(
                spec=ModelSpec(
                    model_id="openai/fast",
                    max_output_tokens=4096,
                    reasoning_effort="low",
                    role="query_generation",
                    profile_name="Fast",
                ),
                cost_budget_usd=1.0,
            )
        ),
    )
    build = mocker.patch("reporting.services.chat_graph.build_chat_model", return_value=MagicMock())
    _model_returns(mocker, GeneratedCypher(query="MATCH (n) RETURN n LIMIT 1", explanation="x"))
    _validator(mocker, ValidationResult())

    result = await generate_query("one node", model_profile_id="p1", reasoning_effort="high")

    resolve.assert_awaited_once_with("p1", "high")
    assert build.call_args.args[0].model_id == "openai/fast"
    assert (result.model_id, result.reasoning_effort, result.profile_name) == ("openai/fast", "low", "Fast")
