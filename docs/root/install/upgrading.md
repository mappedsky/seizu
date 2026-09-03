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

## 5.1.0

No schema migrations and no removed settings. Two defaults change behavior.

### The MCP graph query rejects risky plans

`graph__query` plans every query as part of the validation pass it already ran,
and now refuses to execute one when Neo4j reports a performance notification, or
when a non-index scan participates in a plan whose largest cardinality estimate
exceeds `MCP_GRAPH_QUERY_UNINDEXED_MAX_ESTIMATED_ROWS` (100,000). The rejection
returns the plan, the estimate and the scan operators so the caller can rewrite
the query.

1. After deploying, watch MCP clients and chat turns for the
   `query_plan_rejected` error code. A bounded scan below the threshold is
   unaffected.
2. Raise `MCP_GRAPH_QUERY_UNINDEXED_MAX_ESTIMATED_ROWS` if your graph plans
   legitimately large, set it to `0` to reject every non-index scan, or set
   `MCP_GRAPH_QUERY_REJECT_UNINDEXED=false` to restore 5.0.0 behavior.
3. Set both on the web service **and** on `seizu-temporal-worker`; the chat
   agent calls this tool from either process.

REST queries, the query console, and authored Cypher-backed toolset tools are
not affected by this policy.

### Model profiles: the assistant stage is now the base model

A profile has one primary base model. Direct assistant calls use it, and every
runtime stage inherits it unless the profile overrides that stage. The separate
`assistant` stage override is gone; an entry stored by 5.0.0 is dropped when the
profile is read, and the primary model governs those calls instead.

1. Open each model profile and re-check any that set an `assistant` override.
   Nothing fails, but the model in effect for direct assistant calls may change.
2. `router` and `verifier` are now overridable stages, and
   `CHAT_LLM_WORKER_SUMMARY_MODEL` is a new deployment setting for the
   worker-summary pass. Both are optional; empty inherits as before.
3. `CHAT_LLM_MODEL` may now be left empty when an enabled **default** profile
   exists — the profile supplies every stage. Keep it set if you have no
   profiles, or as the fallback for turns that run without one.
4. `seizu-temporal-worker` now validates chat model ids at startup, the same way
   the web service does, and it validates every enabled profile's primary,
   economy and stage-override ids as well as `CHAT_LLM_ROUTER_MODEL`,
   `CHAT_LLM_WORKER_SUMMARY_MODEL` and `SANDBOX_LLM_MODEL`. A model id LiteLLM
   cannot resolve now fails the worker at startup rather than the first turn
   that needs it. Deploy the worker before or with the web service and check its
   logs.

### Seeding model profiles

Model profiles are now part of the seedable configuration. `seizu export` emits
a top-level `model_profiles:` section, and `seizu seed` reconciles it by exact
profile **name** — ids are server-generated and are deliberately not exported.
Seeding never deletes a profile, and no profiles are seeded by default, so an
existing deployment needs no action here. If you adopt the section, remember
that exactly one enabled profile must be the default; the seeder writes the
declared default first so the store is never left without one.

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

### Scheduled queries become workflows

**Existing definitions need no migration.** A workflow is the same record under
a new name: same ids, same version history. A scheduled query is projected into
a workflow automatically — its Cypher becomes the first stage, with an output
named `query`, and each of its actions becomes a stage after it. Nothing is
copied or rewritten, and `/app/scheduled-queries/<id>` redirects to
`/app/workflows/<id>`.

What a workflow adds is shape: ordered stages, several activities running in
parallel within one stage, and named outputs that later stages consume. The old
one-query-then-actions form is the narrowest case of that.

Three things to change on your side:

1. **Seed files.** Rename the top-level `scheduled_queries:` key to
   `workflows:`. A configuration containing both is rejected — the loader
   refuses rather than guessing which one wins.
2. **`SCHEDULED_QUERY_MODULES`.** Renamed to `WORKFLOW_ACTIVITY_MODULES`; the
   old name is accepted for one release.
3. **The `seizu-scheduled-queries` service**, if you run one, is gone. The
   `seizu-temporal-worker` owns both scheduling and execution. See
   [Standing up Temporal for chat](#standing-up-temporal-for-chat) if you have
   not deployed it yet.

The `/api/v1/scheduled-queries` REST routes, `seizu scheduled-queries` CLI, and
`scheduled_queries__*` MCP tools remain aliases for one release. Note that they
list **only** definitions still expressible in the old single-query-plus-actions
shape: a workflow with parallel activities or multiple query stages will not
appear through them, so scripts that enumerate definitions should move to
`/api/v1/workflows` before you build anything multi-stage.

One case does need manual work: a definition saved under the superseded
feature-branch `inputs`/`activities` shape cannot be projected and raises on
read. Recreate or reseed those. Everything installed from a released version is
unaffected.

See [Workflows](workflows.md) for the stage model.

### Skillsets become plugins

Existing skillsets are projected into one plugin per skillset at startup, with
their existing `skillset__skill` prompt names preserved, so agents calling them
keep working. The `/api/v1/skillsets` REST routes, the `skillsets__*` MCP tools,
the CLI commands and the permission names remain compatibility aliases for one
release. See [Agent Plugins](agent-plugins.md) for the package format they are
projected into.

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
