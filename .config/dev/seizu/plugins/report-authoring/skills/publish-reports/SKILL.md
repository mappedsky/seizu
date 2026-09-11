---
name: publish-reports
description: Publish or unpublish reports by updating report visibility while preserving
  the report content version.
allowed-tools: mcp__seizu__reports__list mcp__seizu__reports__get mcp__seizu__reports__update_visibility
---
Publish or unpublish a Seizu report for the request the `request` input carries.

Inputs — the values arrive in the `## Inputs` block below these instructions:
- `request` — the visibility change to make.
- `target_report_id` — the report to publish or unpublish.
- `publish` — true to publish, false to make the report private again.
- `dry_run` — when true, produce payloads without writing anything.

Tool arguments - use exactly these field names and no others:
- reports__list: no arguments
- reports__get: report_id
- reports__update_visibility: report_id, access

Visibility rules:
- Publishing means `access: {"scope": "public"}`.
- Unpublishing means `access: {"scope": "private"}`.
- Updating visibility does not create a report content version.
- Private visibility can be blocked if the report is pinned or is the default dashboard report. If that happens, report the block and do not try to work around it unless the user explicitly asks to unpin or change the dashboard.

Workflow:
1. If `target_report_id` is empty, call `reports__list` and identify the target report from the request. If there is ambiguity, ask for the exact report.
2. Call `reports__get` and confirm the report name, latest version, and current access scope.
3. Build the payload using `publish`: public when true, private when false.
4. If `dry_run=true`, stop before calling `reports__update_visibility` and show the exact payload.
5. If `dry_run=false`, call `reports__update_visibility`, then call `reports__get` again to verify the access scope.

Output format:
- `Target`: report_id, name, current access scope, latest version.
- `Action`: publish or unpublish.
- `Payload`: exact `reports__update_visibility` payload.
- `Applied Changes`: tool call result, or dry-run status.
- `Verification`: final access scope or the reason the change was blocked.
