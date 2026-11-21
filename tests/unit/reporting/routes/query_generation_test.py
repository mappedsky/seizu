from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS, Permission
from reporting.schema.model_profiles import QueryConsoleModelOption, QueryConsoleModelOptions
from reporting.schema.report_config import QueryHistoryItem, User
from reporting.services.query_generation import QueryGeneration, QueryGenerationTimeout, QueryGenerationUnavailable

_USER = User(
    user_id="test-user-id",
    sub="sub123",
    iss="https://idp.example.com",
    email="test@example.com",
    created_at="2024-01-01T00:00:00+00:00",
    last_login="2024-01-01T00:00:00+00:00",
)
_CURRENT = CurrentUser(user=_USER, jwt_claims={}, permissions=ALL_PERMISSIONS)


@pytest.fixture(autouse=True)
def _chat_enabled(mocker):
    mocker.patch("reporting.settings.CHAT_ENABLED", True)


def _client(current: CurrentUser = _CURRENT) -> AsyncClient:
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _history_item(query: str, prompt: str) -> QueryHistoryItem:
    return QueryHistoryItem(
        history_id="h1", user_id=_USER.user_id, query=query, executed_at="2026-01-01T00:00:00+00:00", prompt=prompt
    )


async def test_valid_query_is_recorded_with_its_prompt(mocker):
    mocker.patch(
        "reporting.routes.query_generation.generate_query",
        AsyncMock(return_value=QueryGeneration(query="MATCH (n) RETURN n LIMIT 25", explanation="All nodes.")),
    )
    save = mocker.patch(
        "reporting.routes.query_generation.report_store.save_query_history",
        AsyncMock(return_value=_history_item("MATCH (n) RETURN n LIMIT 25", "show nodes")),
    )

    async with _client() as client:
        ret = await client.post("/api/v1/query/generate", json={"question": "  show nodes "})

    assert ret.status_code == 200
    body = ret.json()
    assert body["query"] == "MATCH (n) RETURN n LIMIT 25"
    assert body["explanation"] == "All nodes."
    assert body["history_id"] == "h1"
    save.assert_awaited_once_with(user_id=_USER.user_id, query="MATCH (n) RETURN n LIMIT 25", prompt="show nodes")


async def test_current_query_is_passed_through(mocker):
    generate = mocker.patch(
        "reporting.routes.query_generation.generate_query",
        AsyncMock(return_value=QueryGeneration(query="MATCH (n) RETURN n LIMIT 5", explanation="")),
    )
    mocker.patch(
        "reporting.routes.query_generation.report_store.save_query_history",
        AsyncMock(return_value=_history_item("MATCH (n) RETURN n LIMIT 5", "fewer")),
    )

    async with _client() as client:
        await client.post(
            "/api/v1/query/generate",
            json={"question": "fewer", "current_query": "MATCH (n) RETURN n LIMIT 25"},
        )

    generate.assert_awaited_once_with(
        "fewer",
        current_query="MATCH (n) RETURN n LIMIT 25",
        model_profile_id=None,
        reasoning_effort=None,
    )


async def test_profile_and_effort_are_passed_through_and_reported(mocker):
    generate = mocker.patch(
        "reporting.routes.query_generation.generate_query",
        AsyncMock(
            return_value=QueryGeneration(
                query="MATCH (n) RETURN n LIMIT 5",
                explanation="",
                model_id="openai/fast",
                reasoning_effort="low",
                profile_name="Fast",
            )
        ),
    )
    mocker.patch(
        "reporting.routes.query_generation.report_store.save_query_history",
        AsyncMock(return_value=_history_item("MATCH (n) RETURN n LIMIT 5", "q")),
    )

    async with _client() as client:
        ret = await client.post(
            "/api/v1/query/generate",
            json={"question": "q", "model_profile_id": "p1", "reasoning_effort": "high"},
        )

    assert generate.await_args.kwargs["model_profile_id"] == "p1"
    assert generate.await_args.kwargs["reasoning_effort"] == "high"
    body = ret.json()
    assert (body["model_id"], body["reasoning_effort"], body["profile_name"]) == ("openai/fast", "low", "Fast")


async def test_effort_without_a_profile_is_rejected():
    async with _client() as client:
        ret = await client.post("/api/v1/query/generate", json={"question": "q", "reasoning_effort": "high"})

    assert ret.status_code == 422


async def test_invalid_query_is_returned_but_not_recorded(mocker):
    mocker.patch(
        "reporting.routes.query_generation.generate_query",
        AsyncMock(return_value=QueryGeneration(query="MATCH (n RETURN n", explanation="", errors=["syntax"])),
    )
    save = mocker.patch("reporting.routes.query_generation.report_store.save_query_history", AsyncMock())

    async with _client() as client:
        ret = await client.post("/api/v1/query/generate", json={"question": "nodes"})

    assert ret.status_code == 200
    assert ret.json()["errors"] == ["syntax"]
    assert ret.json()["history_id"] is None
    save.assert_not_awaited()


async def test_history_failure_still_returns_the_query(mocker):
    mocker.patch(
        "reporting.routes.query_generation.generate_query",
        AsyncMock(return_value=QueryGeneration(query="MATCH (n) RETURN n LIMIT 25", explanation="")),
    )
    mocker.patch(
        "reporting.routes.query_generation.report_store.save_query_history",
        AsyncMock(side_effect=RuntimeError("db down")),
    )

    async with _client() as client:
        ret = await client.post("/api/v1/query/generate", json={"question": "nodes"})

    assert ret.status_code == 200
    assert ret.json()["query"] == "MATCH (n) RETURN n LIMIT 25"
    assert ret.json()["history_id"] is None


async def test_unavailable_model_is_503(mocker):
    mocker.patch(
        "reporting.routes.query_generation.generate_query",
        AsyncMock(side_effect=QueryGenerationUnavailable("no profile")),
    )

    async with _client() as client:
        ret = await client.post("/api/v1/query/generate", json={"question": "nodes"})

    assert ret.status_code == 503
    assert ret.json()["error"] == "no profile"


async def test_timeout_is_504(mocker):
    mocker.patch(
        "reporting.routes.query_generation.generate_query",
        AsyncMock(side_effect=QueryGenerationTimeout()),
    )

    async with _client() as client:
        ret = await client.post("/api/v1/query/generate", json={"question": "nodes"})

    assert ret.status_code == 504


async def test_model_failure_is_502_without_detail(mocker):
    mocker.patch(
        "reporting.routes.query_generation.generate_query",
        AsyncMock(side_effect=RuntimeError("provider said secret things")),
    )

    async with _client() as client:
        ret = await client.post("/api/v1/query/generate", json={"question": "nodes"})

    assert ret.status_code == 502
    assert "secret" not in ret.text


async def test_empty_question_is_rejected():
    async with _client() as client:
        ret = await client.post("/api/v1/query/generate", json={"question": ""})

    assert ret.status_code == 422


@pytest.mark.parametrize("missing", [Permission.CHAT_USE, Permission.QUERY_EXECUTE])
async def test_requires_both_chat_and_query_permissions(mocker, missing):
    generate = mocker.patch("reporting.routes.query_generation.generate_query", AsyncMock())
    current = CurrentUser(user=_USER, jwt_claims={}, permissions=ALL_PERMISSIONS - {missing.value})

    async with _client(current) as client:
        ret = await client.post("/api/v1/query/generate", json={"question": "nodes"})

    assert ret.status_code == 403
    generate.assert_not_awaited()


async def test_route_absent_when_chat_disabled(mocker):
    mocker.patch("reporting.settings.CHAT_ENABLED", False)

    async with _client() as client:
        ret = await client.post("/api/v1/query/generate", json={"question": "nodes"})

    assert ret.status_code in (404, 405)


async def test_route_absent_when_generation_disabled(mocker):
    mocker.patch("reporting.settings.QUERY_GENERATION_ENABLED", False)

    async with _client() as client:
        ret = await client.post("/api/v1/query/generate", json={"question": "nodes"})

    assert ret.status_code in (404, 405)


async def test_options_list_the_profiles_enabled_for_the_console(mocker):
    mocker.patch(
        "reporting.routes.query_generation.model_profiles.query_console_options",
        AsyncMock(
            return_value=QueryConsoleModelOptions(
                available=True,
                profiles=[
                    QueryConsoleModelOption(
                        profile_id="p1",
                        name="Fast",
                        description="",
                        model_id="openai/fast",
                        default_reasoning_effort="low",
                        reasoning_efforts=("low",),
                    )
                ],
                default_profile_id="p1",
            )
        ),
    )
    async with _client() as client:
        response = await client.get("/api/v1/query/generate/options")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["profiles"][0]["reasoning_efforts"] == ["low"]
    assert body["default_profile_id"] == "p1"


@pytest.mark.parametrize("missing", [Permission.QUERY_EXECUTE, Permission.CHAT_USE])
async def test_options_require_both_chat_and_query_permissions(missing):
    current = CurrentUser(user=_USER, jwt_claims={}, permissions=ALL_PERMISSIONS - {missing.value})
    async with _client(current) as client:
        response = await client.get("/api/v1/query/generate/options")

    assert response.status_code == 403
