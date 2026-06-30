# Sandbox Delegation

## Purpose

The `sandbox__delegate` chat tool lets the chat agent hand off tasks requiring code execution or file operations to an isolated sandbox. The agent can run Python, execute shell commands, and read/write files — then returns a summary of what happened. Use it when a task involves data processing, scripting, or file manipulation that cannot be expressed as a Cypher query.

Sandbox delegation is **chat-only**: the tool never appears in the MCP server's tool listing and cannot be called by external MCP clients. Each invocation creates a fresh, ephemeral sandbox that is destroyed after the task completes; the sandbox has no access to Seizu's internal services or credentials.

## Architecture

```
Seizu chat agent
  → sandbox__delegate(task="...", context="...")
    → _open_backend()        # provider-specific lifecycle (create + destroy)
      → SandboxBackend       # stable five-operation interface
        → create_react_agent with run_python / run_bash / read_file / write_file / list_files
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
| `SANDBOX_LLM_MODEL` | `""` | LiteLLM model ID for the inner sandbox agent. Empty → inherits `CHAT_LLM_MODEL`. Set a separate model when you want the sandbox subagent to use a cheaper or faster model than the outer chat agent. |

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
