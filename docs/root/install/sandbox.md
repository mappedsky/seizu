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
                              + the bound Seizu tools + find_seizu_tools / call_seizu_tool
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
`/tmp/seizu_results/` and replaced by a receipt: the path, byte size, row count,
column names, and two sample rows, plus a note that the full data is in the
file. The agent then processes it with `run_python`.

This matters because the sandbox exists to handle data *as data*. Without the
file path, the only route from a query to `run_python` runs through the model:
it has to read every row out of its own context and re-emit them as a Python
literal, so the data crosses the model twice and it hand-serializes in between.
Results also stop being silently capped at `CHAT_TOOL_RESULT_MAX_ROWS`, which
protects a context window; the sub-agent fetches to `SANDBOX_FILE_RESULT_MAX_ROWS`
and `SANDBOX_FILE_RESULT_MAX_BYTES` instead, and either bound decides whether what
comes back is returned or filed. Both are needed: because the fetch deliberately
exceeds both, the row cap is the only thing keeping an inline result small in row
terms, and triggering on bytes alone let multi-thousand-row results return in
full — measured at 880 inner calls and a 581-character answer, against 124 and a
complete one.

**Routing is decided by size, never by the agent.** An earlier version exposed a
`save_to_path` argument and let the agent choose. Given the choice it wrote
every result to a file — including schema lookups it needed to read — read none
of them back, and re-ran the queries instead, at over four times the sandbox
token spend. Because the trigger is size, a file now appears only where the
alternative was a truncated result, so the file is strictly more than the agent
would otherwise have received, and where a result fits nothing changes at all.

If the write fails, the truncated result is returned instead — exactly what
would have happened without this path.

**One sandbox per conversation, not per delegation.** A sandbox is opened lazily
on a turn's first `sandbox__delegate` call and shared by every delegation and
every step of that turn, so files written by one are still there for the next.
Each delegation used to open and destroy its own, which meant a result file —
and the receipt pointing at it — was gone before anything could read it, and a
turn making 31–79 delegations paid that many sandbox creations.

At the end of the turn the sandbox is **suspended rather than destroyed**, and
the next turn of the same thread resumes it by id. That is what makes a
follow-up question cheap: the data the previous turn fetched is still on disk,
and the session memory below tells the next turn what is there, so it reads
files instead of re-running the queries that produced them. A turn that ends in
an error keeps the sandbox when the thread already knows its id, and destroys it
only when that turn created it: a resumed sandbox's id is already in the
checkpoint, so failing changes nothing about finding it again, while destroying
it would cost a long conversation everything earlier turns put on disk. A
sandbox the failing turn created has its id nowhere, so pausing it would strand
it. Deleting the chat thread destroys the sandbox with it, and never fails the
deletion if the provider call fails; an orphan is logged with its id.

`SANDBOX_SESSION_PERSIST` is **off by default**, because nothing reaps an
abandoned sandbox. Cleanup happens when a thread is deleted; a conversation a
user simply stops replying to leaves a suspended sandbox until the provider's
own retention reclaims it, and a deployment with many chat users accumulates
those indefinitely. Turn it on once you have a TTL or a sweep over the
provider's sandbox list — or if you accept that cost knowingly.

The other trade is explicit too: persistence means untrusted *data* lives for the
length of a conversation rather than a single turn, still bounded to one user's
thread and still holding no credentials. Suspension keeps only the filesystem
(`pause(keep_memory=False)`), so untrusted processes do not survive the turn.

### Session memory

Sub-agents get a **recall block** naming what earlier sub-agents in the same
step found, what earlier *turns* of the conversation established, and the files
already saved in the shared sandbox. The top-level agent — the planner, the
worker, or the single-agent loop — gets a shorter **digest** of the same
material, because it is the one that decides whether to delegate at all: told
only afterwards, it has already planned the re-fetch.

A file is only advertised for the sandbox it was written in. If a resume fails
and the turn gets a replacement sandbox, every earlier receipt silently stops
being offered, which is the correct answer rather than a special case — sending
a sub-agent to read a file that is not there is worse than saying nothing. Both
blocks are fenced as untrusted data: they carry what graph and tool output said,
so they can carry text shaped like an instruction with it.

Bounds are `CHAT_SESSION_MEMORY_MAX_ENTRIES`,
`CHAT_SESSION_MEMORY_MAX_RECEIPTS` and `CHAT_SESSION_MEMORY_DIGEST_MAX_CHARS`;
the memory rides in the thread's LangGraph checkpoint, which is already
namespaced per user.

The agent reads such files with **`preview_file`**, which returns files at or
under `SANDBOX_PREVIEW_MAX_BYTES` whole and, above that, only shape plus the
beginning. That keeps a file written to stay out of context from being pulled
straight back into it; `run_python` is how the full contents get used. This is a
design choice rather than a measured improvement — a four-sample comparison found
no difference against `read_file` — so set `SANDBOX_PREVIEW_MAX_BYTES=0` if you
prefer whole-file reads.

`read_file` is deliberately not a way around this. Asked for a file larger than
`SANDBOX_MAX_OUTPUT_BYTES` it returns the beginning together with the file's real
size and an explicit statement that this is not the whole file, rather than a
bare `[truncated]` marker that an agent can read past — reading a 500KB result
file should not quietly yield a tenth of it.

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
(`graph__query`, `graph__schema`, `graph__validate_query`, `graph__explain`),
whatever the conversation has already disclosed, and whatever the delegating
call named in `tools`. Naming `tools` narrows as well as widens: a caller that
says what the task needs gets that plus the core, not the disclosed set on top.
Anything else the user may reach is still reachable — `find_seizu_tools` and
`call_seizu_tool` search it and run it, with the same result bounds and
oversized-result handling as a bound tool — so this costs a round trip, never a
capability.

It used to be handed every chat-safe tool in the deployment. In a measured
instance that was 58 tools and ~3,800 tokens of schema re-sent on *every* inner
LLM call — about a fifth of a delegation's token spend, two thirds of it
management CRUD (roles, spaces, report and toolset editing) that a
data-processing sub-agent never uses. The bound set is now ~830–1,100 tokens.
RBAC is unchanged either way: `chat_safe_only` plus the confirmation-gate
exclusion decide what is reachable at all, and nothing here widens that.

On the two-turn harness benchmark, four samples before and after (a before/after
across builds rather than a within-run A/B, and bundling the cross-turn session
memory and the disclosure fixes alongside the narrowing): median sandbox tokens
181,021 → 48,039 with non-overlapping ranges, median total 202,562 → 80,554,
median answer 1,233 → 2,339 characters, runs exhausting the token budget 4/4 →
0/4, and failed plan steps 4/5 → 0/8. No sample needed `find_seizu_tools` at
all — the core covered every delegation — so the round-trip cost of narrowing
was, on this benchmark, never paid.

Sandbox delegation requires the `sandbox:delegate` permission, which is granted to `seizu-editor` and above.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SANDBOX_ENABLED` | `false` | Master switch. Set `true` to allow the chat agent to use the `sandbox__delegate` tool. |
| `SANDBOX_API_KEY` | `""` | API key for the sandbox provider. Required for E2B cloud; leave empty for self-hosted deployments that use internal auth. |
| `SANDBOX_DOMAIN` | `""` | Sandbox service hostname. Empty → E2B cloud (`e2b.app`). For self-hosted deployments (e.g. OpenKruise Agents): set to your cluster ingress hostname. The E2B SDK constructs `https://api.<domain>` as the API base URL. |
| `SANDBOX_ALLOW_INTERNET` | `false` | Allow sandboxes to make outbound internet connections. Off by default for a hardened posture; enable only when a task legitimately needs network access. |
| `SANDBOX_TIMEOUT_SECONDS` | `120` | Maximum wall-clock time for one sandbox task. If exceeded, the tool returns an error and the sandbox is destroyed. |
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
