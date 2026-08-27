# Upgrading Seizu

Read the [changelog](https://github.com/mappedsky/seizu/blob/master/CHANGELOG.md)
from the version you run through the version you plan to deploy. Breaking
changes link to the applicable procedure in this guide. Do not skip intermediate
procedures when upgrading across more than one release.

For every production upgrade:

1. Record the running Seizu version and take recoverable backups of PostgreSQL,
   the chat checkpoint database, configuration files, and deployment secrets.
2. Read every applicable breaking-change section and identify settings that
   must be added, removed, or renamed before startup.
3. Quiesce writes and stop the web and Temporal worker processes when a
   procedure calls for an offline migration. A database backup taken while
   writes continue is not a cutover point.
4. Apply the release-specific procedure below. Seizu runs inspector-guarded
   Alembic application-schema migrations during startup; LangGraph owns the
   checkpoint schema. A release-specific data migration is never implied by
   those schema upgrades.
5. Verify schema versions, entity counts, schedules, authentication, and a
   representative report before reopening traffic.
6. Keep the pre-upgrade backups and old deployment artifacts until the
   verification window closes. Do not run an older Seizu release against a
   database written by a newer one unless its upgrade procedure explicitly says
   that downgrade is supported.

## 5.0.0

### Standing up Temporal for chat

Interactive chat turns, scheduled chats, and the session reaper all execute as
Temporal workflows in this release. `CHAT_ENABLED=true` without a reachable
Temporal server leaves chat unable to admit a turn.

1. Provision a Temporal server and namespace reachable from both the web service
   and `seizu-temporal-worker`, and set `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`,
   and `TEMPORAL_TASK_QUEUE` identically on both.
2. Give the worker the same chat configuration the web service has:
   `CHAT_ENABLED`, `CHAT_SCHEDULES_ENABLED`, `CHAT_SCHEDULE_TIMEOUT_SECONDS`,
   `CHAT_LLM_*`, `CHAT_CHECKPOINT_*`, the sandbox settings, and the external MCP
   proxy definitions and token environment variables. A worker missing any of
   these fails the turns that need them, not startup.
3. Remove the `seizu-scheduled-chats` service and any supervisor entry for
   `python -m reporting.scheduled_chats`; both are gone. Remove
   `CHAT_SCHEDULES_POLL_SECONDS`.
4. Update any client of `POST /api/v1/chat/stream` or
   `GET /api/v1/chat/stream/{thread_id}` before deploying. Sending is now
   admit-then-attach: `POST /api/v1/chat/threads/{thread_id}/turns` with an
   idempotency key, then `GET /api/v1/chat/turns/{turn_id}/stream`, with
   `POST /api/v1/chat/turns/{turn_id}/cancel` to stop and
   `GET /api/v1/chat/threads/{thread_id}/turns/active` to reattach after a
   reload.
5. Size `TEMPORAL_MAX_CONCURRENT_ACTIVITIES` for the fleet before enabling
   distributed plan steps at scale; `CHAT_ORCHESTRATOR_MAX_PARALLEL` bounds one
   turn, this bounds the cluster.

### Chat settings that changed meaning

Apply these before starting the release; a stale value is silently the old
intent, not an error.

- `CHAT_LLM_CONTEXT_MAX_CHARS` is no longer read. Set
  `CHAT_LLM_CONTEXT_MAX_TOKENS` (default 40,000) instead. Context is budgeted in
  tokens against the model's own window.
- `CHAT_RUN_COST_BUDGET_USD` now defaults to `2.00` where it was unlimited. This
  is the runaway guard; raise it deliberately rather than discovering it in a
  stopped run.
- `CHAT_LLM_MAX_TOKENS`, `CHAT_ORCHESTRATOR_PLANNER_MAX_TOKENS`,
  `CHAT_RUN_TOKEN_BUDGET`, and `CHAT_RUN_MAX_LLM_CALLS` now default to `0`,
  meaning derive from the model and the plan. A value carried over from a
  previous deployment still pins the old number — including
  `CHAT_LLM_MAX_TOKENS=4096`, which starves the planner on a reasoning model and
  silently collapses every plan to one step.
- `SANDBOX_AGENT_CREDENTIAL_PROXY_REQUIREMENTS` is replaced by
  `SANDBOX_AGENT_CREDENTIAL_PROXY_REQUIREMENTS_FILE`, naming a hash lock. If you
  set `SANDBOX_AGENT_CREDENTIAL_PROXY_TEMPLATE`, re-run
  `make lock_proxy_requirements` and `make build_proxy_template`: a template is
  now used as built and a run installs nothing over it.

### Reviewing roles before the plugin permissions land

`plugins:read` / `plugins:write` / `plugins:delete` imply the legacy
`skillsets:*` and `skills:*` permissions, but the reverse now requires **both**
legacy permissions. Audit user-defined roles that grant one and not the other:
before this release such a role silently expanded to `plugins:write`, which
grants package installation. Decide separately who should hold the new
`model_profiles:read` / `:write` / `:delete` permissions, which the built-in
Admin role receives.

### Migrating from DynamoDB to PostgreSQL

The release that removes the DynamoDB backends is a hard storage boundary. Do
not point it at an empty PostgreSQL database and start accepting traffic.

1. On the last release that supports DynamoDB, block external writes and stop
   every Temporal worker. Leave one API process reachable only by the migration
   operator, then take recoverable backups of the application table, checkpoint
   table, and any checkpoint-offload bucket.
2. Run `seizu export --config /safe/path/seizu-export.yaml` against that
   isolated API, then stop it. The export contains the durable product
   configuration supported by seed/export: spaces, reports, workflows,
   toolsets, skillsets, and their cross-references. Preserve this file with the
   database backups.
3. Provision and back up two PostgreSQL databases: one for `SQL_DATABASE_*`
   and one for `CHAT_CHECKPOINT_DATABASE_*`. Start this release with
   `CHAT_CHECKPOINT_CREATE_TABLE=true` and without any removed backend or
   DynamoDB checkpoint settings; Alembic upgrades the application schema and
   LangGraph creates the checkpoint schema.
4. Import the exported configuration with `seizu seed --force --config
   /safe/path/seizu-export.yaml` and verify report, workflow, space, toolset,
   and skillset counts before reopening traffic.

User profiles are rebuilt from the identity provider on the next authenticated
request. Query history, user-defined role history, action confirmations,
scheduled/chat run transcripts, and report version history are not represented
by the seed/export format. If those records are retention requirements, remain
on the last dual-backend release and perform a deployment-specific database
migration before upgrading. The PostgreSQL-only release deliberately refuses
stale backend settings instead of pretending those records were copied.

Existing DynamoDB LangGraph checkpoints and their offloaded objects are not
migrated. Conversation history therefore starts empty on PostgreSQL; remove the
corresponding chat-session records during a deployment-specific migration so
the sidebar cannot point at missing checkpoints. Keep the old tables and bucket
unchanged until the verification window closes.

Rollback means stopping all new writes, restoring the pre-cutover backups, and
redeploying the last release that supports DynamoDB. Never point that older
release at data written by the PostgreSQL-only release.

Startup rejects `REPORT_STORE_BACKEND`, `CHAT_CHECKPOINT_BACKEND`, and all
removed persistence-specific DynamoDB/S3 settings even when they contain the old
SQL-selecting values. This makes an incomplete cutover fail before any schema or
application write.
