# Decision log

Why the code is the way it is. Each entry records a decision that is **not
recoverable from reading the code** — usually because the obvious alternative
was tried and measured, or because a subtle failure drove the design.

This exists so the rationale lives in one place per product area instead of
being re-encoded across `AGENTS.MD`, install docs, settings comments, and
docstrings. Install docs describe *what to configure*; these describe *why it
works that way*.

## Logs by area

| Area | Log | Covers |
|------|-----|--------|
| `CTX` | [Chat context and cost](chat-context.md) | Context windows, compaction, prompt caching, token budgeting |
| `AGT` | [Chat agent](chat-agent.md) | Orchestration, progressive disclosure, tool permissions, headless runs |
| `SBX` | [Sandbox](sandbox.md) | Delegation, sandbox lifecycle, tool binding, session memory |
| `SPC` | [Spaces](spaces.md) | Membership invariants, visibility, the overview pointer |
| `STO` | [Report store](report-store.md) | DynamoDB record shape, the GSI, SQL migrations |
| `WF` | [Workflows](workflows.md) | Temporal pipelines, cartography sync, remediation isolation |
| `QV` | [Query validation](query-validation.md) | The three validator layers and the fuzzing corpus |
| `AUTH` | [Authentication](authentication.md) | Identity resolution and the OIDC configuration guards |

## Referring to a decision

Every entry has a stable ID in its heading (`SBX-004`). Cite it rather than
restating the reasoning:

- **In code**, where a line looks wrong or arbitrary without the context:
  ```python
  # Written even when empty -- see SBX-006 in docs/root/dev/decisions/sandbox.md.
  ```
- **In `AGENTS.MD`**, as a pointer to read before changing that area.
- **In install docs**, only when an operator's configuration choice depends on
  it.

## Writing an entry

Entries are append-only in spirit: **allocate the next free number in the
area** and never renumber, because IDs are cited from code. A decision that is
later reversed keeps its ID and gains a `Superseded by` line — the reversal is
itself worth recording, and a dangling citation is worse than a stale one.

Use this shape:

```markdown
## SBX-007 — Short statement of the decision, in the present tense

**Applies to:** `reporting/services/mcp_builtins/sandbox.py`

What was decided, in a sentence or two.

**Why:** the reasoning, including the alternative that was rejected and what it
cost. Numbers where they exist — "measured 181k → 48k tokens across 4 samples"
beats "much cheaper".

**Don't:** the specific change that would silently undo this.
```

Three things make an entry worth writing:

1. **The alternative is more obvious than the choice.** If a reader would
   naturally write it the other way, say why that fails.
2. **There is evidence.** Measurements, a reproduced bug, a provider behaviour.
3. **It is still load-bearing.** Archaeology about code that no longer exists
   belongs in git history.

Routine choices that the code states plainly do not need an entry.

```{toctree}
:hidden:

chat-context
chat-agent
sandbox
spaces
report-store
workflows
query-validation
authentication
```
