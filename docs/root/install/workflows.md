# Workflows

Workflows are Seizu's durable automation pipelines. A workflow has zero or
more named Cypher **inputs**, an ordered list of **activities**, and either a
UTC time schedule or a set of SyncMetadata watch filters. Temporal owns the
schedule and execution history, so runs are not tied to a single polling
process and missed ticks can be caught up after downtime.

## Managing workflows

Open **Workflows** in the sidebar. Users with `workflows:read` can inspect
definitions, versions, and runs. `workflows:write` permits create, edit, and
run-now; `workflows:delete` permits deletion. The legacy
`scheduled_queries:*` permissions are expanded to these permissions during
the compatibility release.

The editor supports:

- adding any number of named query inputs;
- adding name/value parameters and an optional row cap to each query;
- adding activities and selecting which input supplies each activity's rows;
- activity-specific parameter forms supplied by the enabled module;
- pointer or keyboard drag-and-drop activity reordering; and
- interval, hourly, daily, monthly, or SyncMetadata watch triggers.

Query input IDs use `lower_snake_case`. Inputs are evaluated concurrently at
the start of a run. Activities then execute from top to bottom. Ordinary
activity modules receive the selected input's rows. A `workflow` activity
starts a registered code-defined Temporal child workflow and waits for its
result before the next activity starts.

**Run now** starts a Temporal execution immediately, including for a disabled
workflow. Disabling a workflow pauses its Temporal Schedule but deliberately
does not prevent an operator-requested run.

## Scheduling behavior

Schedules are stored as desired state in the report store and reconciled to a
deterministic Temporal Schedule ID. The UI exposes schedule sync state so a
temporary Temporal outage does not lose the saved definition.

- Interval and hourly schedules run once when first enabled, then at their
  configured cadence.
- Daily schedules use selected weekdays and an `HH:MM` UTC time.
- Monthly schedules use selected calendar days and clamp missing days to the
  month's last day.
- Watch schedules poll at `WORKFLOW_WATCH_POLL_SECONDS`; the Temporal workflow
  checks SyncMetadata and exits as skipped when nothing changed.
- Overlap policy is **buffer one**: one due run is retained while a previous
  run is still active.

## YAML seed format

The YAML file remains a seed source, not runtime configuration:

```yaml
workflows:
  - name: Notify on critical CVEs
    inputs:
      critical_cves:
        type: query
        cypher: |
          MATCH (cve:CVE)
          WHERE cve.severity = $severity
          RETURN {id: cve.id, severity: cve.severity} AS details
        parameters:
          - name: severity
            value: CRITICAL
        max_rows: 200
    schedule:
      type: daily
      days_of_week: [0, 1, 2, 3, 4]
      hour: 9
      minute: 0
    enabled: true
    activities:
      - type: slack
        input: critical_cves
        parameters:
          slack_channel: security-alerts
```

`scheduled_queries` seeds are still accepted when `workflows` is absent and
are normalized to a one-input workflow. A configuration cannot contain both
top-level sections.

## API and CLI

The canonical REST collection is `/api/v1/workflows`; it exposes CRUD,
version history, `POST /{id}/run`, and Temporal run history. The CLI mirrors
this under `seizu workflows`. MCP built-ins use the `workflows__*` group.

The `/api/v1/scheduled-queries` API and `seizu scheduled-queries` CLI remain
temporary aliases. They list only definitions that can be projected to the
old single-query/action shape without losing meaning.

## Worker configuration

The `seizu-temporal-worker` service owns both schedule reconciliation and
execution. Relevant settings are:

- `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, and `TEMPORAL_TASK_QUEUE`;
- `WORKFLOW_ACTIVITY_MODULES` (comma-separated Python modules);
- `WORKFLOW_QUERY_MAX_ROWS`;
- `WORKFLOW_WATCH_POLL_SECONDS`; and
- `WORKFLOW_RECONCILE_SECONDS`.

`SCHEDULED_QUERY_MODULES` is accepted as a fallback setting for one
compatibility release. The standalone `seizu-scheduled-queries` service is no
longer part of the Compose stack.
