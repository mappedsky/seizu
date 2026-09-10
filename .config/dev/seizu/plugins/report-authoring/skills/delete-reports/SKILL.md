---
name: delete-reports
description: Delete a Seizu report and every version of it, after confirming which
  report is meant and what the deletion would destroy. Use for any request to delete,
  remove, or discard a report, including a draft or a leftover review clone.
allowed-tools: mcp__seizu__reports__list mcp__seizu__reports__get mcp__seizu__reports__list_versions
  mcp__seizu__reports__delete
---
Delete a Seizu report for the request the `request` input carries.

Inputs — the values arrive in the `## Inputs` block below these instructions:
- `request` — the deletion to make and why.
- `target_report_id` — the report to delete, if known.
- `report_name` — the report's name, when the id is not known.
- `dry_run` — when true, produce the payload without deleting anything.

Tool arguments - use exactly these field names and no others:
- reports__list: no arguments
- reports__get: report_id
- reports__list_versions: report_id
- reports__delete: report_id

Deletion rules:
- **A delete removes the report and all of its versions, and cannot be undone.**
  There is no restore, so identify the report before calling anything.
- Delete exactly one report per request unless the user has named several. Never
  widen a request into a cleanup of everything that looks similar.
- `reports__delete` is confirmation-gated: the call returns an approval request
  rather than deleting. Say so plainly and tell the user to approve it in the
  chat's confirmations panel. That is the expected path, not a failure, and not
  a reason to look for another way to remove the report.
- A pinned report, or the one the dashboard points at, may refuse deletion.
  Report the refusal and what would have to change first; do not unpin or
  repoint the dashboard unless the user asks.

Workflow:
1. If `target_report_id` is empty, call `reports__list` and identify the report
   from `report_name` and the request. If more than one matches, or none does,
   ask for the exact report and stop.
2. Call `reports__get` and confirm the report's name, latest version and access
   scope. Say what is about to be destroyed, naming the report rather than only
   its id.
3. Call `reports__list_versions` and state how many versions go with it.
4. If `dry_run=true`, stop here and show the exact `reports__delete` payload.
5. If `dry_run=false`, call `reports__delete` with `report_id`. Report the
   approval request if one comes back, and after it is approved confirm the
   deletion.
6. Verify by calling `reports__get` for the same id; a deleted report is gone.

Output format:
- `Target`: report_id, name, access scope, latest version, version count.
- `Impact`: what the deletion destroys, and anything that blocks it.
- `Applied Changes`: the `reports__delete` call and its result, or the dry-run
  payload, or the approval that is waiting.
- `Verification`: evidence the report is gone.
