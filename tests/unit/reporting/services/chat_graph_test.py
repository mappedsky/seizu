import asyncio
import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.modifier import RemoveMessage
from mcp.types import Prompt, PromptArgument, Tool
from pydantic import BaseModel

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.confirmations import ActionConfirmation
from reporting.schema.report_config import User
from reporting.services import chat_graph
from reporting.services.chat_messages import MessageTag, has_tag
from reporting.services.mcp_runtime import ChatActionOutcome, ChatBlockReason

_NOW = "2024-01-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _single_agent_path(mocker):
    """Pin the orchestrator off for the single-agent test module.

    These tests target chat_agent_node directly; the orchestrated path has its
    own suite. Without this, an ambient CHAT_ORCHESTRATOR_ENABLED=true in the
    environment makes the router run (and consume the mocked model turns these
    tests assert on), so the suite would be non-hermetic.
    """
    mocker.patch("reporting.settings.CHAT_ORCHESTRATOR_ENABLED", False)


def _user() -> CurrentUser:
    return CurrentUser(
        user=User(
            user_id="user-1",
            sub="sub",
            iss="iss",
            email="user@example.com",
            created_at=_NOW,
            last_login=_NOW,
        ),
        jwt_claims={},
        permissions=frozenset(
            {
                Permission.CHAT_TOOLS_CALL.value,
                Permission.TOOLS_CALL.value,
                Permission.CHAT_SKILLS_CALL.value,
                Permission.SKILLS_RENDER.value,
            }
        ),
    )


def _tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"name": name, "args": args, "id": call_id}


class _Structured:
    def __init__(self, result: Any) -> None:
        self.result = result

    async def ainvoke(self, _messages: Any, config: Any = None) -> Any:
        return self.result


class _ToolCallingFakeModel:
    def __init__(self, responses: list[AIMessage | AIMessageChunk]) -> None:
        self.responses = responses
        self.calls = 0
        self.inputs = []
        self.bound_tools = []

    def bind_tools(self, tools):
        self.bound_tools.append(tools)
        return self

    def with_structured_output(self, schema):
        # The single-agent loop no longer uses structured output; this is only
        # reached if a test drives the orchestrator router through this model.
        raise AssertionError(f"unexpected structured-output schema {schema!r}")

    async def astream(self, input, config=None, **kwargs):
        self.inputs.append(input)
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        yield self.responses[index]


def test_chunk_reasoning_delta_reads_both_litellm_reasoning_shapes():
    # LiteLLM surfaces DeepSeek/OpenAI-shape reasoning in additional_kwargs...
    kwargs_chunk = AIMessageChunk(content="", additional_kwargs={"reasoning_content": "checked tools"})
    assert chat_graph._chunk_reasoning_delta(kwargs_chunk) == "checked tools"

    # ...and Anthropic-shape reasoning as an injected thinking content block.
    thinking_chunk = AIMessageChunk(content=[{"type": "thinking", "thinking": "weighing options"}])
    assert chat_graph._chunk_reasoning_delta(thinking_chunk) == "weighing options"


def test_litellm_model_id_namespaces_legacy_provider(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_MODEL", "claude-3-5-sonnet-latest")
    assert chat_graph._litellm_model_id("anthropic") == "anthropic/claude-3-5-sonnet-latest"


def test_litellm_model_id_passes_through_qualified_and_sentinel(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_MODEL", "anthropic/claude-3-5-sonnet-latest")
    assert chat_graph._litellm_model_id("anthropic") == "anthropic/claude-3-5-sonnet-latest"
    mocker.patch("reporting.settings.CHAT_LLM_MODEL", "gpt-4o")
    assert chat_graph._litellm_model_id("litellm") == "gpt-4o"


def test_strip_reasoning_context_flattens_mixed_list_content_to_text():
    # Mirrors LiteLLM's streamed+merged shape: thinking dicts concatenated with a
    # bare answer-text string in one list. This is the shape that crashed
    # DeepSeek's content-list-to-str conversion when re-sent in the tool loop.
    message = AIMessage(
        content=[{"type": "thinking", "thinking": "hidden"}, "Answer."],
        additional_kwargs={"reasoning_content": "hidden"},
    )
    stripped = chat_graph._strip_reasoning_context(message)
    assert "reasoning_content" not in stripped.additional_kwargs
    assert stripped.content == "Answer."


def test_strip_reasoning_context_preserves_tool_call_reasoning_for_deepseek():
    message = AIMessage(
        content=[{"type": "thinking", "thinking": "hidden"}, ""],
        additional_kwargs={"reasoning_content": "hidden"},
        tool_calls=[_tool_call("graph__schema", {}, "call_1")],
    )

    stripped = chat_graph._strip_reasoning_context(message)

    assert stripped.additional_kwargs["reasoning_content"] == "hidden"
    assert stripped.content == ""
    assert [call["id"] for call in stripped.tool_calls] == ["call_1"]


def test_ai_message_for_tool_results_preserves_reasoning_while_filtering_tool_calls():
    message = AIMessage(
        content="",
        additional_kwargs={"reasoning_content": "hidden"},
        tool_calls=[
            _tool_call("graph__schema", {}, "call_1"),
            _tool_call("toolsets__list", {}, "call_2"),
        ],
    )
    request = chat_graph.ToolCallRequest(
        id="call_1",
        name="graph__schema",
        arguments={},
        spec=chat_graph.ChatToolSpec(
            name="graph__schema",
            kind="tool",
            description="Graph schema",
            input_schema={"type": "object"},
        ),
    )

    filtered = chat_graph._ai_message_for_tool_results(
        message,
        [chat_graph.ToolCallResult(request=request, content="{}")],
    )

    assert filtered.additional_kwargs["reasoning_content"] == "hidden"
    assert [call["id"] for call in filtered.tool_calls] == ["call_1"]


async def test_invoke_structured_output_falls_back_to_json_text():
    class _Decision(BaseModel):
        complete: bool
        reason: str = ""

    class _BrokenStructured:
        async def ainvoke(self, _messages, config=None):
            raise RuntimeError("structured output unavailable")

    class _JsonModel:
        def with_structured_output(self, _schema):
            return _BrokenStructured()

        async def astream(self, _input, config=None, **kwargs):
            yield AIMessageChunk(content='{"complete": false, "reason": "more evidence is needed"}')

    result = await chat_graph._invoke_structured_output(
        _JsonModel(),
        _Decision,
        [HumanMessage(content="decide")],
        {},
    )

    assert isinstance(result, _Decision)
    assert result.complete is False
    assert result.reason == "more evidence is needed"


def test_structured_from_text_extracts_object_from_fences_and_prose():
    class _Plan(BaseModel):
        steps: list[str]

    text = 'Let me plan this out.\n```json\n{"steps": ["s1", "s2"]}\n```\nThat should work.'
    parsed = chat_graph._structured_from_text(_Plan, text)
    assert parsed is not None and parsed.steps == ["s1", "s2"]


def test_structured_from_text_skips_objects_that_do_not_match_schema():
    class _Decision(BaseModel):
        route: str

    # A stray object in the reasoning, then the real one; only the latter matches.
    text = 'First {"note": "thinking out loud"} then the decision {"route": "orchestrate"} done.'
    parsed = chat_graph._structured_from_text(_Decision, text)
    assert parsed is not None and parsed.route == "orchestrate"


def test_balanced_brace_objects_ignores_braces_inside_strings():
    text = 'prefix {"text": "a } brace in a string", "n": 1} suffix'
    assert chat_graph._balanced_brace_objects(text) == ['{"text": "a } brace in a string", "n": 1}']


def test_chat_result_parsers_handle_invalid_and_confirmation_payloads():
    assert chat_graph._blocked_tool_call_reason_label(ChatBlockReason.CONFIRMATION_REQUIRED) == "confirmation required"
    assert chat_graph._blocked_tool_call_reason_label(None) == "blocked"

    assert chat_graph._confirmation_status("not json") is None
    assert chat_graph._confirmation_status("[]") is None
    assert chat_graph._confirmation_status('{"confirmation_required": true, "status": "pending"}') == "pending"

    assert chat_graph._blocked_tool_call_body(" plain failure ") == "plain failure"
    assert (
        chat_graph._blocked_tool_call_body('{"confirmation_required": true, "status": "denied"}')
        == "Action was denied for this confirmation window."
    )
    assert chat_graph._blocked_tool_call_body("{}") == "{}"

    assert chat_graph._tool_result_error_text("not json") is None
    assert chat_graph._tool_result_error_text("[]") is None
    assert chat_graph._tool_result_error_text('{"error": "failed"}') == "failed"

    assert chat_graph._json_objects_from_text("") == []
    assert chat_graph._json_objects_from_text("[1, 2]") == []
    assert chat_graph._balanced_brace_objects(r'{"text": "escaped \\\" quote", "ok": true}') == [
        r'{"text": "escaped \\\" quote", "ok": true}'
    ]


async def test_invoke_structured_output_retries_when_first_response_lacks_json():
    class _Decision(BaseModel):
        ok: bool

    class _Model:
        def __init__(self) -> None:
            self.calls = 0
            self.kwargs: list[dict[str, Any]] = []

        # No with_structured_output -> straight to the JSON-prompt fallback.
        async def astream(self, _input, config=None, **kwargs):
            self.calls += 1
            self.kwargs.append(kwargs)
            content = "Here is my analysis, but no JSON yet." if self.calls == 1 else '{"ok": true}'
            yield AIMessageChunk(content=content)

    model = _Model()
    result = await chat_graph._invoke_structured_output(
        model,
        _Decision,
        [HumanMessage(content="x")],
        {},
        max_output_tokens=4096,
    )
    assert result.ok is True
    assert model.calls == 2
    assert [kwargs["max_tokens"] for kwargs in model.kwargs] == [4096, 4096]


async def test_invoke_structured_output_failure_reports_safe_attempt_diagnostics():
    class _Decision(BaseModel):
        ok: bool

    class _Model:
        async def astream(self, _input, config=None, **kwargs):
            yield AIMessageChunk(content="not json", response_metadata={"finish_reason": "length"})

    with pytest.raises(ValueError, match=r"2 attempts \(chars=8, finish_reason=length; chars=8"):
        await chat_graph._invoke_structured_output(
            _Model(),
            _Decision,
            [HumanMessage(content="sensitive request")],
            {},
        )


async def test_invoke_structured_output_stops_retrying_native_after_unsupported_error():
    class _Decision(BaseModel):
        ok: bool

    class _UnsupportedStructured:
        async def ainvoke(self, _messages, config=None):
            # Mirror the DeepSeek 400 that langchain-litellm surfaces.
            raise RuntimeError('{"error":{"message":"This response_format type is unavailable now"}}')

    class _Model:
        def __init__(self) -> None:
            self.structured_calls = 0

        def with_structured_output(self, _schema):
            self.structured_calls += 1
            return _UnsupportedStructured()

        async def astream(self, _input, config=None, **kwargs):
            yield AIMessageChunk(content='{"ok": true}')

    model = _Model()
    chat_graph._structured_output_native_ok.pop(id(model), None)

    first = await chat_graph._invoke_structured_output(model, _Decision, [HumanMessage(content="x")], {})
    second = await chat_graph._invoke_structured_output(model, _Decision, [HumanMessage(content="y")], {})

    assert first.ok is True and second.ok is True
    # The unsupported native path is attempted once, then skipped for this model.
    assert model.structured_calls == 1


def test_last_user_request_skips_control_directives():
    messages = [
        HumanMessage(content="Update the CVE toolset to use CVEMetadata"),
        AIMessage(content="Done, pending approval."),
        HumanMessage(
            content="Resume approved confirmation 475a9796",
            additional_kwargs={"resume_confirmation_id": "475a9796"},
        ),
    ]
    # The synthetic resume directive must not be mistaken for the user's request.
    assert chat_graph._last_user_request(messages) == "Update the CVE toolset to use CVEMetadata"

    continuation = [
        HumanMessage(content="Real ask"),
        HumanMessage(content="continue", additional_kwargs={"continue_response": True}),
    ]
    assert chat_graph._last_user_request(continuation) == "Real ask"

    # No directive -> same as the plain last-user-text.
    assert chat_graph._last_user_request([HumanMessage(content="hello")]) == "hello"


def test_terminal_specs_exposed_only_after_an_action_has_run():
    assert chat_graph._terminal_specs(post_action=False) == []
    specs = chat_graph._terminal_specs(post_action=True)
    assert [spec.name for spec in specs] == [chat_graph._FINAL_ANSWER_TOOL]
    assert specs[0].input_schema["required"] == ["answer"]


async def test_run_llm_tool_turn_streams_reasoning_as_detail_and_strips_context():
    class _FakeModel:
        async def astream(self, input, config=None, **kwargs):
            yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": "checking "})
            yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": "graph"})
            yield AIMessageChunk(content="Final answer.")

    events = []

    result = await chat_graph._run_llm_tool_turn(
        _FakeModel(),
        "system",
        [HumanMessage(content="Run overview")],
        [],
        {},
        events.append,
    )

    detail_events = [event for event in events if event["kind"] == "detail"]
    assert detail_events == [
        {
            "kind": "detail",
            "id": detail_events[0]["id"],
            "data": {
                "kind": "thinking",
                "title": "Thinking",
                "status": "running",
            },
        },
        {
            "kind": "detail",
            "id": detail_events[0]["id"],
            "data": {
                "kind": "thinking",
                "title": "Thinking",
                "status": "completed",
                "body": "checking graph",
            },
        },
    ]
    assert result.streamed == "Final answer."
    assert "reasoning_content" not in result.message.additional_kwargs


async def test_chat_graph_streams_final_no_tool_text_deltas_as_they_arrive(mocker):
    """Final no-tool LLM text deltas hit the writer as they arrive.

    Tool-enabled turns are buffered until we know whether the model requested
    tools, but final answer turns can stream live.
    """
    from langgraph.checkpoint.memory import MemorySaver

    class _FakeModel:
        async def astream(self, input, config=None, **kwargs):
            yield AIMessageChunk(content="alpha ")
            yield AIMessageChunk(content="beta ")
            yield AIMessageChunk(content="gamma")

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=_FakeModel())
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_tools_for_user", return_value=[])
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="say it")]},
            {"configurable": {"thread_id": "thread-stream-deltas", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    deltas = [chunk["content"] for chunk in chunks]
    assert deltas == ["alpha ", "beta ", "gamma"]


# Leaked DeepSeek tool-call markup uses the fullwidth bar U+FF5C ("｜").
_LEAK = (
    '<｜｜DSML｜｜tool_calls> <｜｜DSML｜｜invoke name="graph__schema"></｜｜DSML｜｜invoke> </｜｜DSML｜｜tool_calls>'
)


def test_tool_markup_filter_suppresses_marker_split_across_chunks():
    f = chat_graph._ToolMarkupFilter()
    assert f.feed("hello ") == "hello "
    # A lone '<' is held back in case it begins a marker.
    assert f.feed("<") == ""
    # The fullwidth bar completes the marker: detected, and suppressed.
    assert f.feed("｜tool_calls>") == ""
    assert f.detected is True
    assert f.feed(" trailing junk") == ""
    assert f.flush() == ""


def test_tool_markup_filter_passes_through_normal_anglebracket_text():
    f = chat_graph._ToolMarkupFilter()
    assert f.feed("see <details> here") == "see <details> here"
    assert f.flush() == ""
    assert f.detected is False


def test_strip_tool_markup_cuts_at_first_marker():
    assert chat_graph._strip_tool_markup(f"answer\n\n{_LEAK}") == "answer\n\n"
    assert chat_graph._strip_tool_markup("clean text") == "clean text"


def test_leaked_tool_names_extracts_group_action_names_from_markup():
    assert chat_graph._leaked_tool_names(f"thinking...\n\n{_LEAK}") == ("graph__schema",)
    # Ordinary prose with no leaked tool reference yields nothing.
    assert chat_graph._leaked_tool_names("a normal sentence with no tools") == ()


async def test_chat_graph_withholds_leaked_tool_markup_and_retries_once(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    class _LeakThenAnswerModel:
        def __init__(self):
            self.calls = 0
            self.inputs = []

        async def astream(self, input, config=None, **kwargs):
            self.calls += 1
            self.inputs.append(input)
            if self.calls == 1:
                yield AIMessageChunk(content="Let me check.")
                yield AIMessageChunk(content="\n\n")
                yield AIMessageChunk(content=_LEAK)
            else:
                yield AIMessageChunk(content="Here is the answer.")

    model = _LeakThenAnswerModel()
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "deepseek")
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_tools_for_user", return_value=[])
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="inspect the schema")]},
            {"configurable": {"thread_id": "thread-leak-retry", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk["kind"] == "token")
    # The raw protocol markup never reaches the user.
    assert "DSML" not in streamed
    assert "｜" not in streamed
    # The clean prefix and the retried answer both show.
    assert "Let me check." in streamed
    assert "Here is the answer." in streamed
    assert model.calls == 2
    # The retry turn's system prompt names the attempted tool and how to unlock
    # it (render the providing skill), steering the model back into disclosure.
    retry_system_prompt = model.inputs[1][0].content
    assert "graph__schema" in retry_system_prompt
    assert "first call the skill that provides it" in retry_system_prompt


async def test_chat_graph_filters_unexecuted_tool_calls_from_next_context(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    class _TwoToolModel:
        def __init__(self) -> None:
            self.inputs = []
            self.calls = 0

        def bind_tools(self, _tools):
            return self

        async def astream(self, input, config=None, **kwargs):
            self.inputs.append(input)
            self.calls += 1
            if self.calls == 1:
                yield AIMessageChunk(
                    content="",
                    tool_calls=[
                        _tool_call("skillsets__list", {}, "call_1"),
                        _tool_call("toolsets__list", {}, "call_2"),
                    ],
                )
            else:
                yield AIMessageChunk(content="Final synthesis.")

    model = _TwoToolModel()
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", False)
    mocker.patch("reporting.settings.CHAT_LLM_MAX_AUTO_ACTIONS", 1)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_tools_for_user",
        return_value=[
            Tool(name="skillsets__list", description="List skillsets", inputSchema={"type": "object"}),
            Tool(name="toolsets__list", description="List toolsets", inputSchema={"type": "object"}),
        ],
    )
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        return_value=ChatActionOutcome(text='{"ok": true}'),
    )
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="run both")]},
            {"configurable": {"thread_id": "thread-filter-tool-calls", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    assert "Final synthesis." in "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert len(model.inputs) == 2
    second_input = model.inputs[1]
    tool_call_messages = [message for message in second_input if isinstance(message, AIMessage) and message.tool_calls]
    tool_messages = [message for message in second_input if isinstance(message, ToolMessage)]
    assert len(tool_call_messages) == 1
    assert [call["id"] for call in tool_call_messages[0].tool_calls] == ["call_1"]
    assert [message.tool_call_id for message in tool_messages] == ["call_1"]


async def test_chat_graph_degrades_when_tool_markup_leaks_twice(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    class _AlwaysLeakModel:
        def __init__(self):
            self.calls = 0

        async def astream(self, input, config=None, **kwargs):
            self.calls += 1
            yield AIMessageChunk(content=_LEAK)

    model = _AlwaysLeakModel()
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "deepseek")
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_tools_for_user", return_value=[])
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="inspect the schema")]},
            {"configurable": {"thread_id": "thread-leak-degrade", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk["kind"] == "token")
    assert "DSML" not in streamed
    assert "｜" not in streamed
    assert "couldn't complete that request" in streamed
    # Retried exactly once before degrading.
    assert model.calls == 2

    state = await graph.aget_state({"configurable": {"thread_id": "thread-leak-degrade"}})
    persisted = state.values["messages"][-1]
    assert has_tag(persisted, MessageTag.BROKEN)


def test_trim_overlap_removes_repeated_seam():
    assert chat_graph._trim_overlap("alpha beta gamma", "beta gamma delta") == " delta"
    assert chat_graph._trim_overlap("alpha", "totally new text") == "totally new text"


def test_stream_tail_inserts_separator_when_segments_would_jam():
    assert chat_graph._stream_tail("old", "new") == " new"
    assert chat_graph._stream_tail("old", "new", separator="\n\n") == "\n\nnew"
    assert chat_graph._stream_tail("old ", "new") == "new"
    assert chat_graph._stream_tail("old", ".") == "."


class _CutoffModel:
    """Yields scripted (content, finish_reason) per astream call, for testing
    auto-continuation of output-limit-truncated answers."""

    def __init__(self, turns: list[tuple[str, str | None]]) -> None:
        self.turns = turns
        self.calls = 0

    def bind_tools(self, _tools: Any) -> "_CutoffModel":
        return self

    async def astream(self, input, config=None, **kwargs):
        index = min(self.calls, len(self.turns) - 1)
        self.calls += 1
        content, finish_reason = self.turns[index]
        metadata = {"finish_reason": finish_reason} if finish_reason else {}
        yield AIMessageChunk(content=content, response_metadata=metadata)


async def _run_cutoff_graph(mocker, model: _CutoffModel, thread_id: str) -> list[dict]:
    from langgraph.checkpoint.memory import MemorySaver

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_tools_for_user", return_value=[])
    graph = chat_graph.build_chat_graph(MemorySaver())
    return [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="write a long answer")]},
            {"configurable": {"thread_id": thread_id, "current_user": _user()}},
            stream_mode="custom",
        )
    ]


async def test_auto_continuation_stitches_truncated_answer(mocker):
    # First turn is cut off; the continuation repeats the seam, which is trimmed,
    # and the stitched answer streams seamlessly with no cut-off notice.
    model = _CutoffModel(
        [
            ("Hello world, this is the start", "length"),
            ("this is the start and the rest.", "stop"),
        ]
    )
    chunks = await _run_cutoff_graph(mocker, model, "thread-cont-stitch")

    streamed = "".join(c["content"] for c in chunks if c["kind"] == "token")
    assert streamed == "Hello world, this is the start and the rest."
    assert "hit its output limit" not in streamed
    assert {"kind": "finish_reason", "finish_reason": "length"} not in chunks
    assert model.calls == 2


async def test_auto_continuation_stops_on_no_progress(mocker):
    # A continuation that only repeats prior text adds nothing once trimmed, so
    # the loop stops immediately and falls back to the cut-off notice.
    model = _CutoffModel(
        [
            ("Partial answer", "length"),
            ("Partial answer", "length"),
        ]
    )
    chunks = await _run_cutoff_graph(mocker, model, "thread-cont-noprogress")

    streamed = "".join(c["content"] for c in chunks if c["kind"] == "token")
    # "Partial answer" appears once (no duplicated seam), then the notice.
    assert streamed.count("Partial answer") == 1
    assert "hit its output limit" in streamed
    assert model.calls == 2


async def test_auto_continuation_respects_max_loops(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_MAX_CONTINUATIONS", 2)
    # The model never finishes; continuation stops after the loop budget and shows
    # the cut-off notice rather than looping forever.
    model = _CutoffModel(
        [
            ("chunk0 ", "length"),
            ("chunk1 ", "length"),
            ("chunk2 ", "length"),
            ("chunk3 ", "length"),
        ]
    )
    chunks = await _run_cutoff_graph(mocker, model, "thread-cont-maxloops")

    streamed = "".join(c["content"] for c in chunks if c["kind"] == "token")
    assert "chunk0 chunk1 chunk2" in streamed
    assert "chunk3" not in streamed
    assert "hit its output limit" in streamed
    assert model.calls == 3  # initial + 2 continuations


async def test_chat_graph_marks_output_limit_cutoff(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    class _LimitModel:
        async def astream(self, input, config=None, **kwargs):
            yield AIMessageChunk(
                content="partial answer",
                response_metadata={"finish_reason": "length"},
            )

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=_LimitModel())
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_tools_for_user", return_value=[])
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="write a long answer")]},
            {"configurable": {"thread_id": "thread-output-limit", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk["kind"] == "token")
    assert "partial answer" in streamed
    assert "hit its output limit" in streamed
    assert {"kind": "finish_reason", "finish_reason": "length"} in chunks

    state = await graph.aget_state({"configurable": {"thread_id": "thread-output-limit"}})
    persisted = state.values["messages"][-1]
    assert "hit its output limit" in persisted.content


async def test_output_limit_notice_keeps_tool_details_out_of_user_text():
    response, hit_limit = chat_graph._append_output_limit_notice(
        "partial synthesis",
        "length",
        ["Seizu ran tool `toolsets__create_tool`.\n\nResult:\ncreated"],
    )

    assert hit_limit is True
    assert "hit its output limit" in response
    assert "completed tool work before the cutoff" in response
    assert "toolsets__create_tool" not in response
    assert "created" not in response


async def test_chat_graph_streams_tool_enabled_text_as_it_arrives(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    fake_model = _ToolCallingFakeModel(
        [
            AIMessage(content="Inspecting now", tool_calls=[_tool_call("security__one", {"org": "mappedsky"})]),
            AIMessage(content="Final answer."),
        ]
    )
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", False)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_tools_for_user",
        return_value=[Tool(name="security__one", description="One", inputSchema={"type": "object"})],
    )
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        return_value=ChatActionOutcome(text='{"ok": true}'),
    )
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Run the overview")]},
            {"configurable": {"thread_id": "thread-buffer-tool-text", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "Inspecting now" in streamed
    assert "Running tool `security__one`..." not in streamed
    assert "Final answer." in streamed
    details = [chunk for chunk in chunks if chunk.get("kind") == "detail"]
    # Prose that accompanied the tool call is recorded as a "Planning" thinking
    # detail so it survives a reload as narration instead of leaking into the
    # persisted answer body, alongside the tool-execution detail.
    planning = [d for d in details if d["data"]["title"] == "Planning"]
    tool_details = [d for d in details if d["data"]["title"] == "Tool: security__one"]
    assert len(planning) == 1
    assert planning[0]["data"]["kind"] == "thinking"
    assert planning[0]["data"]["body"] == "Inspecting now"
    assert len(tool_details) == 1
    assert tool_details[0]["data"]["arguments"] == '{"org":"mappedsky"}'
    assert tool_details[0]["data"]["body"] == '{"ok": true}'


async def test_chat_graph_finishes_on_structured_respond_to_user_without_a_nudge(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    fake_model = _ToolCallingFakeModel(
        [
            AIMessage(content="", tool_calls=[_tool_call("security__one", {"org": "mappedsky"})]),
            # Well-behaved completion: deliver the answer through respond_to_user
            # instead of plain text, so no stall nudge is needed.
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(chat_graph._FINAL_ANSWER_TOOL, {"answer": "Three repos are high-risk."}, "call_2")
                ],
            ),
        ]
    )
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", False)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_tools_for_user",
        return_value=[Tool(name="security__one", description="One", inputSchema={"type": "object"})],
    )
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        return_value=ChatActionOutcome(text='{"ok": true}'),
    )
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Run the overview")]},
            {"configurable": {"thread_id": "thread-respond-tool", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert streamed == "Three repos are high-risk."
    # respond_to_user is intercepted as the terminal answer, not dispatched as a
    # tool, so it produces no tool-execution detail and only one model turn ran
    # after the action (no stall nudge).
    assert not [
        c
        for c in chunks
        if c.get("kind") == "detail" and c["data"]["title"] == f"Tool: {chat_graph._FINAL_ANSWER_TOOL}"
    ]
    assert fake_model.calls == 2


async def test_run_llm_tool_turn_streams_text_before_and_after_tool_call_chunk():
    """Streams text chunks even when a later tool-call chunk arrives."""

    class _PeekModel:
        async def astream(self, input, config=None, **kwargs):
            yield AIMessageChunk(content="Let me check ")
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": "security__one", "args": "{}", "id": "call_1", "index": 0}],
            )
            yield AIMessageChunk(content=" — actually wait")

    streamed_deltas: list[str] = []

    def writer(item: dict) -> None:
        streamed_deltas.append(item["content"])

    result = await chat_graph._run_llm_tool_turn(
        _PeekModel(),
        "system",
        [HumanMessage(content="hi")],
        [],
        {},
        writer,
    )

    assert streamed_deltas == ["Let me check ", " — actually wait"]
    assert result.streamed == "Let me check  — actually wait"
    assert "— actually wait" in message_text_of(result.message)


async def test_run_llm_tool_turn_streams_text_when_tools_are_available():
    class _PeekModel:
        def bind_tools(self, tools):
            return self

        async def astream(self, input, config=None, **kwargs):
            yield AIMessageChunk(content="Let me pull a focused investigation.")
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": "security__one", "args": "{}", "id": "call_1", "index": 0}],
            )

    streamed_deltas: list[str] = []

    def writer(item: dict) -> None:
        if item["kind"] == "token":
            streamed_deltas.append(item["content"])

    result = await chat_graph._run_llm_tool_turn(
        _PeekModel(),
        "system",
        [HumanMessage(content="hi")],
        [
            chat_graph.ChatToolSpec(
                name="security__one",
                kind="tool",
                description="Security overview",
                input_schema={"type": "object"},
            )
        ],
        {},
        writer,
    )

    assert streamed_deltas == ["Let me pull a focused investigation."]
    assert result.streamed == "Let me pull a focused investigation."
    assert "Let me pull" in message_text_of(result.message)


def test_provider_tool_name_mapping_keeps_seizu_execution_name():
    long_name = "github_security_investigations__single_repository_security_overview_with_actions_and_alerts"
    spec = chat_graph.ChatToolSpec(
        name=long_name,
        kind="tool",
        description="Long-name tool",
        input_schema={"type": "object"},
    )

    mapped = chat_graph._with_provider_tool_names([spec])[0]
    llm_name = chat_graph._llm_tool_name(mapped)
    schema = chat_graph._langchain_tool_schema(mapped)
    requests = chat_graph._tool_call_requests(
        AIMessage(content="", tool_calls=[_tool_call(llm_name, {"repo": "mappedsky/seizu"})]),
        [mapped],
    )

    assert llm_name != long_name
    assert len(llm_name) <= 64
    assert schema["function"]["name"] == llm_name
    assert long_name in schema["function"]["description"]
    assert requests[0].name == long_name
    assert requests[0].arguments == {"repo": "mappedsky/seizu"}


def message_text_of(message):
    from reporting.services.chat_messages import message_text

    return message_text(message.content)


async def test_chat_graph_streams_real_llm_with_seizu_prompt(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    class _FakeModel:
        def __init__(self) -> None:
            self.messages = []

        async def astream(self, input, config=None, **kwargs):
            self.messages = input
            yield AIMessageChunk(content="Investigate ")
            yield AIMessageChunk(content="the graph.")

    fake_model = _FakeModel()
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", True)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_prompts_for_user",
        return_value=[
            Prompt(
                name="investigation__triage",
                description="Triage a graph investigation",
                arguments=[PromptArgument(name="asset", required=True)],
            )
        ],
    )
    # Tools are fetched once even under progressive disclosure because
    # always-disclosed tools (e.g. sandbox__delegate) must appear in the
    # capability context and be immediately callable by the model.
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_tools_for_user",
        return_value=[],
    )
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="What should I check?")]},
            {"configurable": {"thread_id": "thread-llm", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    assert "".join(chunk["content"] for chunk in chunks) == "Investigate the graph."
    assert isinstance(fake_model.messages[0], SystemMessage)
    assert "security graph dashboard" in fake_model.messages[0].content
    assert "not a generic chatbot" in fake_model.messages[0].content
    assert "progressive disclosure is enabled" in fake_model.messages[0].content
    assert "investigation__triage" in fake_model.messages[0].content
    assert fake_model.messages[-1].content == "What should I check?"


async def test_chat_graph_auto_runs_model_requested_skill(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    fake_model = _ToolCallingFakeModel(
        [
            AIMessage(content="", tool_calls=[_tool_call("investigation__triage", {"org": "mappedsky"})]),
            AIMessage(content="Mappedsky overview is ready."),
        ]
    )
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", True)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_prompts_for_user",
        return_value=[Prompt(name="investigation__triage", description="Triage a graph investigation", arguments=[])],
    )
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_tools_for_user", return_value=[])
    render_skill = mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.render_prompt_for_chat",
        return_value=ChatActionOutcome(
            text="Call github_security__org_overview with org=mappedsky, then summarize.",
        ),
    )
    graph = chat_graph.build_chat_graph(MemorySaver())
    current = _user()

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Give me a security overview of mappedsky")]},
            {"configurable": {"thread_id": "thread-skill", "current_user": current}},
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "Loading skill `investigation__triage`..." not in streamed
    assert "Mappedsky overview is ready." in streamed
    assert "/skill investigation__triage" not in streamed
    render_skill.assert_awaited_once_with(
        current,
        "investigation__triage",
        {"org": "mappedsky"},
        gate_permission=Permission.CHAT_SKILLS_CALL,
    )
    assert fake_model.bound_tools[0][0]["function"]["name"] == "investigation__triage"
    assert fake_model.inputs[1][-1].content == "Call github_security__org_overview with org=mappedsky, then summarize."


async def test_progressive_disclosure_exposes_only_skill_required_tools(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    fake_model = _ToolCallingFakeModel(
        [
            AIMessage(content="", tool_calls=[_tool_call("investigation__triage", {"org": "mappedsky"})]),
            AIMessage(content="", tool_calls=[_tool_call("github_security__org_overview", {"org": "mappedsky"})]),
            AIMessage(content="Mappedsky overview is summarized."),
        ]
    )
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", True)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_prompts_for_user",
        return_value=[Prompt(name="investigation__triage", description="Triage a graph investigation", arguments=[])],
    )
    list_tools = mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_tools_for_user",
        return_value=[
            Tool(name="github_security__org_overview", description="Org overview", inputSchema={"type": "object"}),
            Tool(name="github_security__update_repo", description="Update repo", inputSchema={"type": "object"}),
        ],
    )
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.render_prompt_for_chat",
        return_value=ChatActionOutcome(
            text="Use the org overview tool.",
            tools_required=("github_security__org_overview",),
        ),
    )
    call_tool = mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        return_value=ChatActionOutcome(text='{"overview": true}'),
    )
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Give me a security overview of mappedsky")]},
            {"configurable": {"thread_id": "thread-strict-disclosure", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "Mappedsky overview is summarized." in streamed
    assert fake_model.bound_tools[0][0]["function"]["name"] == "investigation__triage"
    second_turn_names = {tool["function"]["name"] for tool in fake_model.bound_tools[1]}
    assert "github_security__org_overview" in second_turn_names
    assert "github_security__update_repo" not in second_turn_names
    list_tools.assert_awaited_once()
    call_tool.assert_awaited_once()
    assert call_tool.await_args.args[1] == "github_security__org_overview"


async def test_progressive_disclosure_persists_unlocked_tools_across_turns(mocker):
    """A tool unlocked by a skill in one turn stays callable in the next turn.

    The in-turn disclosure set is otherwise reset each turn, so a turn that
    ended mid-flow (rate limit, output cap) would lose the tools a rendered
    skill had surfaced. ``ChatState.disclosed_tools`` carries them forward.
    """
    from langgraph.checkpoint.memory import MemorySaver

    fake_model = _ToolCallingFakeModel(
        [
            # Turn 1: render the skill (which discloses the tool), then finish
            # without calling it.
            AIMessage(content="", tool_calls=[_tool_call("investigation__triage", {"org": "mappedsky"})]),
            AIMessage(content="Triage skill rendered."),
            # Turn 2: call the disclosed tool directly, without re-rendering the
            # skill first.
            AIMessage(content="", tool_calls=[_tool_call("github_security__org_overview", {"org": "mappedsky"})]),
            AIMessage(content="Overview summarized."),
        ]
    )
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", True)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_prompts_for_user",
        return_value=[Prompt(name="investigation__triage", description="Triage a graph investigation", arguments=[])],
    )
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_tools_for_user",
        return_value=[
            Tool(name="github_security__org_overview", description="Org overview", inputSchema={"type": "object"}),
            Tool(name="github_security__update_repo", description="Update repo", inputSchema={"type": "object"}),
        ],
    )
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.render_prompt_for_chat",
        return_value=ChatActionOutcome(
            text="Use the org overview tool.",
            tools_required=("github_security__org_overview",),
        ),
    )
    call_tool = mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        return_value=ChatActionOutcome(text='{"overview": true}'),
    )
    graph = chat_graph.build_chat_graph(MemorySaver())
    config = {"configurable": {"thread_id": "thread-persist-disclosure", "current_user": _user()}}

    async for _ in graph.astream(
        {"messages": [HumanMessage(content="Render the triage skill")]}, config, stream_mode="custom"
    ):
        pass

    second_turn_chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Now run the org overview")]}, config, stream_mode="custom"
        )
    ]

    streamed = "".join(chunk["content"] for chunk in second_turn_chunks if chunk.get("kind") == "token")
    assert "Overview summarized." in streamed
    # The first LLM turn of the *second* request must already see the unlocked
    # tool (seeded from persisted disclosed_tools), without re-rendering the
    # skill — but not tools that were never disclosed.
    second_request_first_bind = {tool["function"]["name"] for tool in fake_model.bound_tools[2]}
    assert "github_security__org_overview" in second_request_first_bind
    assert "github_security__update_repo" not in second_request_first_bind
    # The tool actually ran rather than being reported as unavailable.
    call_tool.assert_awaited_once()
    assert call_tool.await_args.args[1] == "github_security__org_overview"


async def test_chat_graph_runs_model_requested_tools_in_parallel(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    started: list[str] = []
    both_started = asyncio.Event()

    async def _call_tool(current_user, name, arguments, **kwargs):
        started.append(name)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1)
        return ChatActionOutcome(text=f'{{"tool": "{name}"}}')

    fake_model = _ToolCallingFakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call("security__one", {"org": "mappedsky"}, "call_1"),
                    _tool_call("security__two", {"org": "mappedsky"}, "call_2"),
                ],
            ),
            AIMessage(content="Both tool results are summarized."),
        ]
    )
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", False)
    mocker.patch("reporting.settings.CHAT_LLM_MAX_PARALLEL_TOOL_CALLS", 4)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_tools_for_user",
        return_value=[
            Tool(name="security__one", description="One", inputSchema={"type": "object"}),
            Tool(name="security__two", description="Two", inputSchema={"type": "object"}),
        ],
    )
    call_tool = mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        side_effect=_call_tool,
    )
    graph = chat_graph.build_chat_graph(MemorySaver())
    current = _user()

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Check both repositories")]},
            {"configurable": {"thread_id": "thread-tools", "current_user": current}},
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "Running 2 tools in parallel" not in streamed
    assert "Both tool results are summarized." in streamed
    assert call_tool.await_count == 2
    assert set(started) == {"security__one", "security__two"}
    assert {message.name for message in fake_model.inputs[1][-2:]} == {"security__one", "security__two"}


async def test_chat_graph_retries_empty_response_after_action_result(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    fake_model = _ToolCallingFakeModel(
        [
            AIMessage(content="", tool_calls=[_tool_call("security__one", {"org": "mappedsky"})]),
            AIMessage(content=""),
            AIMessage(content="Final answer after retry."),
        ]
    )
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", False)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_tools_for_user",
        return_value=[Tool(name="security__one", description="One", inputSchema={"type": "object"})],
    )
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        return_value=ChatActionOutcome(text='{"ok": true}'),
    )
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Run the overview")]},
            {"configurable": {"thread_id": "thread-empty-retry", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "Running tool `security__one`..." not in streamed
    assert "Final answer after retry." in streamed
    assert fake_model.calls == 3
    # Retry guidance is appended to the system prompt for the next turn,
    # so it appears as the (first) SystemMessage rather than at the tail.
    retry_context = fake_model.inputs[2][0].content
    assert "final answer" in retry_context
    assert "security__one" in retry_context


async def test_chat_graph_retries_nonterminal_post_action_text_without_streaming_it(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    fake_model = _ToolCallingFakeModel(
        [
            AIMessage(content="", tool_calls=[_tool_call("security__one", {"org": "mappedsky"}, "call_1")]),
            AIMessage(content="Let me pull the high-severity findings next."),
            AIMessage(content="", tool_calls=[_tool_call("security__two", {"repo": "mappedsky/omnibot"}, "call_2")]),
            AIMessage(content="Final answer using both tool results."),
        ]
    )
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", False)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_tools_for_user",
        return_value=[
            Tool(name="security__one", description="One", inputSchema={"type": "object"}),
            Tool(name="security__two", description="Two", inputSchema={"type": "object"}),
        ],
    )
    call_tool = mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        side_effect=[
            ChatActionOutcome(text='{"critical": 1}'),
            ChatActionOutcome(text='{"high": 26}'),
        ],
    )
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Run the multi-step investigation")]},
            {"configurable": {"thread_id": "thread-nonterminal-post-action", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    # Post-action plain text ("Let me pull… next") skipped respond_to_user, so it
    # is treated as a stall: never streamed, and the model is nudged once to act
    # or finish. It then makes the second tool call and the real answer is taken.
    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "Let me pull the high-severity findings next." not in streamed
    assert "Final answer using both tool results." in streamed
    assert call_tool.await_count == 2
    assert [call.args[1] for call in call_tool.await_args_list] == ["security__one", "security__two"]
    # The stall nudge is appended to the next turn's system prompt.
    assert "without finishing the turn" in fake_model.inputs[2][0].content
    assert chat_graph._FINAL_ANSWER_TOOL in fake_model.inputs[2][0].content


async def test_chat_graph_retries_repeated_tool_call_without_rerunning(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    fake_model = _ToolCallingFakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[_tool_call("toolsets__list_tools", {"toolset_id": "github_security"})],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("toolsets__list_tools", {"toolset_id": "github_security"})],
            ),
            AIMessage(content="Final synthesis from the existing tool list."),
        ]
    )
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", False)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_tools_for_user",
        return_value=[
            Tool(
                name="toolsets__list_tools",
                description="List tools",
                inputSchema={"type": "object", "properties": {"toolset_id": {"type": "string"}}},
            )
        ],
    )
    call_tool = mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        return_value=ChatActionOutcome(text='{"tools": []}'),
    )
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Run the overview")]},
            {"configurable": {"thread_id": "thread-repeat-tool-retry", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "Running tool `toolsets__list_tools`..." not in streamed
    assert "Final synthesis from the existing tool list." in streamed
    assert call_tool.await_count == 1
    assert "already run in this turn" in fake_model.inputs[2][0].content
    assert "All completed action summaries so far" in fake_model.inputs[2][0].content
    assert "using data from the completed result" in fake_model.inputs[2][0].content


async def test_chat_graph_repeated_tool_fallback_does_not_rerun_or_dump_internal_prompt(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    fake_model = _ToolCallingFakeModel(
        [
            AIMessage(content="", tool_calls=[_tool_call("skillsets__list", {})]),
            AIMessage(content="", tool_calls=[_tool_call("skillsets__list", {})]),
        ]
    )

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", False)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_tools_for_user",
        return_value=[Tool(name="skillsets__list", description="List skillsets", inputSchema={"type": "object"})],
    )
    call_tool = mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        return_value=ChatActionOutcome(text='{"skillsets": []}'),
    )
    graph = chat_graph.build_chat_graph(MemorySaver())
    config = {"configurable": {"thread_id": "thread-repeat-tool-fallback", "current_user": _user()}}

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Run the overview")]},
            config,
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "Running tool `skillsets__list`..." not in streamed
    assert "repeatedly requested the same internal action" in streamed
    assert "Use this result as evidence" not in streamed
    assert '{"skillsets": []}' not in streamed
    assert call_tool.await_count == 1
    state = await graph.aget_state(config)
    assert has_tag(state.values["messages"][-1], MessageTag.BROKEN)


async def test_chat_graph_retries_initial_empty_response(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    class _FakeModel:
        def __init__(self) -> None:
            self.calls = 0
            self.inputs = []

        async def astream(self, input, config=None, **kwargs):
            self.inputs.append(input)
            self.calls += 1
            if self.calls == 1:
                return
            yield AIMessageChunk(content="Retry produced a useful answer.")

    fake_model = _FakeModel()
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Try the action again from scratch")]},
            {"configurable": {"thread_id": "thread-initial-empty-retry", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert streamed == "Retry produced a useful answer."
    assert fake_model.calls == 2
    assert "previous response was empty before Seizu could run" in fake_model.inputs[1][0].content


async def test_chat_graph_initial_empty_response_fallback_is_specific(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    class _FakeModel:
        async def astream(self, input, config=None, **kwargs):
            if False:
                yield AIMessageChunk(content="")

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=_FakeModel())
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Try again")]},
            {"configurable": {"thread_id": "thread-initial-empty-fallback", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "after retrying" in streamed
    assert "did not run any skill or tool" in streamed


async def test_chat_graph_empty_response_fallback_preserves_last_action_result(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    fake_model = _ToolCallingFakeModel(
        [
            AIMessage(content="", tool_calls=[_tool_call("security__one", {"org": "mappedsky"})]),
            AIMessage(content=""),
            AIMessage(content=""),
        ]
    )
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", False)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_tools_for_user",
        return_value=[Tool(name="security__one", description="One", inputSchema={"type": "object"})],
    )
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        return_value=ChatActionOutcome(text='{"finding": "missing toolset_id"}'),
    )
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Run the overview")]},
            {"configurable": {"thread_id": "thread-empty-fallback", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "did not return a final synthesis" in streamed
    assert "security__one" not in streamed
    assert "missing toolset_id" not in streamed
    assert "Use this result as evidence" not in streamed
    assert fake_model.calls == 3


async def test_chat_tool_create_already_exists_is_idempotent_success(mocker):
    request = chat_graph.ToolCallRequest(
        id="call_1",
        name="skillsets__create_skill",
        arguments={"skillset_id": "github", "skill_id": "overview"},
        spec=chat_graph.ChatToolSpec(
            name="skillsets__create_skill",
            kind="tool",
            description="Create a skill",
            input_schema={"type": "object"},
        ),
    )
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        return_value=ChatActionOutcome(text='{"error":"Skill already exists"}'),
    )

    result = await chat_graph._run_tool_call(request, _user(), session_key="1001")

    data = json.loads(result.content)
    assert result.blocked is None
    assert data["ok"] is True
    assert data["idempotent"] is True
    assert "already completed" in data["message"]


def test_confirmation_batch_id_only_for_multiple_requests(mocker):
    request = chat_graph.ToolCallRequest(
        id="call_1",
        name="reports__delete",
        arguments={"report_id": "r1"},
        spec=chat_graph.ChatToolSpec(
            name="reports__delete",
            kind="tool",
            description="Delete report",
            input_schema={"type": "object"},
        ),
    )

    assert chat_graph._confirmation_batch_id_for_requests([request]) is None
    mocker.patch(
        "reporting.services.chat_graph.report_store.generate_id",
        return_value="123456789012345678",
    )
    batch_id = chat_graph._confirmation_batch_id_for_requests([request, request])
    assert batch_id == "123456789012345678"


async def test_pending_confirmation_response_uses_chat_panel_not_url():
    request = chat_graph.ToolCallRequest(
        id="call_1",
        name="reports__delete",
        arguments={"report_id": "r1"},
        spec=chat_graph.ChatToolSpec(
            name="reports__delete",
            kind="tool",
            description="Delete report",
            input_schema={"type": "object"},
        ),
    )
    result = chat_graph.ToolCallResult(
        request=request,
        blocked=ChatBlockReason.CONFIRMATION_REQUIRED,
        content=json.dumps(
            {
                "confirmation_required": True,
                "status": "pending",
                "confirmation_url": "https://seizu.example.com/app/confirmations/abc123",
            }
        ),
    )

    response = chat_graph._blocked_tool_call_response([result])

    assert "Approval needed" in response
    assert "confirmations panel" in response.lower()
    assert "https://seizu.example.com/app/confirmations/abc123" not in response


async def test_batch_confirmation_response_uses_chat_panel_not_batch_url():
    request = chat_graph.ToolCallRequest(
        id="call_1",
        name="reports__delete",
        arguments={"report_id": "r1"},
        spec=chat_graph.ChatToolSpec(
            name="reports__delete",
            kind="tool",
            description="Delete report",
            input_schema={"type": "object"},
        ),
    )
    result_1 = chat_graph.ToolCallResult(
        request=request,
        blocked=ChatBlockReason.CONFIRMATION_REQUIRED,
        content=json.dumps(
            {
                "confirmation_required": True,
                "status": "pending",
                "batch_url": "https://seizu.example.com/app/confirmations/batch/batch123",
            }
        ),
    )
    result_2 = result_1

    response = chat_graph._blocked_tool_call_response([result_1, result_2])

    assert "Approval needed for 2 actions" in response
    assert "confirmations panel" in response.lower()
    assert "https://seizu.example.com/app/confirmations/batch/batch123" not in response


async def test_decided_confirmation_response_does_not_include_url():
    request = chat_graph.ToolCallRequest(
        id="call_1",
        name="reports__delete",
        arguments={"report_id": "r1"},
        spec=chat_graph.ChatToolSpec(
            name="reports__delete",
            kind="tool",
            description="Delete report",
            input_schema={"type": "object"},
        ),
    )
    result = chat_graph.ToolCallResult(
        request=request,
        blocked=ChatBlockReason.CONFIRMATION_REQUIRED,
        content=json.dumps(
            {
                "confirmation_required": True,
                "status": "denied",
                "error": "Action was denied for this confirmation window",
            }
        ),
    )

    response = chat_graph._blocked_tool_call_response([result])

    assert "already been decided or has expired" in response
    assert "Confirmations" not in response


async def test_resume_expired_approved_confirmation_does_not_execute(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    confirmation = ActionConfirmation.model_validate(
        {
            "confirmation_id": "confirm-expired",
            "user_id": "user-1",
            "source": "chat",
            "session_key": "thread-expired-confirmation",
            "tool_name": "reports__delete",
            "action": "delete",
            "resource_type": "report",
            "resource_id": "report-1",
            "arguments": {"report_id": "report-1"},
            "arguments_hash": "hash",
            "status": "approved",
            "created_at": "2024-01-01T00:00:00+00:00",
            "expires_at": "2024-01-01T00:30:00+00:00",
        }
    )
    mocker.patch("reporting.services.chat_graph.report_store.get_action_confirmation", return_value=confirmation)
    claim = mocker.patch("reporting.services.chat_graph.report_store.claim_action_confirmation_for_execution")
    call_tool = mocker.patch("reporting.services.chat_graph.mcp_runtime.call_tool_for_chat")
    graph = chat_graph.build_chat_graph(MemorySaver())
    config = {
        "configurable": {
            "thread_id": "thread-expired-confirmation",
            "client_thread_id": "thread-expired-confirmation",
            "current_user": _user(),
        }
    }

    chunks = [
        chunk
        async for chunk in graph.astream(
            {
                "messages": [
                    HumanMessage(
                        content="Resume approved confirmation confirm-expired",
                        additional_kwargs={"resume_confirmation_id": "confirm-expired"},
                    )
                ]
            },
            config,
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "has expired" in streamed
    claim.assert_not_called()
    call_tool.assert_not_called()


async def test_resume_confirmation_must_belong_to_active_chat_thread(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    confirmation = ActionConfirmation.model_validate(
        {
            "confirmation_id": "confirm-mcp",
            "user_id": "user-1",
            "source": "mcp",
            "session_key": "hashed-mcp-session",
            "tool_name": "reports__delete",
            "action": "delete",
            "resource_type": "report",
            "resource_id": "report-1",
            "arguments": {"report_id": "report-1"},
            "arguments_hash": "hash",
            "status": "approved",
            "created_at": "2024-01-01T00:00:00+00:00",
            "expires_at": "2099-01-01T00:30:00+00:00",
        }
    )
    mocker.patch("reporting.services.chat_graph.report_store.get_action_confirmation", return_value=confirmation)
    claim = mocker.patch("reporting.services.chat_graph.report_store.claim_action_confirmation_for_execution")
    call_tool = mocker.patch("reporting.services.chat_graph.mcp_runtime.call_tool_for_chat")
    graph = chat_graph.build_chat_graph(MemorySaver())
    config = {
        "configurable": {
            "thread_id": "thread-active",
            "client_thread_id": "thread-active",
            "current_user": _user(),
        }
    }

    chunks = [
        chunk
        async for chunk in graph.astream(
            {
                "messages": [
                    HumanMessage(
                        content="Resume approved confirmation confirm-mcp",
                        additional_kwargs={"resume_confirmation_id": "confirm-mcp"},
                    )
                ]
            },
            config,
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "does not belong to this chat thread" in streamed
    claim.assert_not_called()
    call_tool.assert_not_called()


async def test_resume_batch_confirmation_uses_batch_lookup(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    approved = ActionConfirmation.model_validate(
        {
            "confirmation_id": "confirm-approved",
            "user_id": "user-1",
            "source": "chat",
            "session_key": "thread-batch-confirmation",
            "tool_name": "reports__delete",
            "action": "delete",
            "resource_type": "report",
            "resource_id": "report-1",
            "arguments": {"report_id": "report-1"},
            "arguments_hash": "hash-1",
            "status": "approved",
            "batch_id": "batch-1",
            "created_at": "2024-01-01T00:00:00+00:00",
            "expires_at": "2099-01-01T00:30:00+00:00",
        }
    )
    pending = approved.model_copy(
        update={
            "confirmation_id": "confirm-pending",
            "tool_name": "reports__pin",
            "action": "pin",
            "resource_id": "report-2",
            "arguments": {"report_id": "report-2", "pinned": True},
            "status": "pending",
        }
    )
    mocker.patch("reporting.services.chat_graph.report_store.get_action_confirmation", return_value=approved)
    list_batch = mocker.patch(
        "reporting.services.chat_graph.report_store.list_batch_action_confirmations",
        return_value=[approved, pending],
    )
    list_session = mocker.patch("reporting.services.chat_graph.report_store.list_action_confirmations")
    claim = mocker.patch("reporting.services.chat_graph.report_store.claim_action_confirmation_for_execution")
    graph = chat_graph.build_chat_graph(MemorySaver())
    config = {
        "configurable": {
            "thread_id": "thread-batch-confirmation",
            "client_thread_id": "thread-batch-confirmation",
            "current_user": _user(),
        }
    }

    chunks = [
        chunk
        async for chunk in graph.astream(
            {
                "messages": [
                    HumanMessage(
                        content="Resume approved confirmation confirm-approved",
                        additional_kwargs={"resume_confirmation_id": "confirm-approved"},
                    )
                ]
            },
            config,
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "Waiting for 1 more approval" in streamed
    list_batch.assert_awaited_once_with(user_id="user-1", batch_id="batch-1")
    list_session.assert_not_called()
    claim.assert_not_called()


async def test_resume_confirmation_tool_error_does_not_ask_model_to_reapply(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    approved = ActionConfirmation.model_validate(
        {
            "confirmation_id": "confirm-approved",
            "user_id": "user-1",
            "source": "chat",
            "session_key": "thread-tool-error",
            "tool_name": "skillsets__create_skill",
            "action": "create_skill",
            "resource_type": "skill",
            "resource_id": "attack_path_tracing/demo",
            "arguments": {"skillset_id": "attack_path_tracing", "skill_id": "demo"},
            "arguments_hash": "hash-1",
            "status": "approved",
            "created_at": "2024-01-01T00:00:00+00:00",
            "expires_at": "2099-01-01T00:30:00+00:00",
        }
    )
    mocker.patch("reporting.services.chat_graph.report_store.get_action_confirmation", return_value=approved)
    mocker.patch(
        "reporting.services.chat_graph.report_store.claim_action_confirmation_for_execution",
        return_value=approved.model_copy(update={"status": "executed"}),
    )
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        return_value=ChatActionOutcome(text='{"error":"tools_required must use toolset_id__tool_id"}'),
    )
    get_model = mocker.patch("reporting.services.chat_graph.get_chat_model")
    graph = chat_graph.build_chat_graph(MemorySaver())
    config = {
        "configurable": {
            "thread_id": "thread-tool-error",
            "client_thread_id": "thread-tool-error",
            "current_user": _user(),
        }
    }

    chunks = [
        chunk
        async for chunk in graph.astream(
            {
                "messages": [
                    HumanMessage(
                        content="Resume approved confirmation confirm-approved",
                        additional_kwargs={"resume_confirmation_id": "confirm-approved"},
                    )
                ]
            },
            config,
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "Running approved action" in streamed
    assert "tools_required must use toolset_id__tool_id" in streamed
    assert "reapply" not in streamed.lower()
    get_model.assert_not_called()


async def test_resume_batch_confirmation_does_not_run_after_denial(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    approved = ActionConfirmation.model_validate(
        {
            "confirmation_id": "confirm-approved",
            "user_id": "user-1",
            "source": "chat",
            "session_key": "thread-batch-denied",
            "tool_name": "reports__delete",
            "action": "delete",
            "resource_type": "report",
            "resource_id": "report-1",
            "arguments": {"report_id": "report-1"},
            "arguments_hash": "hash-1",
            "status": "approved",
            "batch_id": "batch-denied",
            "created_at": "2024-01-01T00:00:00+00:00",
            "expires_at": "2099-01-01T00:30:00+00:00",
        }
    )
    denied = approved.model_copy(
        update={
            "confirmation_id": "confirm-denied",
            "tool_name": "reports__pin",
            "action": "pin",
            "resource_id": "report-2",
            "arguments": {"report_id": "report-2", "pinned": True},
            "status": "denied",
        }
    )
    mocker.patch("reporting.services.chat_graph.report_store.get_action_confirmation", return_value=approved)
    mocker.patch(
        "reporting.services.chat_graph.report_store.list_batch_action_confirmations",
        return_value=[approved, denied],
    )
    claim = mocker.patch("reporting.services.chat_graph.report_store.claim_action_confirmation_for_execution")
    graph = chat_graph.build_chat_graph(MemorySaver())
    config = {
        "configurable": {
            "thread_id": "thread-batch-denied",
            "client_thread_id": "thread-batch-denied",
            "current_user": _user(),
        }
    }

    chunks = [
        chunk
        async for chunk in graph.astream(
            {
                "messages": [
                    HumanMessage(
                        content="Resume approved confirmation confirm-approved",
                        additional_kwargs={"resume_confirmation_id": "confirm-approved"},
                    )
                ]
            },
            config,
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "were denied" in streamed
    claim.assert_not_called()


async def test_resume_batch_confirmation_does_not_abort_already_executed_batch_after_ttl(mocker):
    """Executed batch items whose TTL has passed must not be treated as expired."""
    from langgraph.checkpoint.memory import MemorySaver

    # expires_at is in the past so is_expired() returns True for both items.
    executed1 = ActionConfirmation.model_validate(
        {
            "confirmation_id": "confirm-exec-1",
            "user_id": "user-1",
            "source": "chat",
            "session_key": "thread-exec-batch",
            "tool_name": "reports__delete",
            "action": "delete",
            "resource_type": "report",
            "resource_id": "report-1",
            "arguments": {"report_id": "report-1"},
            "arguments_hash": "hash-1",
            "status": "executed",
            "batch_id": "batch-exec",
            "created_at": "2020-01-01T00:00:00+00:00",
            "expires_at": "2020-01-01T00:30:00+00:00",
        }
    )
    executed2 = executed1.model_copy(
        update={
            "confirmation_id": "confirm-exec-2",
            "tool_name": "reports__pin",
            "action": "pin",
            "resource_id": "report-2",
            "arguments": {"report_id": "report-2", "pinned": True},
        }
    )
    mocker.patch("reporting.services.chat_graph.report_store.get_action_confirmation", return_value=executed1)
    mocker.patch(
        "reporting.services.chat_graph.report_store.list_batch_action_confirmations",
        return_value=[executed1, executed2],
    )
    claim = mocker.patch("reporting.services.chat_graph.report_store.claim_action_confirmation_for_execution")
    graph = chat_graph.build_chat_graph(MemorySaver())
    config = {
        "configurable": {
            "thread_id": "thread-exec-batch",
            "client_thread_id": "thread-exec-batch",
            "current_user": _user(),
        }
    }

    chunks = [
        chunk
        async for chunk in graph.astream(
            {
                "messages": [
                    HumanMessage(
                        content="Resume approved confirmation confirm-exec-1",
                        additional_kwargs={"resume_confirmation_id": "confirm-exec-1"},
                    )
                ]
            },
            config,
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "already been executed" in streamed
    claim.assert_not_called()


async def test_resume_batch_confirmation_respects_parallel_tool_limit(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    base = ActionConfirmation.model_validate(
        {
            "confirmation_id": "confirm-approved-1",
            "user_id": "user-1",
            "source": "chat",
            "session_key": "thread-limited-batch",
            "tool_name": "reports__delete",
            "action": "delete",
            "resource_type": "report",
            "resource_id": "report-1",
            "arguments": {"report_id": "report-1"},
            "arguments_hash": "hash-1",
            "status": "approved",
            "batch_id": "batch-limited",
            "created_at": "2024-01-01T00:00:00+00:00",
            "expires_at": "2099-01-01T00:30:00+00:00",
        }
    )
    batch = [
        base,
        base.model_copy(
            update={
                "confirmation_id": "confirm-approved-2",
                "tool_name": "reports__pin",
                "action": "pin",
                "resource_id": "report-2",
                "arguments": {"report_id": "report-2", "pinned": True},
            }
        ),
        base.model_copy(
            update={
                "confirmation_id": "confirm-approved-3",
                "tool_name": "reports__set_dashboard",
                "action": "set_dashboard",
                "resource_id": "report-3",
                "arguments": {"report_id": "report-3"},
            }
        ),
    ]
    by_id = {item.confirmation_id: item for item in batch}
    active = 0
    max_seen = 0

    async def _claim(confirmation_id: str, user_id: str):
        return by_id[confirmation_id]

    async def _call_tool(*args, **kwargs):
        nonlocal active, max_seen
        active += 1
        max_seen = max(max_seen, active)
        await asyncio.sleep(0.01)
        active -= 1
        return ChatActionOutcome(text='{"ok": true}')

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "mock")
    mocker.patch("reporting.settings.CHAT_LLM_MAX_PARALLEL_TOOL_CALLS", 1)
    mocker.patch("reporting.services.chat_graph.report_store.get_action_confirmation", return_value=base)
    mocker.patch("reporting.services.chat_graph.report_store.list_batch_action_confirmations", return_value=batch)
    claim = mocker.patch(
        "reporting.services.chat_graph.report_store.claim_action_confirmation_for_execution",
        side_effect=_claim,
    )
    call_tool = mocker.patch("reporting.services.chat_graph.mcp_runtime.call_tool_for_chat", side_effect=_call_tool)
    graph = chat_graph.build_chat_graph(MemorySaver())
    config = {
        "configurable": {
            "thread_id": "thread-limited-batch",
            "client_thread_id": "thread-limited-batch",
            "current_user": _user(),
        }
    }

    chunks = [
        chunk
        async for chunk in graph.astream(
            {
                "messages": [
                    HumanMessage(
                        content="Resume approved confirmation confirm-approved-1",
                        additional_kwargs={"resume_confirmation_id": "confirm-approved-1"},
                    )
                ]
            },
            config,
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "Running approved actions" in streamed
    assert "reports__delete" not in streamed
    assert "reports__pin" not in streamed
    assert "reports__set_dashboard" not in streamed
    assert claim.await_count == 3
    assert call_tool.await_count == 3
    assert max_seen == 1


async def test_chat_graph_reports_unavailable_tool_call_and_persists_notice(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    fake_model = _ToolCallingFakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[_tool_call("toolsets__update_tool", {"toolset_id": "github_security"}, "call_1")],
            )
        ]
    )
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", False)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_tools_for_user",
        return_value=[Tool(name="toolsets__list", description="List toolsets", inputSchema={"type": "object"})],
    )
    call_tool = mocker.patch("reporting.services.chat_graph.mcp_runtime.call_tool_for_chat")
    graph = chat_graph.build_chat_graph(MemorySaver())
    config = {"configurable": {"thread_id": "thread-unavailable-tool", "current_user": _user()}}

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Update these tools")]},
            config,
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "Seizu blocked the requested action" in streamed
    assert "toolsets__update_tool" in streamed
    assert "No blocked action was executed." in streamed
    call_tool.assert_not_called()
    state = await graph.aget_state(config)
    persisted = state.values["messages"][-1]
    assert "Seizu blocked the requested action" in persisted.content
    assert not has_tag(persisted, MessageTag.BROKEN)


async def test_chat_graph_reports_permission_denied_tool_result_and_persists_notice(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    fake_model = _ToolCallingFakeModel(
        [AIMessage(content="", tool_calls=[_tool_call("security__one", {"org": "mappedsky"}, "call_1")])]
    )
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", False)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_tools_for_user",
        return_value=[Tool(name="security__one", description="One", inputSchema={"type": "object"})],
    )
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        return_value=ChatActionOutcome(
            text='{"error": "Permission denied: tools:call"}',
            blocked=ChatBlockReason.PERMISSION_DENIED,
        ),
    )
    graph = chat_graph.build_chat_graph(MemorySaver())
    config = {"configurable": {"thread_id": "thread-permission-denied-tool", "current_user": _user()}}

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Run the overview")]},
            config,
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "Running tool `security__one`..." not in streamed
    assert "Seizu blocked the requested action" in streamed
    assert "Permission denied: tools:call" in streamed
    state = await graph.aget_state(config)
    persisted = state.values["messages"][-1]
    assert "Permission denied: tools:call" in persisted.content
    assert not has_tag(persisted, MessageTag.BROKEN)


async def test_chat_graph_does_not_persist_internal_command_attempt(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    fake_model = _ToolCallingFakeModel(
        [
            AIMessage(content="", tool_calls=[_tool_call("investigation__triage", {"org": "mappedsky"})]),
            AIMessage(content="Final overview."),
        ]
    )
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_prompts_for_user",
        return_value=[Prompt(name="investigation__triage", description="Triage a graph investigation", arguments=[])],
    )
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_tools_for_user", return_value=[])
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.render_prompt_for_chat",
        return_value=ChatActionOutcome(text="Rendered skill."),
    )
    graph = chat_graph.build_chat_graph(MemorySaver())
    current = _user()
    config = {"configurable": {"thread_id": "thread-no-stale", "current_user": current}}

    _ = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Give me the overview")]},
            config,
            stream_mode="custom",
        )
    ]

    state = await graph.aget_state(config)
    persisted = state.values["messages"]
    assert [type(message) for message in persisted] == [HumanMessage, AIMessage]
    assert persisted[1].content == "Final overview."
    assert all("/skill investigation__triage" not in str(message.content) for message in persisted)


def test_build_system_prompt_is_seizu_specific(mocker):
    # Pin the output budget so this content test is independent of the default
    # CHAT_LLM_MAX_TOKENS (budget scaling is covered separately).
    mocker.patch("reporting.settings.CHAT_LLM_MAX_TOKENS", 2048)
    prompt = chat_graph.build_system_prompt("gemini", _user())

    assert "Seizu's AI investigation assistant" in prompt
    assert "configuration-driven reporting platform" in prompt
    assert "security graph data" in prompt
    assert "not a generic chatbot" in prompt
    assert "Cypher" in prompt
    assert "include every required parameter" in prompt
    assert "native structured tool calling" in prompt
    assert "configured output budget" in prompt
    assert "under about 600 words" in prompt
    assert "at most 8 bullets" in prompt
    assert "You are the Seizu agent" in prompt
    assert "never tell the user to ask another Seizu agent" in prompt
    assert "call the matching skill" in prompt


def test_build_system_prompt_includes_sandbox_note_when_enabled(mocker):
    mocker.patch("reporting.settings.SANDBOX_ENABLED", True)
    prompt = chat_graph.build_system_prompt("anthropic", _user())
    assert "sandbox__delegate" in prompt
    assert "do not compute statistics" in prompt.lower() or "numbers computed by the model" in prompt


def test_build_system_prompt_excludes_sandbox_note_when_disabled(mocker):
    mocker.patch("reporting.settings.SANDBOX_ENABLED", False)
    prompt = chat_graph.build_system_prompt("anthropic", _user())
    assert "sandbox__delegate" not in prompt


def test_answer_budget_scales_with_configured_output_limit():
    assert chat_graph._answer_budget(1024) == chat_graph.AnswerBudget(
        min_words=150,
        max_words=300,
        max_bullets=4,
        max_tables=1,
    )
    assert chat_graph._answer_budget(2048) == chat_graph.AnswerBudget(
        min_words=300,
        max_words=600,
        max_bullets=8,
        max_tables=1,
    )
    assert chat_graph._answer_budget(4096) == chat_graph.AnswerBudget(
        min_words=625,
        max_words=1250,
        max_bullets=16,
        max_tables=2,
    )


def test_final_synthesis_retry_message_uses_configured_answer_budget(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_MAX_TOKENS", 1024)

    prompt = chat_graph._final_synthesis_retry_message(["Seizu ran tool `graph__query`.\n\nResult:\n{}"])

    assert "Be selective" in prompt
    assert "150-300 words" in prompt
    assert "at most 4 bullets" in prompt
    assert "at most one compact table" in prompt


def test_internal_action_transcript_leak_detection():
    assert chat_graph._internal_action_transcript_leaked("Seizu ran 1 action:\n\n`tool__x` with arguments {}")
    assert chat_graph._internal_action_transcript_leaked("- `attack_paths__entry` with arguments `{}` returned:")
    assert not chat_graph._internal_action_transcript_leaked("The attack path enters through public DNS.")


async def test_final_synthesis_retries_internal_action_transcript(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    fake_model = _ToolCallingFakeModel(
        [
            AIMessage(content="", tool_calls=[_tool_call("security__one", {"org": "mappedsky"})]),
            AIMessage(content=""),
            AIMessage(content="Seizu ran 1 action:\n\n`security__one` with arguments `{}` returned: []"),
            AIMessage(content="The highest-risk path is public DNS to the vulnerable service."),
        ]
    )
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", False)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_tools_for_user",
        return_value=[Tool(name="security__one", description="One", inputSchema={"type": "object"})],
    )
    mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        return_value=ChatActionOutcome(text='{"path": "public DNS to vulnerable service"}'),
    )
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Find the attack path")]},
            {"configurable": {"thread_id": "thread-synth-transcript-retry", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    streamed = "".join(chunk["content"] for chunk in chunks if chunk.get("kind") == "token")
    assert "Seizu ran 1 action" not in streamed
    assert "The highest-risk path is public DNS" in streamed
    assert "_action_transcript_retry" not in streamed
    assert fake_model.calls == 4


def test_llm_context_messages_applies_message_and_character_limits(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_MAX_MESSAGES", 3)
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_MAX_CHARS", 12)
    messages = [
        HumanMessage(content="older"),
        AIMessage(content="ignored by message cap"),
        HumanMessage(content="12345"),
        AIMessage(content="67890"),
        HumanMessage(content="abcde"),
    ]

    context = chat_graph._llm_context_messages(messages)

    assert [message.content for message in context] == ["67890", "abcde"]


def test_trim_inner_loop_messages_ignores_reasoning_content_but_counts_tool_calls():
    messages = [
        HumanMessage(content="q"),
        AIMessage(
            content="",
            additional_kwargs={"reasoning_content": "x" * 80},
            tool_calls=[_tool_call("security__one", {"org": "mappedsky"}, "call_1")],
        ),
        ToolMessage(content="{}.", tool_call_id="call_1", name="security__one"),
        AIMessage(content="recent", tool_calls=[_tool_call("security__two", {}, "call_2")]),
        ToolMessage(content="fresh result", tool_call_id="call_2", name="security__two"),
    ]
    without_reasoning = [
        messages[0],
        AIMessage(content="", tool_calls=[_tool_call("security__one", {"org": "mappedsky"}, "call_1")]),
        messages[2],
        messages[3],
        messages[4],
    ]

    retained = chat_graph._trim_inner_loop_messages(messages, max_chars=140)
    retained_without_reasoning = chat_graph._trim_inner_loop_messages(without_reasoning, max_chars=140)

    assert retained == [messages[0], messages[3], messages[4]]
    assert [message.content for message in retained] == [message.content for message in retained_without_reasoning]


def test_llm_context_messages_drops_broken_ai_output_but_keeps_good_context():
    broken = AIMessage(content="The model returned an empty response after retrying.")
    tagged_broken = AIMessage(content="I stopped because the model produced an incomplete or invalid internal command.")
    tagged_broken.additional_kwargs["seizu_tags"] = [MessageTag.BROKEN.value]
    messages = [
        HumanMessage(content="Original task"),
        AIMessage(content="Useful prior answer"),
        HumanMessage(content="Can you try the action again from scratch?"),
        broken,
        tagged_broken,
    ]

    context = chat_graph._llm_context_messages(messages)

    assert [message.content for message in context] == [
        "Original task",
        "Useful prior answer",
        "Can you try the action again from scratch?",
    ]


async def test_chat_graph_from_scratch_keeps_good_context_and_drops_broken_output(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    class _FakeModel:
        def __init__(self) -> None:
            self.messages = []

        async def astream(self, input, config=None, **kwargs):
            self.messages = input
            yield AIMessageChunk(content="Fresh answer.")

    fake_model = _FakeModel()
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    graph = chat_graph.build_chat_graph(MemorySaver())

    _ = [
        chunk
        async for chunk in graph.astream(
            {
                "messages": [
                    HumanMessage(content="Old request"),
                    AIMessage(content="Useful old output"),
                    AIMessage(content="The model returned an empty response after retrying."),
                    HumanMessage(content="Can you try the action again from scratch?"),
                ]
            },
            {"configurable": {"thread_id": "thread-from-scratch", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    model_context = fake_model.messages[1:]
    assert [message.content for message in model_context] == [
        "Old request",
        "Useful old output",
        "Can you try the action again from scratch?",
    ]


def test_build_capability_context_progressive_disclosure_lists_only_skills():
    skills = [
        Prompt(
            name="investigation__triage",
            description="Triage a graph investigation",
            arguments=[PromptArgument(name="asset", required=True)],
        )
    ]

    # tools=None → progressive variant (skills only).
    context = chat_graph.build_capability_context(skills, None)

    assert "progressive disclosure is enabled" in context
    assert "Available skills:" in context
    assert "investigation__triage" in context
    assert "structured skill tools" in context
    assert "trigger phrases" in context
    assert "call that skill now" in context
    assert "Available tools:" not in context
    assert "Always-available tools:" not in context


def test_build_capability_context_progressive_disclosure_includes_always_disclosed_tools():
    skills = [
        Prompt(
            name="investigation__triage",
            description="Triage a graph investigation",
            arguments=[PromptArgument(name="asset", required=True)],
        )
    ]
    always_disclosed = [
        Tool(
            name="sandbox__delegate",
            description="Delegate a task to an isolated sandbox",
            inputSchema={"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
        )
    ]

    context = chat_graph.build_capability_context(skills, None, always_disclosed_tools=always_disclosed)

    assert "progressive disclosure is enabled" in context
    assert "Available skills:" in context
    assert "investigation__triage" in context
    assert "Always-available tools:" in context
    assert "sandbox__delegate" in context
    assert "Available tools:" not in context


def test_build_capability_context_progressive_disclosure_no_skills_only_always_disclosed():
    always_disclosed = [
        Tool(
            name="sandbox__delegate",
            description="Delegate a task to an isolated sandbox",
            inputSchema={"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
        )
    ]

    context = chat_graph.build_capability_context([], None, always_disclosed_tools=always_disclosed)

    assert "progressive disclosure is enabled" in context
    assert "Always-available tools:" in context
    assert "sandbox__delegate" in context
    assert "Available skills:" not in context


def test_build_capability_context_progressive_disclosure_empty_returns_empty():
    context = chat_graph.build_capability_context([], None, always_disclosed_tools=[])
    assert context == ""


def test_build_capability_context_full_disclosure_lists_skills_and_tools():
    skills = [Prompt(name="investigation__triage", description="Triage a graph investigation", arguments=[])]
    tools = [
        Tool(
            name="graph__query",
            description="Run a read-only Cypher query",
            inputSchema={
                "type": "object",
                "properties": {"cypher": {"type": "string"}},
                "required": ["cypher"],
            },
        )
    ]

    context = chat_graph.build_capability_context(skills, tools)

    assert "progressive disclosure is disabled" in context
    assert "Available skills:" in context
    assert "investigation__triage" in context
    assert "Available tools:" in context
    assert "graph__query" in context
    assert "cypher (required)" in context
    assert "structured tool calls" in context
    assert "trigger phrases" in context


async def test_chat_agent_lists_skills_and_tools_once_per_turn(mocker):
    """One ``list_prompts_for_user`` + one ``list_tools_for_user`` per chat turn.

    Regression guard for the per-turn dedupe: before this, ``build_capability_context``
    and ``_skill_tool_specs``/``_mcp_tool_specs`` each called the listing
    functions, so a non-progressive turn fanned out to 4 store reads.
    """
    from langgraph.checkpoint.memory import MemorySaver

    fake_model = _ToolCallingFakeModel([AIMessage(content="Final answer.")])
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", False)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=fake_model)
    list_prompts = mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_prompts_for_user",
        return_value=[],
    )
    list_tools = mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.list_tools_for_user",
        return_value=[],
    )
    graph = chat_graph.build_chat_graph(MemorySaver())

    [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="hi")]},
            {"configurable": {"thread_id": "thread-once", "current_user": _user()}},
            stream_mode="custom",
        )
    ]

    assert list_prompts.await_count == 1
    assert list_tools.await_count == 1


async def test_load_thread_messages_drops_ephemeral(mocker):
    continue_marker = HumanMessage(content="")
    continue_marker.additional_kwargs["seizu_tags"] = [MessageTag.EPHEMERAL.value]
    continue_marker.additional_kwargs["continue_response"] = True
    ephemeral = HumanMessage(content="/tools")
    ephemeral.additional_kwargs["seizu_tags"] = [MessageTag.EPHEMERAL.value]
    persisted = [
        HumanMessage(content="Hi"),
        AIMessage(content="Hello"),
        continue_marker,
        AIMessage(content="continued"),
        ephemeral,
    ]

    class _Graph:
        async def aget_state(self, config):
            return type("State", (), {"values": {"messages": persisted}})()

    mocker.patch("reporting.services.chat_graph.get_chat_graph", return_value=_Graph())

    messages = await chat_graph.load_thread_messages(_user(), "thread-1", limit=10)

    assert [m.content for m in messages] == ["Hi", "Hello\n\n{% continuation /%}\n\ncontinued"]
    assert messages[1].response_metadata == {}


def test_strip_chat_ui_markers_removes_markdoc_continuation():
    assert chat_graph.strip_chat_ui_markers("Hello\n\n{% continuation /%}\n\nworld") == "Hello\n\nworld"


async def test_load_thread_messages_limits_returned_messages(mocker):
    persisted = [
        HumanMessage(content="one"),
        AIMessage(content="two"),
        HumanMessage(content="three"),
    ]

    class _Graph:
        async def aget_state(self, config):
            return type("State", (), {"values": {"messages": persisted}})()

    mocker.patch("reporting.services.chat_graph.get_chat_graph", return_value=_Graph())

    messages = await chat_graph.load_thread_messages(_user(), "thread-1", limit=2)

    assert [m.content for m in messages] == ["two", "three"]


def test_trim_messages_removes_oldest_turn(mocker):
    mocker.patch("reporting.settings.CHAT_MAX_PERSISTED_MESSAGES", 2)
    existing = [
        HumanMessage(content="q1", id="h1"),
        AIMessage(content="a1", id="a1"),
        HumanMessage(content="q2", id="h2"),
    ]
    new_message = AIMessage(content="a2", id="a2")

    # combined = [h1, a1, h2, a2]; cap 2 drops the oldest user/assistant turn.
    removals = chat_graph._trim_messages(existing, new_message)

    assert all(isinstance(r, RemoveMessage) for r in removals)
    assert [r.id for r in removals] == ["h1", "a1"]


def test_trim_messages_keeps_window_starting_at_user_turn(mocker):
    mocker.patch("reporting.settings.CHAT_MAX_PERSISTED_MESSAGES", 3)
    existing = [
        HumanMessage(content="q1", id="h1"),
        AIMessage(content="a1", id="a1"),
        HumanMessage(content="q2", id="h2"),
    ]
    new_message = AIMessage(content="a2", id="a2")

    # combined = [h1, a1, h2, a2]; cap 3 would drop only h1, orphaning a1 — so
    # a1 is shed too and the retained window starts at the user turn h2.
    removals = chat_graph._trim_messages(existing, new_message)

    assert [r.id for r in removals] == ["h1", "a1"]


def test_aws_config_default_uses_virtual_hosted_style(mocker):
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_S3_ENDPOINT_URL", "")
    config = chat_graph._aws_config()
    assert config.s3 is None


def test_aws_config_with_s3_endpoint_uses_path_style(mocker):
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_S3_ENDPOINT_URL", "http://localhost:9000")
    config = chat_graph._aws_config()
    assert config.s3 == {"addressing_style": "path"}


def test_validate_chat_llm_config_accepts_mock_and_rejects_missing_model(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "mock")
    chat_graph.validate_chat_llm_config()

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "litellm")
    mocker.patch("reporting.settings.CHAT_LLM_MODEL", "")
    with pytest.raises(ValueError, match="CHAT_LLM_MODEL is required"):
        chat_graph.validate_chat_llm_config()


def test_get_chat_model_builds_litellm_streaming_client(mocker):
    model = object()
    model_factory = mocker.patch("langchain_litellm.ChatLiteLLM", return_value=model)
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_LLM_MODEL", "gpt-4o")
    mocker.patch("reporting.settings.CHAT_LLM_TEMPERATURE", 0.2)
    mocker.patch("reporting.settings.CHAT_LLM_TIMEOUT_SECONDS", 45.0)
    mocker.patch("reporting.settings.CHAT_LLM_MAX_RETRIES", 3)
    mocker.patch("reporting.settings.CHAT_LLM_MAX_TOKENS", 2048)
    mocker.patch("reporting.settings.CHAT_LLM_API_KEY", "chat-key")
    mocker.patch("reporting.settings.CHAT_LLM_BASE_URL", "https://llm.example.com")
    chat_graph.get_chat_model.cache_clear()

    try:
        assert chat_graph.get_chat_model() is model
    finally:
        chat_graph.get_chat_model.cache_clear()

    model_factory.assert_called_once_with(
        model="openai/gpt-4o",
        temperature=0.2,
        request_timeout=45.0,
        max_retries=3,
        streaming=True,
        max_tokens=2048,
        api_key="chat-key",
        api_base="https://llm.example.com",
    )


def test_legacy_provider_api_key_prefers_gemini_then_google(mocker):
    mocker.patch("reporting.settings.GEMINI_API_KEY", "gemini-key")
    mocker.patch("reporting.settings.GOOGLE_API_KEY", "google-key")
    assert chat_graph._legacy_provider_api_key("gemini") == "gemini-key"

    mocker.patch("reporting.settings.GEMINI_API_KEY", "")
    assert chat_graph._legacy_provider_api_key("gemini") == "google-key"
    assert chat_graph._legacy_provider_api_key("unknown") == ""


def test_build_dynamodb_checkpointer_forwards_ttl_and_s3_offload_settings(mocker):
    saver = object()
    saver_factory = mocker.patch("reporting.services.chat_graph.DynamoDBSaver", return_value=saver)
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_TTL_SECONDS", 3600)
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_S3_BUCKET", "chat-checkpoints")
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_S3_ENDPOINT_URL", "http://minio:9000")
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_S3_KEY_PREFIX", "threads/")
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_TABLE_NAME", "chat-table")
    mocker.patch("reporting.settings.DYNAMODB_REGION", "us-east-1")
    mocker.patch("reporting.settings.DYNAMODB_ENDPOINT_URL", "http://dynamodb:8000")
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_ENABLE_COMPRESSION", True)

    assert chat_graph._build_dynamodb_checkpointer() is saver
    saver_factory.assert_called_once_with(
        table_name="chat-table",
        region_name="us-east-1",
        endpoint_url="http://dynamodb:8000",
        boto_config=mocker.ANY,
        ttl_seconds=3600,
        enable_checkpoint_compression=True,
        s3_offload_config={
            "bucket_name": "chat-checkpoints",
            "endpoint_url": "http://minio:9000",
            "key_prefix": "threads/",
        },
    )


async def test_initialize_chat_checkpoints_respects_create_table_setting(mocker):
    initialize = mocker.patch("reporting.services.chat_graph._initialize_chat_checkpoints_sync")
    to_thread = mocker.patch("reporting.services.chat_graph.asyncio.to_thread", new=mocker.AsyncMock())
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_BACKEND", "dynamodb")

    mocker.patch("reporting.settings.CHAT_CHECKPOINT_CREATE_TABLE", False)
    await chat_graph.initialize_chat_checkpoints()
    to_thread.assert_not_awaited()

    mocker.patch("reporting.settings.CHAT_CHECKPOINT_CREATE_TABLE", True)
    await chat_graph.initialize_chat_checkpoints()
    to_thread.assert_awaited_once_with(initialize)


def test_chat_checkpoint_backend_normalizes_postgres_aliases_and_rejects_unknown(mocker):
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_BACKEND", "postgresql")
    assert chat_graph._chat_checkpoint_backend() == "postgres"

    mocker.patch("reporting.settings.CHAT_CHECKPOINT_BACKEND", "sql")
    assert chat_graph._chat_checkpoint_backend() == "postgres"

    mocker.patch("reporting.settings.CHAT_CHECKPOINT_BACKEND", "unknown")
    with pytest.raises(ValueError, match="Unknown chat checkpoint backend"):
        chat_graph._chat_checkpoint_backend()


def test_postgres_checkpoint_url_accepts_postgres_and_converts_asyncpg(mocker):
    mocker.patch(
        "reporting.settings.CHAT_CHECKPOINT_DATABASE_URL",
        "postgresql+asyncpg://db/seizu",
    )
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_DATABASE_USER", "user")
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_DATABASE_PASSWORD", "p@ssword")
    assert chat_graph._postgres_checkpoint_url() == "postgresql://user:p%40ssword@db/seizu"

    mocker.patch("reporting.settings.CHAT_CHECKPOINT_DATABASE_URL", "sqlite:///seizu.db")
    with pytest.raises(ValueError, match="must be a PostgreSQL URL"):
        chat_graph._postgres_checkpoint_url()


async def test_initialize_postgres_chat_checkpoints_builds_pool_and_graph(mocker):
    pool = mocker.Mock()
    pool.open = mocker.AsyncMock()
    pool.wait = mocker.AsyncMock()
    pool.close = mocker.AsyncMock()
    pool_factory = mocker.patch("reporting.services.chat_graph.AsyncConnectionPool", return_value=pool)
    setup = mocker.patch("reporting.services.chat_graph._setup_postgres_checkpointer", new=mocker.AsyncMock())
    saver = mocker.Mock()
    saver_factory = mocker.patch("reporting.services.chat_graph.AsyncPostgresSaver", return_value=saver)
    graph = object()
    build_graph = mocker.patch("reporting.services.chat_graph.build_chat_graph", return_value=graph)
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_BACKEND", "postgres")
    mocker.patch(
        "reporting.settings.CHAT_CHECKPOINT_DATABASE_URL",
        "postgresql://postgres:5432/seizu",
    )
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_DATABASE_USER", "user")
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_DATABASE_PASSWORD", "pass")
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_DATABASE_POOL_MIN_SIZE", 2)
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_DATABASE_POOL_MAX_SIZE", 8)
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_CREATE_TABLE", True)
    chat_graph._chat_checkpoint_pool = None
    chat_graph._chat_graph = None

    await chat_graph.initialize_chat_checkpoints()

    pool_factory.assert_called_once_with(
        conninfo="postgresql://user:pass@postgres:5432/seizu",
        min_size=2,
        max_size=8,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": chat_graph.dict_row,
        },
    )
    pool.open.assert_awaited_once_with()
    pool.wait.assert_awaited_once_with()
    setup.assert_awaited_once_with(pool)
    saver_factory.assert_called_once_with(pool)
    build_graph.assert_called_once_with(saver)
    assert chat_graph.get_chat_graph() is graph

    await chat_graph.close_chat_checkpoints()
    pool.close.assert_awaited_once_with()
    assert chat_graph._chat_checkpoint_pool is None
    assert chat_graph._chat_graph is None


async def test_initialize_postgres_chat_checkpoints_rejects_invalid_pool_bounds(mocker):
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_BACKEND", "postgres")
    mocker.patch(
        "reporting.settings.CHAT_CHECKPOINT_DATABASE_URL",
        "postgresql://postgres:5432/seizu",
    )
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_DATABASE_USER", "user")
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_DATABASE_PASSWORD", "pass")
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_DATABASE_POOL_MIN_SIZE", 5)
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_DATABASE_POOL_MAX_SIZE", 2)
    chat_graph._chat_checkpoint_pool = None
    chat_graph._chat_graph = None

    with pytest.raises(ValueError, match="must satisfy"):
        await chat_graph.initialize_chat_checkpoints()


async def test_setup_postgres_checkpointer_serializes_migrations(mocker):
    waiting_cursor = mocker.Mock()
    waiting_cursor.fetchone = mocker.AsyncMock(return_value={"acquired": False})
    acquired_cursor = mocker.Mock()
    acquired_cursor.fetchone = mocker.AsyncMock(return_value={"acquired": True})
    connection = mocker.Mock()
    connection.execute = mocker.AsyncMock(
        side_effect=[
            waiting_cursor,
            acquired_cursor,
            None,
        ]
    )
    connection_context = mocker.MagicMock()
    connection_context.__aenter__ = mocker.AsyncMock(return_value=connection)
    connection_context.__aexit__ = mocker.AsyncMock(return_value=False)
    pool = mocker.Mock()
    pool.connection.return_value = connection_context
    saver = mocker.Mock()
    saver.setup = mocker.AsyncMock()
    saver_factory = mocker.patch("reporting.services.chat_graph.AsyncPostgresSaver", return_value=saver)
    sleep = mocker.patch("reporting.services.chat_graph.asyncio.sleep", new=mocker.AsyncMock())

    await chat_graph._setup_postgres_checkpointer(pool)

    saver_factory.assert_called_once_with(connection)
    saver.setup.assert_awaited_once_with()
    sleep.assert_awaited_once_with(0.1)
    assert connection.execute.await_args_list == [
        mocker.call("SELECT pg_try_advisory_lock(hashtextextended('seizu-chat-checkpoint-setup', 0)) AS acquired"),
        mocker.call("SELECT pg_try_advisory_lock(hashtextextended('seizu-chat-checkpoint-setup', 0)) AS acquired"),
        mocker.call("SELECT pg_advisory_unlock(hashtextextended('seizu-chat-checkpoint-setup', 0))"),
    ]


def test_initialize_chat_checkpoints_creates_missing_table_and_ttl(mocker):
    class _Waiter:
        def __init__(self) -> None:
            self.calls = []

        def wait(self, **kwargs):
            self.calls.append(kwargs)

    class _Client:
        def __init__(self) -> None:
            self.created = []
            self.ttl = []
            self.waiter = _Waiter()

        def describe_table(self, **_kwargs):
            raise chat_graph.ClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "missing"}},
                "DescribeTable",
            )

        def create_table(self, **kwargs):
            self.created.append(kwargs)

        def get_waiter(self, name):
            assert name == "table_exists"
            return self.waiter

        def update_time_to_live(self, **kwargs):
            self.ttl.append(kwargs)

    client = _Client()
    mocker.patch(
        "reporting.services.chat_graph._build_dynamodb_checkpointer",
        return_value=type("_Saver", (), {"client": client})(),
    )
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_TABLE_NAME", "chat-table")
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_TTL_SECONDS", 3600)

    chat_graph._initialize_chat_checkpoints_sync()

    assert client.created[0]["TableName"] == "chat-table"
    assert client.waiter.calls == [{"TableName": "chat-table"}]
    assert client.ttl == [
        {
            "TableName": "chat-table",
            "TimeToLiveSpecification": {"Enabled": True, "AttributeName": "ttl"},
        }
    ]


def test_initialize_chat_checkpoints_accepts_existing_table(mocker):
    client = mocker.Mock()
    mocker.patch(
        "reporting.services.chat_graph._build_dynamodb_checkpointer",
        return_value=type("_Saver", (), {"client": client})(),
    )
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_TABLE_NAME", "chat-table")
    mocker.patch("reporting.settings.CHAT_CHECKPOINT_TTL_SECONDS", 0)

    chat_graph._initialize_chat_checkpoints_sync()

    client.describe_table.assert_called_once_with(TableName="chat-table")
    client.create_table.assert_not_called()
    client.update_time_to_live.assert_not_called()


def test_collapse_ephemeral_continuations_discards_orphaned_continuation():
    """Continuation is discarded when the preceding cut-off AIMessage is absent."""
    from langchain_core.messages import AIMessage, HumanMessage

    from reporting.services.chat_graph import _collapse_ephemeral_continuations
    from reporting.services.chat_messages import MessageTag, tag_message

    human = HumanMessage(content="hi", id="h1")
    ephemeral = HumanMessage(content="[continue]", id="e1")
    tag_message(ephemeral, MessageTag.EPHEMERAL)
    ephemeral.additional_kwargs["continue_response"] = True
    continuation = AIMessage(content="continuation text", id="a2")

    # Simulates a checkpoint where the original cut-off AIMessage was trimmed,
    # leaving only the ephemeral continue-request and the continuation.
    result = _collapse_ephemeral_continuations([human, ephemeral, continuation])

    assert len(result) == 1
    assert result[0].id == "h1"


def test_collapse_ephemeral_continuations_merges_when_preceding_ai_present():
    from langchain_core.messages import AIMessage, HumanMessage

    from reporting.services.chat_graph import _collapse_ephemeral_continuations
    from reporting.services.chat_messages import MessageTag, tag_message

    human = HumanMessage(content="hi", id="h1")
    original = AIMessage(content="partial", id="a1")
    ephemeral = HumanMessage(content="[continue]", id="e1")
    tag_message(ephemeral, MessageTag.EPHEMERAL)
    ephemeral.additional_kwargs["continue_response"] = True
    continuation = AIMessage(content="rest", id="a2")

    result = _collapse_ephemeral_continuations([human, original, ephemeral, continuation])

    assert len(result) == 2
    assert result[-1].id == "a1"
    assert "partial" in result[-1].content
    assert "rest" in result[-1].content


def test_output_limit_notice_uses_shared_constant():
    """_strip_output_limit_notice removes the same text _append_output_limit_notice adds."""
    original = "Some partial response."
    appended, hit = chat_graph._append_output_limit_notice(original, "length", ["tool ran"])
    assert hit is True
    stripped = chat_graph._strip_output_limit_notice(appended)
    assert stripped == original


async def test_chat_graph_persists_seizu_output_limit_in_metadata(mocker):
    """output_limit responses store seizu_output_limit=True in response_metadata."""
    from langgraph.checkpoint.memory import MemorySaver

    class _LimitModel:
        async def astream(self, input, config=None, **kwargs):
            yield AIMessageChunk(
                content="partial",
                response_metadata={"finish_reason": "length"},
            )

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=_LimitModel())
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_tools_for_user", return_value=[])
    graph = chat_graph.build_chat_graph(MemorySaver())

    async for _ in graph.astream(
        {"messages": [HumanMessage(content="go")]},
        {"configurable": {"thread_id": "thread-meta-limit", "current_user": _user()}},
        stream_mode="custom",
    ):
        pass

    state = await graph.aget_state({"configurable": {"thread_id": "thread-meta-limit"}})
    last = state.values["messages"][-1]
    assert last.response_metadata.get("seizu_output_limit") is True


async def test_empty_synthesis_response_marked_broken(mocker):
    """Empty synthesis turn with finish_reason=length goes to _empty_response_fallback."""
    from langgraph.checkpoint.memory import MemorySaver

    call_count = 0

    class _ToolThenEmptyModel:
        async def astream(self, input, config=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First turn: return a tool call
                yield AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": "no_such_tool",
                            "args": "{}",
                            "id": "tc1",
                            "index": 0,
                        }
                    ],
                )
            else:
                # Synthesis turn: hit output limit before any text
                yield AIMessageChunk(
                    content="",
                    response_metadata={"finish_reason": "length"},
                )

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=_ToolThenEmptyModel())
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_tools_for_user", return_value=[])
    graph = chat_graph.build_chat_graph(MemorySaver())

    chunks: list[dict[str, Any]] = []
    async for chunk in graph.astream(
        {"messages": [HumanMessage(content="do something")]},
        {"configurable": {"thread_id": "thread-empty-synth", "current_user": _user()}},
        stream_mode="custom",
    ):
        chunks.append(chunk)

    # Broken synthesis should not emit finish_reason:length (no spurious Continue button).
    finish_reason_events = [c for c in chunks if c.get("kind") == "finish_reason"]
    assert not finish_reason_events


def test_bypass_confirmations_from_config():
    helper = chat_graph._bypass_confirmations_from_config
    assert helper({}) is False
    assert helper({"configurable": {}}) is False
    assert helper({"configurable": {"bypass_confirmations": True}}) is True
    assert helper({"configurable": {"bypass_confirmations": False}}) is False
    assert helper({"configurable": {"bypass_confirmations": "yes"}}) is False


def test_headless_from_config():
    helper = chat_graph._headless_from_config
    assert helper({}) is False
    assert helper({"configurable": {"headless": True}}) is True
    assert helper({"configurable": {"headless": False}}) is False


async def test_run_tool_call_bypass_uses_bypass_instead_of_confirmation(mocker):
    call_tool = mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        mocker.AsyncMock(return_value=ChatActionOutcome(text="{}", blocked=None)),
    )
    spec = chat_graph.ChatToolSpec(
        name="reports__create_version",
        kind="tool",
        description="",
        input_schema={"type": "object", "properties": {}},
    )
    request = chat_graph.ToolCallRequest(id="call-1", name="reports__create_version", arguments={}, spec=spec)

    await chat_graph._run_tool_call(
        request,
        None,
        session_key="thread-1",
        batch_id=None,
        bypass_confirmations=True,
    )

    kwargs = call_tool.await_args.kwargs
    assert kwargs["bypass_confirmations"] is True
    assert "confirmation_source" not in kwargs
    assert "confirmation_session_key" not in kwargs


async def test_run_tool_call_interactive_keeps_confirmation_flow(mocker):
    call_tool = mocker.patch(
        "reporting.services.chat_graph.mcp_runtime.call_tool_for_chat",
        mocker.AsyncMock(return_value=ChatActionOutcome(text="{}", blocked=None)),
    )
    spec = chat_graph.ChatToolSpec(
        name="reports__create_version",
        kind="tool",
        description="",
        input_schema={"type": "object", "properties": {}},
    )
    request = chat_graph.ToolCallRequest(id="call-1", name="reports__create_version", arguments={}, spec=spec)

    await chat_graph._run_tool_call(request, None, session_key="thread-1", batch_id=None)

    kwargs = call_tool.await_args.kwargs
    assert kwargs["confirmation_source"] == "chat"
    assert kwargs["confirmation_session_key"] == "thread-1"
    assert "bypass_confirmations" not in kwargs
