import asyncio
from unittest.mock import AsyncMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

from reporting.services.mcp_builtins.graph import _handle_query
from reporting.services.query_validator import ValidationResult


async def _call_graph_query(args: dict[str, object]) -> dict[str, object]:
    with (
        patch(
            "reporting.services.mcp_builtins.graph.validate_query",
            new=AsyncMock(return_value=ValidationResult()),
        ),
        patch(
            "reporting.services.mcp_builtins.graph.reporting_neo4j.run_query_streamed",
            new=AsyncMock(return_value=([{"value": 1}], False)),
        ),
    ):
        return await _handle_query(args, current_user=None)


@settings(max_examples=75)
@given(query=st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=120)))
def test_graph_query_fuzzed_query_argument_never_raises(query: object) -> None:
    result = asyncio.run(_call_graph_query({"query": query}))

    assert "error" in result or "results" in result or "errors" in result


@settings(max_examples=75)
@given(
    args=st.dictionaries(
        st.text(max_size=12),
        st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=80)),
        max_size=5,
    )
)
def test_graph_query_fuzzed_argument_map_never_raises(args: dict[str, object]) -> None:
    result = asyncio.run(_call_graph_query(args))

    assert "error" in result or "results" in result or "errors" in result
