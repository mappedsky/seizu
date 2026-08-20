"""Prototype external MCP server exposing deps.dev package metadata.

Seizu's graph records which package versions are *installed* -- ``version`` and a
resolved ``requirements`` pin per manifest -- and which of them fall in a CVE's
vulnerable range. What it does not record is what a package *declares it needs*,
so a sub-agent asked whether a vulnerable transitive dependency is genuinely
pulled in has nowhere to look. Read off one measured delegation, it filled the
gap by recalling package history: "botocore pins urllib3 < 1.27 historically...
But this is a fictional future scenario". That is a security verdict resting on
training data (AGT-036).

The sandbox cannot fetch it either -- it has no egress, not even DNS -- so this
runs as an ordinary external MCP proxy: Seizu reaches it through
``MCP_EXTERNAL_PROXIES`` and the tools arrive as ``ext__deps__*``. Nothing but a
package name and version leaves the network, and every tool is read-only.

Prototype: it lives in ``scripts/`` and runs on the Seizu image because that
image already carries the MCP SDK and an HTTP client. Promote it to its own
package and image if it earns a place.

    docker compose --profile external-mcp up -d external-mcp-deps
"""

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations
from starlette.applications import Starlette
from starlette.routing import Mount

logger = logging.getLogger("deps_dev_mcp")

API = os.environ.get("DEPS_DEV_API", "https://api.deps.dev/v3")
TIMEOUT = float(os.environ.get("DEPS_DEV_TIMEOUT_SECONDS", "20"))
#: A resolved graph runs to hundreds of nodes; past this it stops being an answer
#: and becomes a listing for something else to process.
MAX_NODES = int(os.environ.get("DEPS_DEV_MAX_NODES", "400"))

#: deps.dev spells these in caps and rejects anything else. Kept as an explicit
#: map so an unknown ecosystem fails here rather than as a 404 from the API.
SYSTEMS = {
    "cargo": "CARGO",
    "go": "GO",
    "maven": "MAVEN",
    "npm": "NPM",
    "nuget": "NUGET",
    "pypi": "PYPI",
    "rubygems": "RUBYGEMS",
}

_SYSTEM_SCHEMA = {
    "type": "string",
    "description": f"Package ecosystem, one of: {', '.join(sorted(SYSTEMS))}.",
    "enum": sorted(SYSTEMS),
}
_PKG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "system": _SYSTEM_SCHEMA,
        "name": {"type": "string", "description": "Package name as the ecosystem spells it, e.g. 'botocore'."},
        "version": {"type": "string", "description": "Exact version, e.g. '1.42.91'. Not a range."},
    },
    "required": ["system", "name", "version"],
}

TOOLS = [
    Tool(
        name="get_requirements",
        description=(
            "What a package version declares it requires: the dependency constraints written in its own "
            "metadata (Requires-Dist, package.json dependencies, and the equivalent per ecosystem). Use this "
            "to answer whether one package's declared range admits another's installed version -- do not "
            "answer that from memory. Covers cargo, go, maven, npm, nuget, pypi, rubygems."
        ),
        input_schema=_PKG_SCHEMA,
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="get_dependencies",
        description=(
            "The resolved dependency graph for a package version: every package actually pulled in, not just "
            "the direct ones. Use it to tell a transitive dependency from an unused one. Bounded; a graph "
            "larger than the bound comes back marked truncated. Covers npm, cargo, maven, pypi."
        ),
        input_schema=_PKG_SCHEMA,
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="find_dependency_path",
        description=(
            "Whether a package version pulls in a given target package, and by which chain. Answers the "
            "reachability question directly -- 'confidant does not import urllib3, but does botocore pull it "
            "in, and how?' -- and returns a short path rather than a whole graph."
        ),
        input_schema={
            "type": "object",
            "properties": {
                **_PKG_SCHEMA["properties"],
                "target": {"type": "string", "description": "Package name to look for, e.g. 'urllib3'."},
            },
            "required": ["system", "name", "version", "target"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
]


async def _get(client: httpx2.AsyncClient, path: str) -> dict[str, Any] | str:
    """One deps.dev call, with its failures turned into text rather than raised."""
    try:
        response = await client.get(f"{API}{path}", timeout=TIMEOUT)
    except Exception as exc:  # network, DNS, timeout
        return f"deps.dev is unreachable: {exc.__class__.__name__}"
    if response.status_code == 404:
        return "Not found on deps.dev. Check the ecosystem, the exact name, and that the version is a real release."
    if response.status_code != 200:
        return f"deps.dev returned {response.status_code}."
    try:
        return dict(response.json())
    except Exception:
        return "deps.dev returned a body that is not JSON."


def _version_key(system: str, name: str, version: str) -> str:
    from urllib.parse import quote

    return f"/systems/{SYSTEMS[system]}/packages/{quote(name, safe='')}/versions/{quote(version, safe='')}"


def _nodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for node in payload.get("nodes") or []:
        key = node.get("versionKey") or {}
        out.append({"name": key.get("name"), "version": key.get("version"), "relation": node.get("relation")})
    return out


async def _requirements(client: Any, args: dict[str, Any], *, get: Any = None) -> Any:
    get = get or _get
    got = await get(client, f"{_version_key(args['system'], args['name'], args['version'])}:requirements")
    if isinstance(got, str):
        return got
    return {"package": args["name"], "version": args["version"], "requirements": got}


async def _dependencies(client: Any, args: dict[str, Any], *, get: Any = None) -> Any:
    get = get or _get
    got = await get(client, f"{_version_key(args['system'], args['name'], args['version'])}:dependencies")
    if isinstance(got, str):
        return got
    nodes = _nodes(got)
    return {
        "package": args["name"],
        "version": args["version"],
        "resolved": nodes[:MAX_NODES],
        "count": len(nodes),
        "truncated": len(nodes) > MAX_NODES,
    }


async def _dependency_path(client: Any, args: dict[str, Any], *, get: Any = None) -> Any:
    get = get or _get
    got = await get(client, f"{_version_key(args['system'], args['name'], args['version'])}:dependencies")
    if isinstance(got, str):
        return got
    nodes = _nodes(got)
    target = str(args["target"]).lower()
    hits = [n for n in nodes if str(n.get("name") or "").lower() == target]
    if not hits:
        return {
            "package": args["name"],
            "target": args["target"],
            "pulls_in": False,
            "note": f"{args['target']} is not in the resolved graph of {args['name']} {args['version']}.",
        }
    # Edges are indices into `nodes`; walk back from the target to the root so the
    # answer is the chain rather than the graph.
    edges = got.get("edges") or []
    parents: dict[int, int] = {}
    for edge in edges:
        parents.setdefault(int(edge.get("toNode", -1)), int(edge.get("fromNode", -1)))
    index = {(n["name"], n["version"]): i for i, n in enumerate(nodes)}
    paths = []
    for hit in hits:
        i = index.get((hit["name"], hit["version"]))
        chain, guard = [], 0
        while i is not None and i >= 0 and guard < 64:
            chain.append(f"{nodes[i]['name']}@{nodes[i]['version']}")
            i = parents.get(i)
            guard += 1
        paths.append(" <- ".join(chain))
    return {"package": args["name"], "target": args["target"], "pulls_in": True, "versions": hits, "paths": paths}


HANDLERS = {
    "get_requirements": _requirements,
    "get_dependencies": _dependencies,
    "find_dependency_path": _dependency_path,
}


async def _list_tools(_ctx: Any, _params: Any) -> ListToolsResult:
    return ListToolsResult(tools=TOOLS)


async def _call_tool(_ctx: Any, params: CallToolRequestParams) -> CallToolResult:
    handler = HANDLERS.get(params.name)
    if handler is None:
        return CallToolResult(content=[TextContent(type="text", text=f"Unknown tool {params.name}")], is_error=True)
    args = dict(params.arguments or {})
    missing = [k for k in ("system", "name", "version") if not str(args.get(k) or "").strip()]
    if missing:
        text = f"Missing required argument(s): {', '.join(missing)}"
        return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)
    if args["system"] not in SYSTEMS:
        text = f"Unknown ecosystem {args['system']!r}. Use one of: {', '.join(sorted(SYSTEMS))}."
        return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)
    async with httpx2.AsyncClient() as client:
        result = await handler(client, args)
    text = result if isinstance(result, str) else json.dumps(result, indent=2, default=str)
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=isinstance(result, str))


def build_app() -> Starlette:
    """The ASGI app: one MCP endpoint at /mcp.

    The session manager owns background tasks, so it is started and stopped by
    the app's lifespan rather than entered ad hoc -- the same arrangement Seizu's
    own MCP transport uses.
    """
    server = Server("deps-dev", on_list_tools=_list_tools, on_call_tool=_call_tool)
    manager = StreamableHTTPSessionManager(app=server, event_store=None, json_response=True, stateless=True)

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            yield

    # Mounted at the root, not at "/mcp". Starlette's Mount redirects a path
    # matching the mount prefix to the same path with a trailing slash, and a
    # 307 on the POST is what an MCP client sees as "request failed" -- the same
    # trailing-slash problem Seizu's own transport avoids by not using Mount.
    # Mounting at the root means any path reaches the handler, /mcp included.
    return Starlette(routes=[Mount("/", app=StreamableHTTPASGIApp(manager))], lifespan=lifespan)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
