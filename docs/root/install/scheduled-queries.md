# Scheduled Queries

> **Deprecated compatibility API:** Scheduled Queries have been replaced by
> [Workflows](workflows.html). Existing single-query definitions, REST paths,
> CLI commands, permissions, and YAML seeds remain available for one
> compatibility release when they can be represented without losing data.
> New automation should use the Workflows UI and `/api/v1/workflows` API.

## Purpose

Dashboards are a good visual representation of a system, for when you want to actively check something, but sometimes you want to be passively notified about changes in the graph data.

Seizu can run queries on a set schedule, or when the SyncMetadata graph data changes.
It can take the query results and pass them into a list of configurable actions.

## Managing Scheduled Queries

Scheduled queries are stored in the database and managed through the Seizu UI or API. They are not read directly from the YAML configuration file at runtime.

> **Permissions:** Creating and editing scheduled queries requires the `scheduled_queries:write` permission (`seizu-admin`). Deleting requires `scheduled_queries:delete`. Restoring a historical version also requires `scheduled_queries:write`. Users with `seizu-viewer` or `seizu-editor` roles can view scheduled queries and history but will not see the **New scheduled query** button, and write/delete/restore actions in the **⋮** menu will be disabled.

### Scheduled Queries list

Navigate to **Scheduled Queries** in the sidebar to view all scheduled queries. From the list you can:

- Click a query name to open its details page.
- Open the **⋮** menu on any row to **Edit**, **Run now**, **View history**, or **Delete** a query.
- The table shows the trigger type, configured actions, enabled status, current version, latest update timestamp, and who last updated each query.

### Details page

The details page shows the query's status, trigger, version, owner, and last-run outcome alongside its Cypher, parameters, and configured actions, plus any recent run errors. For queries with a `temporal` action it also shows a **Workflow runs** panel with the recent workflow executions and their per-activity status, retries, and failures — see [Run visibility in the UI](temporal-workflows.html#run-visibility-in-the-ui). The **Edit** button and the **⋮** menu (**Run now**, **View history**, **Delete**) mirror the list-row actions.

### Running a query on demand

Choose **Run now** from the **⋮** menu — on a list row or on the details page (requires `scheduled_queries:write`) — to request an immediate run. The worker picks the request up on its next poll (`SCHEDULED_QUERY_FREQUENCY`, default a few seconds) and runs the query with its configured actions — **even if the query is disabled**, so you can test a query before enabling it. The same is available via `POST /api/v1/scheduled-queries/<id>/run` and `seizu scheduled-queries run <id>` from the CLI.

### Creating a scheduled query

Click **New scheduled query** on the Scheduled Queries page. The form includes:

| Field | Description |
|-------|-------------|
| name | A user-friendly name for the scheduled query. |
| cypher | A Cypher query to run. The query must return the data as `details` (or as configured via `query_return_attribute` in the action config). |
| enabled | Whether the query will be run by the worker. |
| trigger | Choose **Schedule** (a structured time-based schedule) or **Watch scans** (run when matching SyncMetadata nodes are updated). |
| schedule | The structured schedule: **Every N minutes**, **Hourly** (every N hours), **Daily** (selected weekdays at an HH:MM UTC time), or **Monthly** (selected days of month at an HH:MM UTC time). Used when trigger is **Schedule**. |
| watch scans | List of SyncMetadata filters. Each entry takes `grouptype`, `syncedtype`, and `groupid` (all support `.*` as a wildcard). Used when trigger is **Watch scans**. |
| params | Query parameters. Each param has a name and a value. Toggle the **list** button to switch between a single value and a comma-separated list of values. |
| actions | One or more actions to run with the query results. See the Built-in Actions section below. |

### Editing a scheduled query

Choose **Edit** from the **⋮** menu. The form is the same as creation. An optional **Comment** field is shown when editing, allowing you to describe what changed.

Every save creates a new numbered version; existing versions are never overwritten.

### Version history

Every save creates a new numbered version. To view the history:

- Choose **View history** from the **⋮** menu in the Scheduled Queries list.

The history page lists all versions newest-first, showing the version number, save date, who created that version, and the save comment. The current (latest) version is labeled **current**.

Click a version number to view the full configuration at that point in time. From the overflow menu on any version row you can **Restore** to save that historical configuration as a new latest version. The Restore action is disabled if you do not have the `scheduled_queries:write` permission.

Restoring a version never deletes history — it creates a new version whose config matches the restored one, with a comment of `Restored from version N`.

### Deleting a scheduled query

Choose **Delete** from the **⋮** menu and confirm. This permanently removes the query and all its versions.

## YAML Configuration (for seeding)

The YAML configuration file is used only as a seed source — it is not read at runtime by the scheduled query worker.
Scheduled queries can be seeded from the YAML file using:

```bash
make seed_dashboard
```

The `scheduled_queries` section in the YAML uses a **list** format:

```yaml
scheduled_queries:
  - name: Recently published HIGH/CRITICAL CVEs
    cypher: recent-cves          # reference to a key in the top-level queries dict
    params:
      - name: base_severity
        value:
          - HIGH
          - CRITICAL
    frequency: 1440              # every 24 hours
    enabled: true
    actions:
      - action_type: slack
        action_config:
          title: Recently published HIGH/CRITICAL CVEs
          initial_comment: |
            The following HIGH/CRITICAL CVEs have been published in the last 2 hours.
          channels:
            - C00000000

  - name: K8s container images with no vulnerability scans
    cypher: k8s-images-without-scans
    watch_scans:
      - grouptype: KubernetesCluster
        syncedtype: KubernetesCluster
    enabled: true
    actions:
      - action_type: sqs
        action_config:
          sqs_queue: k8s-image-scanner
```

The `cypher` field is resolved against the top-level `queries` dict; if no matching key is found, the value is used as a literal Cypher string.

Seeding is idempotent by name: existing queries are skipped unless their content has changed or `--force` is passed.

## Scheduling

### Structured schedule

Use the `schedule` field for a time-based schedule (all times UTC). Four types are supported:

```yaml
  - name: Fast-moving inventory check
    schedule:
      type: interval          # every N minutes
      interval_minutes: 15

  - name: Hourly sweep
    schedule:
      type: hourly            # every N hours
      interval_hours: 4

  - name: Weekday morning report
    schedule:
      type: daily             # selected weekdays at hour:minute UTC
      days_of_week: [0, 1, 2, 3, 4]   # 0=Monday .. 6=Sunday
      hour: 9
      minute: 30

  - name: Month-end review
    schedule:
      type: monthly           # selected days of month at hour:minute UTC (default 00:00)
      days_of_month: [1, 15, 31]      # days a month lacks clamp to its last day
      hour: 6
      minute: 15
```

`interval` and `hourly` schedules are anchored to the last run (a new schedule runs immediately). `daily` and `monthly` schedules wait for the first selected occurrence after the schedule is created.

### Fixed frequency (deprecated)

The legacy `frequency` field (minutes between runs) is still honored by the worker for existing records, but is superseded by `schedule` — `frequency: 1440` is equivalent to `schedule: {type: interval, interval_minutes: 1440}`. `frequency` and `schedule` are mutually exclusive; editing a legacy query in the UI migrates it to the equivalent `schedule` on save.

### Watch scans

Use `watch_scans` to trigger a query when Cartography SyncMetadata nodes are updated:

```yaml
  - name: K8s container images with no vulnerability scans
    watch_scans:
      - grouptype: KubernetesCluster
        syncedtype: KubernetesCluster
```

`watch_scans` works by tracking when the query last ran and comparing that time to the SyncMetadata node timestamps. A newly created query will run immediately, then only again after a matching sync is detected.

In the UI, the `grouptype`, `syncedtype`, and `groupid` fields autocomplete from the distinct values present on `SyncMetadata` nodes in the graph (`GET /api/v1/sync-metadata/values`); free-form input (e.g. `.*` regexes) is still accepted.

`frequency`, `schedule`, and `watch_scans` are mutually exclusive; configs with more than one trigger are rejected at save/seed time.

## Built-in Actions

Action configuration forms in the UI are generated dynamically from the schema declared by each module. Required fields are marked with `*`.

### slack

The `slack` action takes query results and attaches them as a CSV to a Slack message.

| Field | Required | Description |
|-------|----------|-------------|
| title | Yes | The title of the Slack message. |
| initial\_comment | Yes | The message body. The CSV is attached to this message. |
| channels | Yes | A list of channel IDs (not names) to send the message to. |
| query\_return\_attribute | No | The attribute in each result row to include. Default: `details` |

Requires the following environment variable:

- `SLACK_OAUTH_BOT_TOKEN`: Slack OAuth bot token for authentication.

### sqs

The `sqs` action enqueues each query result row into an SQS queue.

| Field | Required | Description |
|-------|----------|-------------|
| sqs\_queue | Yes | The SQS queue name to enqueue results into. |
| query\_return\_attribute | No | The attribute in each result row to enqueue. Default: `details` |

Local development options:

- `SQS_CREATE_SCHEDULED_QUERY_QUEUES`: Automatically create the configured queues if they don't exist.
- `SQS_URL`: URL for a local/fake SQS server.

### statsd

The `statsd` action emits a numeric field from each query result row as a DogStatsD metric.
Enabled by default; requires `STATSD_HOST` to be configured (the action logs a warning and skips emission when it is unset).

| Field | Required | Description |
|-------|----------|-------------|
| metric | Yes | StatsD metric name to emit (e.g. `cves.critical`). |
| value\_field | Yes | Field in each result row that holds the numeric value. |
| metric\_type | No | `gauge` (default), `increment`, or `decrement`. |
| tag\_fields | No | List of fields whose values are attached as DogStatsD tags (`field:value`). |
| query\_return\_attribute | No | Top-level attribute of each result row that contains the data. Default: `details` |

Requires the following environment variables (see [StatsD configuration](backend.html#statsd-configuration)):

- `STATSD_HOST`: hostname of a DogStatsD-compatible server (e.g. Telegraf).

#### Example

```yaml
  - name: CVEs by severity
    cypher: |
      MATCH (c:CVE) RETURN c.base_severity AS severity, count(c) AS total
    frequency: 60
    enabled: true
    actions:
      - action_type: statsd
        action_config:
          metric: cves.count
          value_field: total
          tag_fields:
            - severity
```

### temporal

The `temporal` action starts a named [Temporal](https://temporal.io/) workflow with the query results. See [Temporal workflows](temporal-workflows.html) for the full architecture, including the AI-session workflow that evaluates CVEs per repository and how confirmations are handled for headless runs.

| Field | Required | Description |
|-------|----------|-------------|
| workflow | Yes | The registered workflow to start (e.g. `cve_repo_report`). |
| max\_rows | No | Result rows beyond this limit are dropped before starting the workflow. Default: `TEMPORAL_WORKFLOW_MAX_RESULT_ROWS`. |
| query\_return\_attribute | No | The attribute in each result row to forward. Default: `details` |

Requires the following environment variables:

- `TEMPORAL_ADDRESS`: host:port of the Temporal frontend (gRPC).
- `TEMPORAL_NAMESPACE`: Temporal namespace (default `default`).
- `TEMPORAL_TASK_QUEUE`: task queue shared with the Seizu temporal worker (default `seizu-workflows`).

> Looking for recurring AI agent runs? Those are **scheduled chats**, managed from the chat sidebar rather than as a scheduled query action — the agent uses its own tools to query the graph, so no Cypher is needed. See [Scheduled chats](chat-schedules.html).

### log

The `log` action logs query results using Python's standard logger. Intended for development and testing; not enabled by default.

| Field | Required | Description |
|-------|----------|-------------|
| log\_attrs | Yes | A list of attributes from each result row to include in the log message. |
| query\_return\_attribute | No | The attribute in each result row to read. Default: `details` |
| message | No | The log message prefix. Default: `Result for <scheduled_query_id>` |
| level | No | Log level: `debug`, `info`, `warning`, `error`. Default: `info` |

To enable the `log` module, add it to `SCHEDULED_QUERY_MODULES`:

```
SCHEDULED_QUERY_MODULES=reporting.scheduled_query_modules.sqs,reporting.scheduled_query_modules.slack,reporting.scheduled_query_modules.statsd,reporting.scheduled_query_modules.log
```

## Custom Actions

Custom actions can be included through Python modules, configured via the `SCHEDULED_QUERY_MODULES` setting (comma-separated module paths; default includes `sqs` and `slack`).

The module must implement the `ModuleInterface`:

```python
from typing import Any

from reporting.schema.report_config import ActionConfigFieldDef
from reporting.schema.reporting_config import ScheduledQueryAction


class ModuleInterface:
    @staticmethod
    def action_name() -> str:
        return ""

    @staticmethod
    async def setup() -> None:
        return

    @staticmethod
    def action_config_schema() -> list[ActionConfigFieldDef]:
        return []

    @staticmethod
    def handle_results(
        scheduled_query_id: str,
        action: ScheduledQueryAction,
        results: list[dict[str, Any]],
    ) -> None:
        return
```

Key methods:

- `action_name()` — returns the string identifier used in `action_type`
- `setup()` — called once at worker startup for initialisation (e.g. creating SQS queues)
- `handle_results()` — called with each set of query results
- `action_config_schema()` — **optional but recommended**; returns a list of `ActionConfigFieldDef` objects describing the action's config fields. The UI uses this to generate typed input forms instead of a raw JSON textarea. Required and optional fields, types (`string`, `text`, `number`, `boolean`, `string_list`, `select`), defaults, and help text are all declared here.

Example minimal module:

```python
def action_name() -> str:
    return "print"

def setup() -> None:
    return

def handle_results(
    scheduled_query_id: str,
    action: ScheduledQueryAction,
    results: List[Dict[str, Any]],
) -> None:
    for result in results:
        print(result)

def action_config_schema():
    return []
```

Settings for modules should be fetched within the module itself:

```python
from reporting.utils.settings import str_env

_SLACK_OAUTH_BOT_TOKEN = str_env("SLACK_OAUTH_BOT_TOKEN")
```

## Worker compatibility

The standalone scheduled-query worker is no longer deployed. Compatibility
records are normalized and scheduled by `seizu-temporal-worker`; run-now
requests are picked up by its reconciliation loop. The old Python entrypoint
is retained only for a one-release rollback window.
