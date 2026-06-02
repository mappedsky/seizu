import asyncio
import json

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.modifier import RemoveMessage
from mcp.types import Prompt, PromptArgument, Tool

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.confirmations import ActionConfirmation
from reporting.schema.report_config import User
from reporting.services import chat_graph
from reporting.services.chat_messages import MessageTag, has_tag
from reporting.services.mcp_runtime import ChatActionOutcome, ChatBlockReason

_NOW = "2024-01-01T00:00:00+00:00"


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


class _ToolCallingFakeModel:
    def __init__(self, responses: list[AIMessage | AIMessageChunk]) -> None:
        self.responses = responses
        self.calls = 0
        self.inputs = []
        self.bound_tools = []

    def bind_tools(self, tools):
        self.bound_tools.append(tools)
        return self

    async def astream(self, input, config=None, **kwargs):
        self.inputs.append(input)
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        yield self.responses[index]


def test_deepseek_reasoning_content_is_added_to_streamed_chunks():
    class _BaseModel:
        def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
            return type("GenerationChunk", (), {"message": AIMessageChunk(content="")})()

    class _Model(chat_graph.SeizuChatDeepSeekMixin, _BaseModel):
        pass

    chunk = {
        "choices": [
            {
                "delta": {"role": "assistant", "reasoning_content": "checked tools"},
                "finish_reason": None,
            }
        ]
    }
    model = _Model()

    generation_chunk = model._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)

    assert generation_chunk.message.additional_kwargs["reasoning_content"] == "checked tools"


async def test_run_llm_tool_turn_streams_reasoning_as_detail_and_strips_context():
    class _FakeModel:
        async def astream(self, input, config=None, **kwargs):
            yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": "checking graph"})
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
                "body": "checking graph",
            },
        }
    ]
    assert result.streamed == "Final answer."
    assert "reasoning_content" not in result.message.additional_kwargs


def test_deepseek_endpoint_detection_matches_exact_host_or_subdomain(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_BASE_URL", "https://api.deepseek.com/v1")
    assert chat_graph._uses_deepseek_compatible_endpoint("gateway-model") is True

    mocker.patch("reporting.settings.CHAT_LLM_BASE_URL", "https://deepseek.com/v1")
    assert chat_graph._uses_deepseek_compatible_endpoint("gateway-model") is True

    mocker.patch("reporting.settings.CHAT_LLM_BASE_URL", "https://notdeepseek.com/v1")
    assert chat_graph._uses_deepseek_compatible_endpoint("gateway-model") is False

    mocker.patch("reporting.settings.CHAT_LLM_BASE_URL", "")
    assert chat_graph._uses_deepseek_compatible_endpoint("deepseek-chat") is True


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
    assert len(details) == 1
    assert details[0]["data"]["title"] == "Tool: security__one"
    assert details[0]["data"]["arguments"] == '{"org":"mappedsky"}'
    assert details[0]["data"]["body"] == '{"ok": true}'


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
    list_tools = mocker.patch("reporting.services.chat_graph.mcp_runtime.list_tools_for_user")
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
    list_tools.assert_not_called()
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


async def test_pending_confirmation_response_includes_url():
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
    assert "https://seizu.example.com/app/confirmations/abc123" in response
    assert "panel" not in response.lower()


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


def test_build_system_prompt_is_seizu_specific():
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
