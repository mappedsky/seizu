# Workflows

Workflows are Seizu's durable automation pipelines. A workflow has an ordered
list of **stages** and either a UTC time schedule or a set of SyncMetadata
watch filters. Activities in one stage start in parallel; the next stage starts
only after every activity in the current stage succeeds. Temporal owns the
schedule and execution history, so runs are not tied to a single polling
process and missed ticks can be caught up after downtime.

## Managing workflows

Open **Workflows** in the sidebar. Users with `workflows:read` can inspect
definitions, versions, and runs. `workflows:write` permits create, edit, and
run-now; `workflows:delete` permits deletion. Edit, run-now, restore, and
delete are additionally owner-scoped: only the user who created a workflow
may mutate it. Non-owners receive the same not-found response as a missing
workflow. The legacy
`scheduled_queries:*` permissions are expanded to these permissions during
the compatibility release.

The editor supports:

- adding, removing, and reordering stages;
- adding activities, reordering them within a stage, or moving them between stages;
- assigning every activity a named output and selecting an earlier-stage output as its input;
- activity-specific parameter forms supplied by the enabled module; and
- interval, hourly, daily, monthly, or SyncMetadata watch triggers.

Output names use `lower_snake_case` and are unique across the workflow. An
activity may reference only an output from an earlier stage, which keeps
parallel activities independent and makes execution deterministic. References
receive the output's `value`; Temporal also retains a small `metadata` object
for status, counts, and diagnostics.

Cypher is now a normal `query` activity, so it can run in any stage and more
than once in one workflow. Its optional input is available to Cypher as
`$input`; `input` is therefore reserved and cannot also be configured as a
static query parameter. Query activities output a list of result-row objects.
Each registered code-defined Temporal workflow (e.g. `cve_repo_report`,
`cve_dependency_remediation`, `cartography_sync`) is its own activity type: the
activity starts the child workflow, waits for it, and exposes the child's
typed result as its named output. Stored definitions using the former
`workflow` activity sub-type (with a `workflow` parameter) are migrated to the
top-level type transparently on read; new saves must use the top-level types.

When one activity fails, the other activities already running in that stage
are allowed to settle. The workflow then fails and no later stage starts.

Query outputs are streamed only up to the configured `max_rows`, rather than
materializing an unbounded Neo4j result first. Row-consuming activity forms
also expose an optional `max_rows`; when set, Seizu takes that prefix before
validating and passing the input to the module. `WORKFLOW_RESULT_MAX_BYTES`
adds a serialized-size bound at every activity boundary. Modules that perform
external side effects run once by default because Temporal activities are
at-least-once; a module may opt into up to ten attempts with an
`activity_retry_attempts()` function after making its handler idempotent.

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
- Watch schedules run a lightweight poll workflow at
  `WORKFLOW_WATCH_POLL_SECONDS`. It starts the configured workflow as a child
  only when SyncMetadata changed, so polling executions do not appear in the
  workflow's recent-runs list. Pre-upgrade scheduled watch executions shared
  one ambiguous ID prefix for polls and real runs, so that legacy prefix is
  omitted from watch-backed recent-run lists after upgrading; manual runs and
  newly triggered runs remain visible.
- Overlap policy is **buffer one**: one due run is retained while a previous
  run is still active.

## YAML seed format

The YAML file remains a seed source, not runtime configuration:

```yaml
workflows:
  - name: Notify on critical CVEs
    schedule:
      type: daily
      days_of_week: [0, 1, 2, 3, 4]
      hour: 9
      minute: 0
    enabled: true
    stages:
      - activities:
          - type: query
            output: critical_cves
            parameters:
              cypher: |
                MATCH (cve:CVE)
                WHERE cve.severity = $severity
                RETURN {id: cve.id, severity: cve.severity} AS details
              parameters:
                - name: severity
                  value: CRITICAL
              max_rows: 200
      - activities:
          - type: slack
            input: critical_cves
            output: slack_notification
            parameters:
              channels: [security-alerts]
```

`scheduled_queries` seeds are still accepted when `workflows` is absent and
are normalized to a query stage followed by one sequential stage per legacy
action. A configuration cannot contain both top-level sections.

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
- `WORKFLOW_RESULT_MAX_BYTES`;
- `WORKFLOW_WATCH_POLL_SECONDS`; and
- `WORKFLOW_RECONCILE_SECONDS`.

`SCHEDULED_QUERY_MODULES` is accepted as a fallback setting for one
compatibility release. The standalone `seizu-scheduled-queries` service is no
longer part of the Compose stack.
