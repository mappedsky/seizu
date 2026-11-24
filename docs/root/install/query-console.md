# Query Console

The Query Console is an interactive Cypher editor accessible from the sidebar at `/app/query-console`.
It is designed for ad-hoc exploration of the Neo4j graph without requiring a saved report, and should be treated as a trusted-operator feature rather than a general viewer capability.

Only users with `query:execute` should have access to the console. For read-only consumers, prefer report panels with backend-signed report-query tokens.

## Layout

The console is split into three areas:

- **Side panel** (left, collapsible) — shows the database schema or query history; toggle between them with the icons in the panel header.
- **Result panel** (top right) — displays query results in Graph, Table, or Raw format.
- **Query editor** (bottom right) — a multi-line Cypher editor with a Run button.

Press **Ctrl+Enter** (or **Cmd+Enter** on macOS) to run the query without reaching for the mouse.

## Asking in Plain Language

When the chat assistant is enabled, the query box has a **Cypher | Ask** switch below the editor. In Ask mode, type a question such as *"Which S3 buckets are publicly readable?"* and press **Enter** (**Shift+Enter** for a new line) to generate a Cypher query for it. **Escape** in an empty question returns to Cypher.

Generation is a single model call, not a chat conversation: nothing appears in your chat sessions. The model is given the graph's labels, relationship types and property keys, and the query it returns goes through the same validator as any ad-hoc query. If the validator rejects the query or flags labels, relationships or properties that do not exist, the model gets one chance to correct it.

While it works, the query box shows a progress line and the elapsed time. **Cancel** (or **Escape**) stops waiting and leaves your question in place.

- **A query that validates** is placed in the editor, saved to your query history together with the question, and run. The question sits above the query in the same box, with a short explanation of what the query matches and the model and reasoning level that wrote it.
- **A query that does not validate** is placed in the editor with the validator's errors, but not run. Edit it and run it yourself.

**Refine** (or switching back to Ask) asks a follow-up about the query in the editor: it is sent along as context, so *"only the ones in production"* changes it rather than starting over. The question it came from stays in view while you type the next one. Running a query you have edited by hand drops the question, since it no longer describes what is running.

### Choosing the model

Each [model profile](chat.md#model-profiles) has its own **Query console** settings, separate from chat's:

- **Offer in the query console** decides whether Ask mode may run on the profile. When profiles exist but none is offered, Ask mode is hidden.
- **Model ID** is the model generation uses; empty runs the profile's primary model.
- **Reasoning** lists the levels the console offers for the profile, with one starred as the level questions start on. New profiles offer `low` only.

In Ask mode, the picker next to the switch lists every offered profile and level, and the console remembers your choice. When there is only one, one profile at one level, there is nothing to choose and the picker is not shown. The console starts on the default profile when it is offered, and otherwise on the first one offered. Generation answers in one call under a time limit, so a fast model at low reasoning usually serves best.

With no model profiles configured there is no picker, and `QUERY_GENERATION_MODEL` (default: `CHAT_LLM_MODEL`) and `QUERY_GENERATION_REASONING_EFFORT` (default `low`) apply. `QUERY_GENERATION_TIMEOUT_SECONDS` (default 50) bounds a generation, the correction call included; keep it under `API_REQUEST_TIMEOUT`.

Generated queries are marked in the history tab with a sparkle icon and the question they were generated from. Opening one shows the question again above the query.

Asking requires both `query:execute` and `chat:use`, and is controlled by `QUERY_GENERATION_ENABLED` (default `true`; it has no effect unless `CHAT_ENABLED` is also on).

## Side Panel

The collapsible left panel has two tabs, switched via icons in the panel header:

- **Database schema** (tree icon) — queries Neo4j on load to discover the current schema.
- **Query history** (clock icon) — shows your past queries from most recent to oldest.

When collapsed, both icons are visible as stacked buttons. Clicking either icon opens the panel on that tab. Clicking the active icon again closes the panel.

### Database Schema

| Section | Source | Click action |
|---------|--------|--------------|
| **NODES** | `CALL db.labels()` | Runs a path query for that label: `MATCH path = (n:\`Label\`)-[r]-(m) RETURN path LIMIT 25` |
| **RELATIONSHIPS** | `CALL db.relationshipTypes()` | Runs a path query for that type: `MATCH path = (a)-[r:\`TYPE\`]->(b) RETURN path LIMIT 25` |
| **PROPERTY KEYS** | `CALL db.propertyKeys()` | Runs a node query for that property: `MATCH (n) WHERE n.\`key\` IS NOT NULL RETURN n LIMIT 25` |

Clicking any item inserts the generated query into the editor and runs it immediately.
Node labels are colour-coded using the same palette as the graph panel, so colours are consistent across the UI.

### Query History

The history tab shows the last 500 ad-hoc queries you have run from the Query Console. Queries are shown newest first, 20 per page. Hovering over a row shows the full query in a tooltip.

Clicking a history item loads the query into the editor and re-runs that saved history item. To modify it, edit the query text after it loads and click **Run**.

History is private to your account — you cannot see queries run by other users.

## Result Tabs

Results are shown in up to three tabs depending on what the query returns:

| Tab | When shown | Description |
|-----|-----------|-------------|
| **Graph** | Only when the query returns graph-compatible data | Interactive node-link diagram. Clicking a node or relationship opens the detail panel. |
| **Table** | Always | Tabular view. Columns are auto-detected from the result shape — no specific return alias required. |
| **Raw** | Always | Full JSON response from the API. |

The console defaults to the Graph tab when the data supports it, otherwise the Table tab.

## Graph View

![graph view in the query console showing a node-link diagram with schema panel and Cypher editor](/images/graph-view.png)

The graph view is the same panel used in reports. It supports:

- **`RETURN path`** queries — Neo4j path objects are automatically unpacked into nodes and relationships.
- **Explicit map format** — `RETURN {nodes: [...], links: [...]}` with any alias (or none).

When a node or relationship is clicked, a detail panel slides in from the right showing its properties.
Clicking the canvas background deselects the item and shows a graph summary (node counts by label, relationship counts by type).

## Table View

![table view in the query console showing CVE path results with schema panel and Cypher editor](/images/console-table-view.png)

The table view accepts any query return shape:

- `RETURN n.name AS name, n.org AS org` — named columns used directly.
- `RETURN n` — Neo4j node objects are unwrapped; properties become columns.
- `RETURN {key: value, ...}` — map values are unwrapped; keys become columns.

Nested objects (such as node or relationship values inside a path) are rendered in a human-readable format:
- Nodes → `(Label) name`
- Relationships → `[TYPE]`
- Paths → `(StartLabel) name → (EndLabel) name`

## Raw View

![raw view in the query console showing full JSON path objects](/images/console-raw-view.png)

The raw view shows the complete JSON response from the API. It is useful for inspecting the exact structure returned by a query — particularly for path objects, node properties, or debugging unexpected results.
