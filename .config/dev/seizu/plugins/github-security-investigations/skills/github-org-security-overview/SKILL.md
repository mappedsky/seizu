---
name: github-org-security-overview
description: Guide an agent through a GitHub organization security investigation using
  the github_security toolset and produce a prioritized executive summary.
allowed-tools: github_security__sync_freshness github_security__org_overview github_security__repo_risk_summary
  github_security__top_vulnerabilities github_security__actions_hardening_findings
  github_security__coverage_gaps github_security__identity_access_summary github_security__recommendations
---
Investigate the GitHub organization `org` using the github_security user-defined tools. Treat `org` as an explicit required input and do not substitute a default organization.

Inputs — the values arrive in the `## Inputs` block below these instructions:
- `org` — the organization to review; empty matches every organization in the graph.
- `exclude_forks` — whether forked repositories are left out.
- `include_archived` — whether archived repositories are included.
- `limit` — how many rows to request from each ranked query.

Workflow:
1. Call `github_security__sync_freshness` with `org` first. State the newest GitHub sync timestamp and qualify the report if the data looks stale or incomplete.
2. Call `github_security__org_overview` with `org`, `exclude_forks`, and `include_archived`. Establish repository count, visibility split, alert totals, and any caveats around fork metadata.
3. Call `github_security__repo_risk_summary` and identify the highest-risk repositories by critical/high alerts, total open alerts, public exposure, and risk score.
4. Call `github_security__top_vulnerabilities` with `state='open'` and `limit`. Prioritize critical and high findings, then high EPSS/CVSS medium findings if they materially change remediation order.
5. Call `github_security__actions_hardening_findings`. Look for unpinned actions, write-level permissions, publish/deploy workflows, and `pull_request_target` usage.
6. Call `github_security__coverage_gaps`. Identify missing CodeQL, Scorecard, dependency graph coverage, manifests, and language metadata.
7. Call `github_security__identity_access_summary`. Summarize org admins, direct repo admins, and MFA visibility limits without overstating unknown fields.
8. Call `github_security__recommendations` and reconcile its computed recommendations with the evidence from the earlier tools.

Output format:
- Start with a concise security posture summary for `org`.
- Include a `Data Freshness` note with exact sync timestamps when available.
- Include `Top Risks` ordered by severity and likely exploitability.
- Include `Vulnerabilities` with repository, package, severity, CVE/GHSA when present, patch version, and why it matters.
- Include `Misconfigurations and Coverage Gaps` focused on GitHub Actions, scanning, dependency visibility, and identity/access findings.
- Include `Top Recommendations` as a numbered remediation plan, with affected repositories and concrete next actions.
- Call out graph limitations explicitly, especially missing fork metadata, archived state uncertainty, absent MFA fields, or repositories with no dependency manifests.
- Do not claim a repository is forked, archived, protected, or MFA-enforced unless the tool output contains that evidence.
- Keep the response evidence-backed; separate confirmed facts from inferences.
