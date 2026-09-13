"""Local, unauthenticated MCP form/URL fixture on port 8099."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitRequestURLParams,
    ElicitResult,
    InputRequiredResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route


async def list_tools(ctx: Any, params: PaginatedRequestParams | None) -> ListToolsResult:
    return ListToolsResult(
        tools=[
            Tool(
                name=name,
                description=description,
                input_schema={"type": "object"},
                annotations=ToolAnnotations(read_only_hint=True),
            )
            for name, description in [
                ("ask_form", "Ask the browser user for a project name and a boolean."),
                ("ask_url", "Ask the browser user to visit the local completion page."),
            ]
        ]
    )


async def call_tool(ctx: Any, params: CallToolRequestParams) -> CallToolResult | InputRequiredResult:
    if params.name not in {"ask_form", "ask_url"}:
        return CallToolResult(is_error=True, content=[TextContent(type="text", text="Unknown tool")])
    if params.input_responses:
        response = params.input_responses.get("demo-input")
        if params.request_state != params.name or not isinstance(response, ElicitResult):
            return CallToolResult(is_error=True, content=[TextContent(type="text", text="Invalid continuation")])
        return CallToolResult(content=[TextContent(type="text", text=f"Demo interaction finished: {response.action}.")])
    request = (
        ElicitRequest(
            params=ElicitRequestFormParams(
                message="Demo form from the local external MCP server.",
                requested_schema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "title": "Project", "maxLength": 80},
                        "include_archived": {"type": "boolean", "title": "Include archived projects"},
                    },
                    "required": ["project", "include_archived"],
                },
            )
        )
        if params.name == "ask_form"
        else ElicitRequest(
            params=ElicitRequestURLParams(
                message="Open the local demo completion page, then choose Completed in chat.",
                url="http://localhost:8099/complete",
                elicitation_id="demo-url",
            )
        )
    )
    return InputRequiredResult(input_requests={"demo-input": request}, request_state=params.name)


manager = StreamableHTTPSessionManager(
    app=Server("elicitation-demo", on_list_tools=list_tools, on_call_tool=call_tool),
    json_response=True,
    stateless=True,
)


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    async with manager.run():
        yield


async def complete(request: Request) -> PlainTextResponse:
    return PlainTextResponse("Demo interaction complete. Return to Seizu and choose Completed.")


app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/complete", complete),
        Mount("/mcp", app=StreamableHTTPASGIApp(manager)),
    ],
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8099)
