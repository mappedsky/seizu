---
name: pin-reports
description: Pin or unpin reports in the dashboard navigation after checking the target
  report.
allowed-tools: mcp__seizu__reports__list mcp__seizu__reports__get mcp__seizu__reports__pin
---
Pin or unpin a Seizu report for the request the `request` input carries.

Inputs — the values arrive in the `## Inputs` block below these instructions:
- `request` — the pinning change to make.
- `target_report_id` — the report to pin or unpin.
- `pinned` — the pinned state to set.
- `dry_run` — when true, produce payloads without writing anything.

Tool arguments - use exactly these field names and no others:
- reports__list: no arguments
- reports__get: report_id
- reports__pin: report_id, pinned

Workflow:
1. If `target_report_id` is empty, call `reports__list` and identify the target report from the request. If more than one report matches, ask for the exact report.
2. Call `reports__get` and confirm the report name, latest version, access scope, and current pinned state if visible from report metadata/list output.
3. Interpret `pinned=true` as pin and `pinned=false` as unpin. Do not change report content.
4. If `dry_run=true`, stop before calling `reports__pin` and show the exact payload.
5. If `dry_run=false`, call `reports__pin` with `report_id` and `pinned`, then call `reports__list` or `reports__get` again to verify the state.

Output format:
- `Target`: report_id, name, access scope, latest version.
- `Action`: pin or unpin.
- `Applied Changes`: tool call result, or the dry-run payload.
- `Verification`: evidence that the report is now pinned or unpinned.
