---
name: clone-reports
description: Clone an existing report into a new private report and verify the copied
  report config.
allowed-tools: mcp__seizu__reports__list mcp__seizu__reports__get mcp__seizu__reports__clone
---
Clone a Seizu report for the request the `request` input carries.

Inputs — the values arrive in the `## Inputs` block below these instructions:
- `request` — the clone to make and why.
- `source_report_id` — the report to clone.
- `clone_name` — the name to give the clone.
- `dry_run` — when true, produce payloads without writing anything.

Tool arguments - use exactly these field names and no others:
- reports__list: no arguments
- reports__get: report_id
- reports__clone: report_id, name

Workflow:
1. If `source_report_id` is empty, call `reports__list` and identify the source report from the request. If there is ambiguity, ask for the exact report.
2. Call `reports__get` for the source report and summarize its name, version, access scope, and major rows/panels.
3. Determine the clone name. Use `clone_name` when provided; otherwise choose a clear name based on the source report and request.
4. If `dry_run=true`, stop before calling `reports__clone` and show the exact payload.
5. If `dry_run=false`, call `reports__clone` with `report_id=source_report_id` and the chosen `name`.
6. Call `reports__get` for the clone and verify its latest config exists and carries the clone name.

Output format:
- `Source`: source report_id, name, version, access scope.
- `Clone Plan`: clone name and why it was chosen.
- `Applied Changes`: clone report_id when created, or the dry-run payload.
- `Verification`: clone config/version check.
- `Next Steps`: whether the clone is ready for editing, review, publishing, or pinning.
