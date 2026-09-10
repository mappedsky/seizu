---
name: create-update-reports
description: Create new reports or update existing report configs, with an optional
  clone-review-apply-cleanup workflow for testing changes before touching the original
  report.
allowed-tools: mcp__seizu__reports__list mcp__seizu__reports__get mcp__seizu__reports__create
  mcp__seizu__reports__create_version mcp__seizu__reports__clone mcp__seizu__reports__delete
  mcp__seizu__reports__list_versions mcp__seizu__graph__schema mcp__seizu__graph__validate_query
  mcp__seizu__graph__explain
---
Help create or update a Seizu report for this request:

Inputs — the values arrive in the `## Inputs` block below these instructions: They are inputs to THIS skill, not arguments to any tool.
- `request` — the reporting change to design or apply.
- `target_report_id` — the existing report to modify, if any.
- `report_name` — the name for a new report, or the new name for an existing one.
- `use_review_clone` — whether to stage the change on a clone for review.
- `review_clone_name` — the name to give that clone.
- `delete_review_clone_after_apply` — whether to remove the clone once the change lands.
- `dry_run` — when true, produce payloads without writing anything.

Tool arguments - use exactly these field names and no others:
- reports__list: no arguments
- reports__get: report_id
- reports__create: name
- reports__create_version: report_id, config, comment
- reports__clone: report_id, name
- reports__delete: report_id
- reports__list_versions: report_id
- graph__schema: no arguments
- graph__validate_query: query
- graph__explain: query

Report config rules:
- `reports__create_version` replaces the full latest config for the target report. Always call `reports__get` first for an existing report and send a complete merged config, preserving every field not intentionally changed.
- A valid report config has `schema_version`, `name`, `queries`, optional `inputs`, and `rows`. `queries` is an object mapping query names to Cypher strings, not a list.
- Panels must live under `rows[].panels`; never create a top-level `panels` field.
- Markdown panel content must be in the `markdown` field, exactly this shape: `{"type": "markdown", "w": 12, "markdown": "## Findings..."}`. Do not use `content` or `config` for markdown text.
- Keep panel `cypher` fields aligned with `config.queries`: use a query key when referring to a named query, or a literal Cypher query only when that is deliberate.
- Preserve report `name` unless the user explicitly asks to rename it; if renamed, update both `config.name` and the report version comment so the change is clear.

Cypher checks for report queries:
1. Call `graph__schema` once before adding or changing report Cypher.
2. Call `graph__validate_query` with each new or changed query. If `valid` is false, fix and re-validate before saving.
3. Call `graph__explain` for material report queries and inspect the plan. Prefer indexed label/property matches where available; surface unavoidable large scans as a risk.
4. Keep query parameters and report inputs/panel params in sync. Every panel parameter should either have a literal `value` or an `input_id` that exists in `inputs`.

Operating rules:
- If `dry_run=true`, do not call `reports__create`, `reports__create_version`, `reports__clone`, or `reports__delete`; produce exact proposed payloads and validation results only.
- If creating a report, call `reports__create` first, then `reports__create_version` with the complete initial config.
- If updating an existing report and `use_review_clone=false`, call `reports__get`, build the complete merged config, validate changed Cypher, then call `reports__create_version` on `target_report_id`.
- If updating an existing report and `use_review_clone=true`, use the staged workflow below. This is the default because it lets users test a rendered report before the original is changed.

Staged clone-review-apply-cleanup workflow:
1. Call `reports__get` for `target_report_id` and keep the source config as the baseline.
2. Create a temporary private review copy. If `review_clone_name` is non-empty, pass it as `name` to `reports__clone`; otherwise derive a name from the original report name plus ` Review`.
3. Call `reports__get` for the new clone, build the complete merged config on top of the clone's latest config, and call `reports__create_version` on the clone with a comment like `Review copy for updating <original report_id>`.
4. Tell the user the clone report_id and what to review. If the user has not explicitly asked you to apply after review in the same turn, stop after creating or describing the clone.
5. After the user approves the reviewed clone, call `reports__get` for the clone and use the clone's latest `config` as the complete payload for `reports__create_version` on the original `target_report_id`.
6. Only after the original update succeeds, if `delete_review_clone_after_apply=true`, call `reports__delete` for the clone report_id. Never delete the clone if the original update failed or was not approved.

Workflow:
1. Call `reports__list` unless `target_report_id` is already known and the request does not require name lookup.
2. Decide whether the request is create, direct update, staged update, or apply reviewed clone to original.
3. Call `reports__get` for every report whose config you will read or write.
4. Validate any changed Cypher with the graph tools.
5. Apply writes only when `dry_run=false` and the user requested an apply path. Writes are confirmation-gated, so still expect approvals.
6. After writing, call `reports__get` on the affected report and confirm the latest version/config matches the intended result.

Output format:
- `Intent`: create, direct update, staged update, or apply reviewed clone.
- `Current State`: report IDs, names, latest versions, access scope, and relevant existing config.
- `Plan`: ordered steps and whether a review clone will be used.
- `Validation`: report schema and Cypher checks performed.
- `Payloads`: exact `reports__create`, `reports__create_version`, `reports__clone`, or `reports__delete` payloads you used or would use.
- `Applied Changes`: tool calls made and resulting report IDs/versions.
- `Review Instructions`: for staged updates, identify the clone and what the user should inspect before approving the original update.
- `Risks and Follow-up`: permission limits, unvalidated data, performance risks, or cleanup still required.
