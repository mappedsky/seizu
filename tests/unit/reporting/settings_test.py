"""Settings parsing that is not a plain env read."""

import os
from unittest.mock import patch

from reporting.settings import _DEFAULT_SANDBOX_CORE_TOOLS, _core_tools_from_env


async def test_unset_core_tools_uses_the_default() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SANDBOX_CORE_TOOLS", None)
        assert _core_tools_from_env() == _DEFAULT_SANDBOX_CORE_TOOLS


async def test_an_explicitly_empty_core_tools_means_no_tools() -> None:
    """`SANDBOX_CORE_TOOLS=` is the documented way to bind nothing up front.

    Regression test: this went through `list_env`, which treats an empty value
    as absent and returns the default, so the documented disable path silently
    kept all four graph tools. The harness's read-back check caught it; nothing
    in the test suite would have.
    """
    with patch.dict(os.environ, {"SANDBOX_CORE_TOOLS": ""}):
        assert _core_tools_from_env() == []


async def test_core_tools_are_split_and_stripped() -> None:
    with patch.dict(os.environ, {"SANDBOX_CORE_TOOLS": "graph__schema, graph__query ,"}):
        assert _core_tools_from_env() == ["graph__schema", "graph__query"]
