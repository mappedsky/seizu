# Query validation decisions (`QV`)

Decisions behind the Cypher validator. For the flow itself, the allowlist, and
the test workflow, see [query validation](../query-validation.md).

Primary code: `reporting/services/query_validator.py`,
`tests/unit/reporting/services/query_validator_test.py`,
`tests/data/query-fuzzing.csv`.

## QV-001 — Errors block, warnings never do

**Applies to:** `validate_query` → `ValidationResult(errors, warnings)`

Layers 1 and 2 (the `EXPLAIN` read-only check and the SSRF/admin/procedure
guard) produce errors. Layers 3 and 4 (CyVer `SchemaValidator` and
`PropertiesValidator`, both run via `asyncio.to_thread`) produce warnings only.

**Why:** CyVer reasons about a schema snapshot. Blocking on it would reject
valid queries whenever the graph is mid-sync, and the failure would look like a
validator bug rather than a stale schema.

## QV-002 — Procedures are allowlisted, not denylisted

**Applies to:** `_DEFAULT_ALLOWED_PROCEDURES`, `QUERY_VALIDATOR_ALLOWED_PROCEDURES`

Every `CALL <procedure>` is matched against an allowlist of side-effect-free
schema procedures. Extend it with exact names or namespace prefixes (`apoc.`).

**Why:** the set of dangerous procedures is open-ended and grows with every
plugin. The set of procedures a reporting tool needs is small and known.

**Note:** a namespace prefix in `QUERY_VALIDATOR_ALLOWED_PROCEDURES` also drops
that namespace's dangerous-function guard. Granting `apoc.` is therefore a
larger decision than it looks.

## QV-003 — The guard scans three forms of the query, and comments are checked two-sided

**Applies to:** `_scan_for_dangerous_constructs`

The original text, the comment-stripped form, and the unicode-decoded form are
all scanned for `LOAD CSV`, the `USE` clause, admin/catalog commands, and the
`apoc.cypher.*`/`gds.*`/`ai.*`/`genai.*` function namespaces.

**Why two-sided on comments:** scanning the comment-stripped form catches
`CALL /* x */ apoc.`; scanning the original catches `//` inside a string literal
(a URL, say) where the stripper would hide a following `CALL apoc.`. Either
scan alone has a bypass.

## QV-004 — Every attack vector tried is recorded, including the ones that were already blocked

**Applies to:** `tests/data/query-fuzzing.csv`

One row per technique (`Technique, Cypher, Result, Blocked_By, Notes`), with
matching assertions in `query_validator_test.py`.

**Why:** the corpus is the record of what has been *tried*, not just what
failed. A vector that is already blocked still belongs in it, because the next
person to consider that technique should find it answered rather than re-derive
it.

**Do, when adding a case:** confirm whether Neo4j actually executes the vector
(logs: `ExternalResourceFailed`, or write side effects) → add the CSV row → add
the unit test → if it is *not* blocked, fix `query_validator.py` first and then
prove it with the test.

## QV-005 — The generic MCP query reuses validation's plan and rejects risky scans

**Applies to:** `ValidationResult.plan`, `mcp_builtins.graph::_handle_query`,
`MCP_GRAPH_QUERY_REJECT_UNINDEXED`,
`MCP_GRAPH_QUERY_UNINDEXED_MAX_ESTIMATED_ROWS`

The validation `EXPLAIN` is the one planning pass: its result is retained on
`ValidationResult`, returned directly by `graph__explain`, and inspected before
`graph__query` executes. The generic MCP query is refused when Neo4j emits a
performance notification, or when its plan combines a non-index scan with an
operator cardinality above the configured threshold. Rejections carry the full
plan and the scan/cardinality summary. REST queries and authored toolset tools
keep their existing behavior.

**Why not reject every scan:** a label scan bounded to ten rows and a targeted
five-hop path estimated at 2,967 rows both completed locally. The failed
internet-reachability shape used the same broad five-hop expansion after a
label scan, but its plan estimated 265,885 rows and it repeatedly reached the
30-second transaction timeout. A threshold distinguishes those observed shapes
without pretending that the word `Scan` alone predicts cost.

**Why notifications are insufficient:** the current Neo4j planner emitted no
performance notification for the timed-out shape, an all-node scan, a
function-wrapped indexed predicate, bounded or unbounded variable paths, or a
Cartesian product in the development graph. They remain a useful signal when
present, but making them the only signal would admit the failure this guard is
for.

**Why the plan is retained:** `graph__query` already paid for `EXPLAIN` to
enforce read-only execution. Calling `graph__explain` first previously planned
twice inside that tool and a third time during `graph__query`, while adding
another model/tool round trip. Keeping the first result makes the guard and its
diagnostic response no additional database call.
