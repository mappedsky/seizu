# Sandbox Delegation

## Purpose

The `sandbox__delegate` chat tool lets the chat agent hand off tasks requiring code execution or file operations to an isolated sandbox. The agent can run Python, execute shell commands, and read/write files — then returns a summary of what happened. Use it when a task involves data processing, scripting, or file manipulation that cannot be expressed as a Cypher query.

Sandbox delegation is **chat-only**: the tool never appears in the MCP server's tool listing and cannot be called by external MCP clients. The sandbox is isolated from Seizu's internal services and credentials, and is shared by the delegations of a conversation rather than created per call — see [Getting data into the sandbox](#getting-data-into-the-sandbox) below for its lifecycle.

## Architecture

```
Seizu chat agent
  → sandbox__delegate(task="...", context="...", tools=["..."])
    → sandbox_session        # the conversation's sandbox: resumed, then suspended again
      → SandboxBackend       # stable five-operation interface
        → create_react_agent with run_python / run_bash / read_file / write_file / list_files
                              + the bound Seizu tools + the discovery tools (see below)
    → result string
```

The `SandboxBackend` protocol (`reporting/services/mcp_builtins/sandbox.py`) defines the five operations the inner agent can use:

| Method | Description |
|--------|-------------|
| `run_python(code)` | Run Python code; returns stdout/stderr/result as text |
| `run_bash(cmd)` | Run a shell command; returns stdout/stderr as text |
| `read_file(path)` | Return file contents as text |
| `write_file(path, content)` | Write content to a file; return confirmation |
| `list_files(path)` | List files/directories; return human-readable text |

These names and descriptions are fixed regardless of which backend is active, so the inner agent's behaviour is consistent across providers.

### Getting data into the sandbox

Alongside those five operations the inner agent gets the Seizu tools its user is
entitled to (`graph__query`, user-defined toolset tools, and so on), so it can
fetch data itself rather than having it relayed through `context`.

A result that fits is returned to the agent as it always was. A result **too
large to return** — more rows than `CHAT_TOOL_RESULT_MAX_ROWS`, or more bytes
than `SANDBOX_MAX_OUTPUT_BYTES` — is instead written to a file under
`/home/user/seizu_results/` and replaced by a receipt: the path, byte size, row count,
column names, and two sample rows, plus a note that the full data is in the
file. The agent then processes it with `run_python`.

Routing is decided by **size**, never by the agent. The sub-agent fetches to
`SANDBOX_FILE_RESULT_MAX_ROWS` and `SANDBOX_FILE_RESULT_MAX_BYTES` rather than
the context-protecting `CHAT_TOOL_RESULT_MAX_ROWS`, and either bound decides
whether a result is returned inline or filed. If the write fails, the truncated
result is returned instead. See [SBX-002](../dev/decisions/sandbox.md) for why
the agent is not given the choice.

**One sandbox per conversation, not per delegation.** A sandbox is opened lazily
on a turn's first `sandbox__delegate` call and shared by every delegation and
every step of that turn. At the end of the turn it is **suspended rather than
destroyed**, and the next turn of the same thread resumes it by id, so a
follow-up question reads files the previous turn wrote instead of re-running the
queries behind them.

Suspension keeps the **full VM state**, memory included
(`pause(keep_memory=True)`). Set `SANDBOX_SESSION_PERSIST=false` to return to a
sandbox per turn.

```{warning}
**Processes survive between turns.** A memory snapshot restores whatever was
running, so code the model executed in one turn can still be running in the
next, for the life of the conversation. The sandbox remains network-isolated
from Seizu's data stores and holds no credentials, and it stays bounded to a
single user's thread — but this is a wider blast radius than a per-turn
sandbox, and it is a deliberate trade rather than an oversight.

Filesystem-only suspension was tried and does not work: the code interpreter is
itself a process, so a resumed sandbox came back unable to run code at all.
Details in [SBX-005](../dev/decisions/sandbox.md).

**With `SANDBOX_ALLOW_INTERNET=true` this compounds.** A surviving process keeps
its outbound access, so code started in one turn can still be running — and
still reaching the internet — while later turns feed the sandbox new data. That
is an exfiltration path for anything supplied after the process started, not
just for what it was given at the time. Treat persistence and outbound internet
as a combination to enable deliberately, not independently.

If you would rather not accept it, `SANDBOX_SESSION_PERSIST=false` destroys the
sandbox at the end of every turn, at the cost of each turn re-fetching what the
last one gathered.
```

Deleting the chat thread destroys its sandbox, and never fails the deletion if
the provider call fails — an orphan is logged with its id. Lifecycle rationale
(turn-level scope, resume failures, error paths) is in
[SBX-005 through SBX-007](../dev/decisions/sandbox.md).

### Reaping abandoned sandboxes

A conversation a user simply stops replying to is never deleted, so nothing
tells Seizu its sandbox is finished with. `SANDBOX_SESSION_TIMEOUT_SECONDS`
bounds a *running* sandbox, not a suspended one, so those would otherwise
accumulate until the provider's own retention reclaimed them.

The **Temporal worker** sweeps them up: every
`SANDBOX_REAP_INTERVAL_SECONDS` it lists the provider's suspended sandboxes and
destroys the ones idle beyond `SANDBOX_REAP_IDLE_SECONDS`.

| Setting | Default | Meaning |
|---|---|---|
| `SANDBOX_REAP_ENABLED` | `true` | Run the sweep at all. |
| `SANDBOX_REAP_IDLE_SECONDS` | `86400` | How long a suspended sandbox may sit unused. `0` disables reaping. |
| `SANDBOX_REAP_INTERVAL_SECONDS` | `900` | Time between sweeps. |
| `SANDBOX_REAP_UNTAGGED` | `false` | Also reap suspended sandboxes carrying no Seizu tag. |

The sweep does not depend on `SANDBOX_ENABLED` — turning delegation off is when
leftover sandboxes most need collecting — but it does need `SANDBOX_API_KEY` or
`SANDBOX_DOMAIN` to reach the provider.

Every sandbox Seizu creates is tagged with a `seizu_managed` metadata key, and
the sweep touches nothing else — the listing is account-wide, so an untagged
sandbox may belong to another deployment or another tool.

**Sandboxes created before this feature carry no tag**, so an existing backlog
is not cleared by upgrading. Set `SANDBOX_REAP_UNTAGGED=true` for a while to
collect it, and only when these credentials belong to this deployment alone.

```{warning}
**The sweep runs in the Temporal worker only** (`seizu-temporal-worker`), where
it is a single process rather than one sweep per web worker. A deployment that
runs no Temporal worker does not reap: set `SANDBOX_SESSION_PERSIST=false`
there, which destroys the sandbox at the end of every turn.

**Treat `SANDBOX_REAP_IDLE_SECONDS` as an upper bound on a sandbox's life, not
as a precise idle timer.** Idle time is inferred from the provider's own
timestamps, since there is no way to stamp a last-used time on a sandbox after
it is created. If the provider does not advance them on resume, a conversation
still in use past that age loses its accumulated files and its next turn starts
from a fresh sandbox — recoverable, but it costs that turn the work. Keep the
value comfortably longer than a conversation's active span. Details in
[SBX-011](../dev/decisions/sandbox.md).
```

### Session memory

Sub-agents get a **recall block** naming what earlier sub-agents in the same
step found, what earlier *turns* established, and the files already saved in the
shared sandbox. The top-level agent gets a shorter **digest** of the same
material. A file is only advertised for the sandbox it was written in, so a
replacement sandbox stops offering receipts for files that no longer exist. Both
blocks are fenced as untrusted data. See
[SBX-008](../dev/decisions/sandbox.md).

Bounds are `CHAT_SESSION_MEMORY_MAX_ENTRIES`,
`CHAT_SESSION_MEMORY_MAX_RECEIPTS` and `CHAT_SESSION_MEMORY_DIGEST_MAX_CHARS`;
the memory rides in the thread's LangGraph checkpoint, which is already
namespaced per user.

The agent reads saved files with **`preview_file`**, which returns files at or
under `SANDBOX_PREVIEW_MAX_BYTES` whole and, above that, only shape plus the
beginning; `run_python` is how the full contents get used. A four-sample
comparison found no measurable difference against plain `read_file`, so set
`SANDBOX_PREVIEW_MAX_BYTES=0` if you prefer whole-file reads. Above
`SANDBOX_MAX_OUTPUT_BYTES`, `read_file` returns the beginning together with the
file's real size and an explicit statement that this is not the whole file.

### Adding a new backend

Implement `SandboxBackend` and open it inside `_open_backend`:

```python
# 1. Implement the protocol
class _MyBackend:
    async def run_python(self, code: str) -> str: ...
    async def run_bash(self, cmd: str) -> str: ...
    async def read_file(self, path: str) -> str: ...
    async def write_file(self, path: str, content: str) -> str: ...
    async def list_files(self, path: str) -> str: ...

# 2. Open it in _open_backend (select by a new SANDBOX_BACKEND setting)
@asynccontextmanager
async def _open_backend(*, api_key: str, domain: str) -> AsyncIterator[SandboxBackend]:
    ...
    my_sandbox = await _MyBackend.create(...)
    async with my_sandbox:
        yield my_sandbox
```

No other code needs to change: `_build_sandbox_tools`, `_handle_delegate`, the registry, the chat agent, and the tests are all backend-agnostic.

## Security model

The sandbox is ephemeral and isolated from Seizu's data stores and credentials — isolation is the safety mechanism. No confirmation gate is needed because the sandbox cannot reach Neo4j, DynamoDB, or any Seizu API. Outbound internet access from the sandbox is **off by default** and only enabled when you set `SANDBOX_ALLOW_INTERNET=true`.

The sandbox subagent can call read-only Seizu tools (and user-defined toolset tools) on the user's behalf, but never confirmation-gated mutating tools: those stay with the outer chat agent, where the user approves them interactively. The subagent runs to completion inside a single tool call and cannot drive the confirmation round-trip, so gated mutations are filtered out of its tool set and the runtime additionally refuses any gated tool reached without a confirmation context.

**Which tools it is given.** The sub-agent is *bound* the read-only graph tools
(`SANDBOX_CORE_TOOLS`, by default `graph__query`, `graph__schema`,
`graph__validate_query`, `graph__explain`), whatever the conversation has
already disclosed, and whatever the delegating call named in `tools`. Naming
`tools` narrows as well as widens: a caller that says what the task needs gets
that plus the core, not the disclosed set on top.

```{important}
The core set bypasses progressive disclosure, so **raw Cypher is available to
every delegation by default**. Anything the skill-gated read tools do,
`graph__query` can also do. Skill gating therefore narrows convenience and
curation, not reach.

To restrict graph access, use the control that actually bounds it:

- **RBAC** — a role without `query:execute` cannot reach the graph tools at all,
  in the sandbox or anywhere else. `SANDBOX_CORE_TOOLS` is intersected with the
  caller's permitted tools, so it can never widen access.
- **`SANDBOX_CORE_TOOLS`** — narrow it (e.g. to `graph__schema`) or set it empty
  to bind nothing up front, which routes even graph access through a skill or
  through the delegating model naming `tools`. Expect a discovery round trip on
  most delegations if you do.
```

**How it reaches anything else** is decided by `CHAT_LLM_PROGRESSIVE_DISCLOSURE`
— the same setting that governs the planner:

| `CHAT_LLM_PROGRESSIVE_DISCLOSURE` | Discovery tools | What `call_seizu_tool` may run |
|---|---|---|
| `false` | `find_seizu_tools`, `call_seizu_tool` | any tool the user may reach |
| `true` (default) | `find_seizu_skills`, `load_seizu_skill`, `call_seizu_tool` | only what a loaded skill declared |

With disclosure on, a skill's `tools_required` is what unlocks a tool, so **how
much the sub-agent can reach depends on how your skills are authored**. A skill
that declares no tools unlocks none; a deployment with no skills at all leaves
the sub-agent with its bound tools only, and the delegating model naming `tools`
becomes the route. A declaration naming a tool the user cannot reach unlocks
nothing — RBAC answers that, not the skill. Rationale and measured coverage:
[SBX-003 and SBX-004](../dev/decisions/sandbox.md).

Sandbox delegation requires the `sandbox:delegate` permission, which is granted to `seizu-editor` and above.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SANDBOX_ENABLED` | `false` | Master switch. Set `true` to allow the chat agent to use the `sandbox__delegate` tool. |
| `SANDBOX_API_KEY` | `""` | API key for the sandbox provider. Required for E2B cloud; leave empty for self-hosted deployments that use internal auth. |
| `SANDBOX_DOMAIN` | `""` | Sandbox service hostname. Empty → E2B cloud (`e2b.app`). For self-hosted deployments (e.g. OpenKruise Agents): set to your cluster ingress hostname. The E2B SDK constructs `https://api.<domain>` as the API base URL. |
| `SANDBOX_ALLOW_INTERNET` | `false` | Allow sandboxes to make outbound internet connections. Off by default for a hardened posture; enable only when a task legitimately needs network access. |
| `SANDBOX_TIMEOUT_SECONDS` | `120` | Maximum wall-clock time for one sandbox task. If exceeded, the delegation returns an error; the sandbox itself is **not** destroyed — it stays with the conversation and is suspended at the end of the turn like any other. |
| `SANDBOX_CORE_TOOLS` | `graph__query,graph__schema,graph__validate_query,graph__explain` | Tools bound to every delegation regardless of progressive disclosure. Intersected with the caller's RBAC-permitted tools, so it never widens access. Empty binds nothing up front. |
| `SANDBOX_MAX_OUTPUT_BYTES` | `50000` | Byte cap applied both to each inner tool result fed back to the sandbox agent and to the final result string returned to the chat agent. Larger output is truncated with a `[truncated]` suffix. |
| `SANDBOX_PREVIEW_MAX_BYTES` | `2000` | Bytes of a file `preview_file` returns. Files at or under this come back whole; larger ones return shape (size, lines, JSON structure, columns) plus the beginning, so a result file cannot be read back into context. `0` restores `read_file`. |
| `SANDBOX_SESSION_TIMEOUT_SECONDS` | `1800` | Lifetime of the sandbox shared by a turn's delegations. |
| `SANDBOX_SESSION_PERSIST` | `true` | Suspend the sandbox between turns and resume it on the next turn of the same thread, so a follow-up turn reads the data earlier turns fetched instead of re-fetching it. `false` destroys it at the end of every turn. |
| `SANDBOX_FILE_RESULT_MAX_ROWS` | `50000` | Row bound the sub-agent fetches to, so an oversized result can be detected and written to a file rather than silently truncated. Much higher than `CHAT_TOOL_RESULT_MAX_ROWS`, which protects a context window a file never enters. |
| `SANDBOX_FILE_RESULT_MAX_BYTES` | `10000000` | Byte cap for the same. Finite because the result materializes in the Seizu process before reaching the sandbox. |
| `SANDBOX_LLM_MODEL` | `""` | LiteLLM model ID for the inner sandbox agent. Empty → inherits `CHAT_LLM_MODEL`. Set a separate model when you want the sandbox subagent to use a cheaper or faster model than the outer chat agent. |

## Other sandbox consumers

The `SandboxBackend` protocol is also used outside the chat tool: the
`cve_dependency_remediation` Temporal workflow drives the sandbox directly
(no chat session, no tool call) to run a coding-agent CLI against a cloned
repository with phase-isolated credentials. See
[Temporal workflows](temporal-workflows.md) for its design and configuration
(`REMEDIATION_*`); it shares `SANDBOX_API_KEY`/`SANDBOX_DOMAIN` for the
sandbox provider itself.

## Providers

### E2B (default)

[E2B](https://e2b.dev) is the default backend. It runs each sandbox in an isolated Firecracker microVM.

1. Sign up at [e2b.dev](https://e2b.dev) and obtain an API key.
2. Set `SANDBOX_ENABLED=true` and `SANDBOX_API_KEY=e2b_...` in your environment.

E2B requires an outbound internet connection from the Seizu server to `https://api.e2b.app`. The sandboxes themselves have outbound internet **disabled** by default; set `SANDBOX_ALLOW_INTERNET=true` to allow it when a task needs network access.

### OpenKruise Agents (self-hosted)

[OpenKruise Agents](https://openkruise.io/kruiseagents/) is an E2B-API-compatible self-hosted alternative that runs sandboxes in Kubernetes pods. It is suitable for air-gapped or cost-sensitive deployments.

1. Deploy OpenKruise Agents to your cluster and expose its API (e.g. `sandbox.example.internal`).
2. Set `SANDBOX_ENABLED=true`, `SANDBOX_DOMAIN=sandbox.example.internal`, and `SANDBOX_API_KEY=<internal-token>` (or leave the key empty if the deployment uses internal auth without a client key).

The E2B SDK disables client-side API key format validation automatically when `SANDBOX_DOMAIN` is set, so non-E2B tokens are accepted without any special configuration.

## Local development

E2B's free tier allows a limited number of sandbox-seconds per month and is the simplest way to test sandbox delegation without running additional infrastructure.

1. Obtain an E2B API key from [e2b.dev/dashboard](https://e2b.dev/dashboard).
2. Add to `.env`:

   ```
   SANDBOX_ENABLED=true
   SANDBOX_API_KEY=e2b_...
   CHAT_LLM_PROVIDER=anthropic        # or any real provider; mock does not work
   CHAT_LLM_MODEL=anthropic/claude-haiku-4-5-20251001
   ```

3. Restart with `make down && make up`.
4. Open the chat UI at `http://localhost:3000/app/chat` and ask the assistant to run some Python code.

The `sandbox__delegate` tool does not appear in the MCP tool listing; verify it is active by asking the assistant: *"Can you run a Python script that prints the first 10 Fibonacci numbers?"*

Note that `CHAT_LLM_PROVIDER=mock` echoes input and cannot invoke tools — a real LLM provider is required for sandbox delegation to work end-to-end.
