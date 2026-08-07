# Sandbox decisions (`SBX`)

Decisions behind `sandbox__delegate` and the sandbox session lifecycle. For how
to configure and operate the sandbox, see the
[sandbox install docs](../../install/sandbox.md).

Primary code: `reporting/services/mcp_builtins/sandbox.py`,
`reporting/services/sandbox_session.py`, `reporting/services/sandbox_backend.py`,
`reporting/services/episodic_memory.py`.

## SBX-001 — The backend is a five-operation protocol, not an E2B client

**Applies to:** `reporting/services/sandbox_backend.py`

`SandboxBackend` defines `run_python`, `run_bash`, `read_file`, `write_file`,
`list_files`, plus `sandbox_id`. `_E2BSandboxBackend` is the only
implementation today and is the only place that knows E2B response shapes.
`_open_backend` is the single swap point for another provider.

**Why:** fixed tool names and descriptions mean the inner agent behaves the same
across providers, and tests need no E2B package.

**Don't:** widen the protocol to expose provider features. A sixth operation is
a sixth thing every future backend must implement.

**Note:** `@runtime_checkable` `isinstance` reads members with `getattr_static`,
so a `MagicMock` standing in for a backend must have **every** member explicitly
assigned — an auto-created attribute does not satisfy the check.

## SBX-002 — Oversized tool results are routed to a file by size, never by the model's choice

**Applies to:** `_invoke` in `reporting/services/mcp_builtins/sandbox.py`

When a tool result exceeds the byte or row bound, it is written into the sandbox
filesystem and the agent receives a receipt (`status: too_large_to_return`,
path, row count, columns, sample) instead.

**Why:** an earlier version exposed `save_to_path` as an argument and let the
agent decide. It then used it for all 233 calls of a measured run — including
schema lookups it needed to read — read none of the files back, and re-queried
instead, at 4.4x the sandbox spend. Routing on size removes the decision: a file
appears only where the alternative was a truncated result, so reading it is
strictly better than what the agent would otherwise have had, and where a result
fits, nothing changes.

Triggering on bytes alone was also tried and was worse. With the fetch raised to
the file bounds, the row cap is the only thing keeping an inline result small in
row terms; dropping it let multi-thousand-row results return in full. A measured
turn went from 124 inner calls and a complete answer to 880 calls, 791 of them
queries, and a 581-character answer with both steps failing.

**Don't:** re-expose the path as a tool argument, and don't drop the row bound
from the oversize test.

## SBX-003 — The sub-agent is bound a narrow tool set, not the catalogue

**Applies to:** `_bound_tool_names` in `reporting/services/mcp_builtins/sandbox.py`

Bound = the read-only graph core (`_CORE_TOOL_NAMES`) + what the conversation
disclosed (`chat_graph.current_disclosed_tools()`) + what the delegating call
named in `tools`. Naming `tools` **narrows** as well as widens: a caller that
knows the task gets that plus the core, not the disclosed set on top.

**Why:** the inner agent used to be handed every chat-safe tool — 58 in a
measured deployment, ~3,800 tokens of schema re-sent on *every* inner LLM call,
about a fifth of a delegation's spend, two thirds of it management CRUD a
data-processing sub-agent never uses. The bound set is now ~830–1,100 tokens. It
also leaked: a tool name the sub-agent saw reached the session memory the
planner reads, which then required a tool the worker had not disclosed, and the
step was refused.

Harness, 4 samples before/after (cross-build, bundling session memory and the
disclosure fixes): median sandbox tokens 181k → 48k with non-overlapping ranges,
budget exhaustion 4/4 → 0/4, failed steps 4/5 → 0/8, median answer 1,233 → 2,339
characters.

**Don't:** treat this as an authorization boundary — see [AGT-002](chat-agent.md).
RBAC (`chat_safe_only` + `exclude_confirmation_gated`) bounds what `_invoke` can
reach, and that is unchanged by any of the above.

## SBX-004 — Discovery follows `CHAT_LLM_PROGRESSIVE_DISCLOSURE`

**Applies to:** `_discovery_tools`, `_skill_discovery_tools` in `sandbox.py`

How the sub-agent reaches tools outside its bound set is decided by the same
setting that governs the planner:

| Setting | Discovery tools | `call_seizu_tool` may run |
|---|---|---|
| `false` | `find_seizu_tools`, `call_seizu_tool` | anything reachable |
| `true` (default) | `find_seizu_skills`, `load_seizu_skill`, `call_seizu_tool` | only what a loaded skill declared |

**Why:** free-text tool search reaches anything RBAC permits by string match,
which is the disclosure model the setting exists to switch off. An inner agent
that browses the catalogue while the outer one is kept to skills is two
disclosure models in one system, and the mismatch is how an undisclosed tool
name reached session memory (see SBX-003). A skill's `tools_required` is its
author naming the tools for a workflow, and the instructions arrive with them.

All routes share the same `_invoke`, so whatever is reached gets identical
result bounds, oversized-result handling and receipts.

**What narrowing costs differs by mode, and the two should not be conflated:**

- **Disclosure off** — a round trip, not a capability. Everything RBAC permits
  is still reachable through `find_seizu_tools`.
- **Disclosure on** — a round trip *and*, where skill coverage is incomplete, a
  capability. A tool no skill declares cannot be reached by the sub-agent on its
  own initiative. That is the deliberate trade, not an oversight.

On one measured deployment, skills reach 30 of the 58 tools a sub-agent could
otherwise browse. Of the 28 lost, roughly twenty are management CRUD (roles,
spaces, scheduled queries, workflows, toolsets) that a data sub-agent has no
reason to touch; the rest are answerable with `graph__query`. Where no skill
covers the need, the delegating model naming the tool in `tools` is the intended
route — the same limitation the planner has. So the loss is bounded by how the
deployment's skills are authored, and is recoverable by the caller, but it is a
real loss rather than a deferred cost.

**Don't:** add a free-text tool search back under progressive disclosure, and
don't bind discovery tools when no skills exist — two tools that can never find
anything are pure schema cost.

**Note:** a skill may declare no tools at all (1 of 10 on a measured
deployment). `load_seizu_skill` says so explicitly, because otherwise the
sub-agent discovers it one failed call at a time.

## SBX-005 — One sandbox per conversation, suspended between turns

**Applies to:** `reporting/services/sandbox_session.py`

A `SandboxSession` is made ambient by `chat_agent_node` (single-agent path) and
`dispatcher_node` (orchestrated path), **turn-level, not step-level**. It opens
lazily on the first delegation, then suspends (E2B `pause()`) and resumes next
turn via `resume_sandbox_id`, with the id carried in `ChatState.sandbox_id`.

**Why turn-level:** `asyncio.gather` copies the context but not the object, so
every step of a batch shares one sandbox while keeping its own `EpisodeLog`.
Per-step sessions meant parallel sub-agents could not see each other's files.

**Why suspend rather than destroy:** data fetched in one turn disappeared with
the sandbox, so the next turn re-fetched it. Pausing keeps only the filesystem
(`keep_memory=False`) — untrusted *processes* do not outlive a turn, but their
output does.

**Don't:** assume retention is solved. Nothing reaps a sandbox whose thread is
abandoned rather than deleted; `SANDBOX_SESSION_TIMEOUT_SECONDS` bounds a
*running* sandbox, not a paused one. A TTL/sweep is planned separately.

## SBX-006 — Only terminal resume failures create a replacement sandbox

**Applies to:** `open_backend`, `_terminal_resume_errors` in `sandbox_backend.py`

A failed resume creates a fresh sandbox **only** for terminal errors
(`SandboxNotFoundException`, `NotFoundException`). A timeout, rate limit or auth
failure propagates.

**Why:** replacing on a transient error leaves the old sandbox paused and alive
while its id is overwritten — paid for and unreachable. The differing
`sandbox_id` is how callers learn the old files are gone, so it must mean the
old sandbox is actually gone.

**Related:** `sandbox_id` is written to state **whenever a sandbox was opened**,
including the empty string. The state reducer overwrites rather than merges, so
omitting the key does not clear it: a killed sandbox stayed in the checkpoint,
later turns retried a dead resume, and — worse — the session digest advertised
receipts under an id that no longer existed. `SandboxTeardown(opened,
suspended_id)` exists to make that distinction expressible; a turn that opened
nothing must leave the stored id alone.

Testing note: the tests that existed while this bug was live asserted the close
call returned `None`, which was *already true*. The return value was right and
the state was wrong — assert on persisted checkpoint state, not on the return.

## SBX-007 — Error paths abandon the session; they do not destroy it

**Applies to:** `abandon_sandbox_session()` in `sandbox_session.py`

On a failed turn, a **resumed** sandbox is kept and a freshly **created** one is
destroyed.

**Why:** a resumed sandbox's id is already in the checkpoint from an earlier
successful turn, so failing changes nothing about finding it again — and
destroying it would cost a long conversation every turn's accumulated work over
one broken turn. A sandbox created by the turn that raised has its id nowhere,
so pausing it would strand it.

**Don't:** collapse this into a single "always pause" or "always kill" path.

## SBX-008 — Session memory carries receipts, scoped to their own sandbox

**Applies to:** `reporting/services/episodic_memory.py`

`EpisodeLog` is per-step; `SessionLedger` is the cross-turn carry, holding
episodes plus **receipts** — files an oversized result was written to, recorded
with the delegated task as their purpose. Receipts render **only for their own
`sandbox_id`**.

**Why:** without a record of the file, the next turn re-runs the query that
produced it, which is the cost SBX-002 exists to avoid. Scoping to the sandbox
id means a replacement sandbox silently stops advertising files that no longer
exist.

Sub-agents get `EpisodeLog.recall(sandbox_id=...)` built *after* the backend
resolves; the top-level agent gets the shorter `session_digest()`, because it is
the model that decides whether to delegate at all. Both are fenced as untrusted.

## SBX-009 — Isolation is the safety mechanism, so there is no confirmation gate

**Applies to:** the `sandbox__delegate` registration in `sandbox.py`

`chat_safe_without_confirmation=True` and `chat_only=True`.

**Why:** the sandbox is network-isolated from Seizu's data stores and holds no
credentials, so there is nothing for a user to approve. `chat_only` keeps it off
the MCP server endpoint entirely. Confirmation-gated mutating tools are excluded
from the sub-agent's reach: it runs to completion inside one outer tool call and
cannot drive the interactive confirmation round-trip, so those stay with the
outer agent where the user can approve them.

Persistence (SBX-005) widens the blast radius deliberately: untrusted code's
*output* lives for a conversation rather than a turn, bounded to one user's
thread, since the resume id lives in that thread's already user-namespaced
checkpoint.

**Don't:** grant the sub-agent a confirmation-gated tool on the grounds that the
runtime would catch it. It fails closed there as a backstop, not as the control.
