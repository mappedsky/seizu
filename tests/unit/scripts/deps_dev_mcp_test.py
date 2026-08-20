"""The deps.dev proxy's own logic: path building, graph walking, argument guards."""

from typing import Any

import pytest

from scripts import deps_dev_mcp as mod


def test_a_version_key_escapes_names_that_need_it():
    assert mod._version_key("pypi", "botocore", "1.34.100").endswith("/packages/botocore/versions/1.34.100")
    # Maven coordinates carry a colon; npm scopes carry a slash. Neither may
    # become another path segment.
    assert "%3A" in mod._version_key("maven", "org.slf4j:slf4j-api", "2.0.9")
    assert "%2F" in mod._version_key("npm", "@types/node", "20.0.0")


def test_the_ecosystem_is_spelled_the_way_the_api_wants():
    assert "/systems/PYPI/" in mod._version_key("pypi", "x", "1")
    assert "/systems/RUBYGEMS/" in mod._version_key("rubygems", "x", "1")


async def test_a_dependency_path_is_a_chain_not_a_graph():
    """The answer to "does this pull in urllib3" is the chain, not 400 nodes."""
    payload = {
        "nodes": [
            {"versionKey": {"name": "confidant-dep", "version": "1.0"}, "relation": "SELF"},
            {"versionKey": {"name": "botocore", "version": "1.34.100"}, "relation": "DIRECT"},
            {"versionKey": {"name": "urllib3", "version": "2.7.0"}, "relation": "INDIRECT"},
        ],
        "edges": [{"fromNode": 0, "toNode": 1}, {"fromNode": 1, "toNode": 2}],
    }

    async def _fake_get(_client: Any, _path: str) -> Any:
        return payload

    result = await mod._dependency_path(
        None, {"system": "pypi", "name": "confidant-dep", "version": "1.0", "target": "urllib3"}, get=_fake_get
    )

    assert result["pulls_in"] is True
    assert result["paths"] == ["urllib3@2.7.0 <- botocore@1.34.100 <- confidant-dep@1.0"]


async def test_a_target_that_is_not_pulled_in_says_so_plainly():
    async def _fake_get(_client: Any, _path: str) -> Any:
        return {"nodes": [{"versionKey": {"name": "jmespath", "version": "1.0.1"}, "relation": "SELF"}], "edges": []}

    result = await mod._dependency_path(
        None, {"system": "pypi", "name": "jmespath", "version": "1.0.1", "target": "urllib3"}, get=_fake_get
    )

    # Not an error: "no" is an answer, and the sub-agent must not read it as a
    # failed lookup and go back to guessing.
    assert result["pulls_in"] is False
    assert "not in the resolved graph" in result["note"]


async def test_a_cycle_in_the_graph_cannot_hang_the_walk():
    payload = {
        "nodes": [
            {"versionKey": {"name": "a", "version": "1"}, "relation": "SELF"},
            {"versionKey": {"name": "b", "version": "1"}, "relation": "DIRECT"},
        ],
        "edges": [{"fromNode": 0, "toNode": 1}, {"fromNode": 1, "toNode": 0}],
    }

    async def _fake_get(_client: Any, _path: str) -> Any:
        return payload

    result = await mod._dependency_path(
        None, {"system": "pypi", "name": "a", "version": "1", "target": "b"}, get=_fake_get
    )

    assert result["pulls_in"] is True
    assert len(result["paths"][0].split(" <- ")) <= 64


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"system": "pypi", "name": "x"}, "version"),
        ({"system": "pypi", "version": "1"}, "name"),
        ({"system": "cobol", "name": "x", "version": "1"}, "Unknown ecosystem"),
    ],
)
async def test_bad_arguments_are_reported_not_sent_upstream(args: dict[str, Any], expected: str):
    result = await mod._call_tool(None, mod.CallToolRequestParams(name="get_requirements", arguments=args))

    assert result.is_error is True
    assert expected in result.content[0].text


async def test_an_unknown_tool_is_an_error_not_an_exception():
    result = await mod._call_tool(None, mod.CallToolRequestParams(name="nope", arguments={}))
    assert result.is_error is True
