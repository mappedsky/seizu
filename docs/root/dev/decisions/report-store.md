# Report store decisions (`STO`)

Decisions about how reports, versions and related entities are persisted. For
database configuration see the [backend install docs](../../install/backend.md);
for breaking migrations see the [upgrade guide](../../install/upgrading.md).

Primary code: `reporting/services/report_store/`.

## STO-001 — The persistence backend is selected behind `get_store()` — superseded by STO-005

**Superseded by:** STO-005. Seizu exposed DynamoDB and SQL implementations through one backend-neutral report-store facade.

## STO-002 — DynamoDB report-list records duplicate report metadata — superseded by STO-005

**Superseded by:** STO-005. DynamoDB list queries read a full metadata duplicate rather than the report's canonical metadata item.

## STO-003 — The DynamoDB spaces index is optional — superseded by STO-005

**Superseded by:** STO-005. Space report listing used an optional sparse GSI with a full-partition fallback.

## STO-004 — Alembic is the sole startup schema owner

**Applies to:** `report_store/migrations.py`, `reporting/migrations/versions/`

`SQLModelReportStore.initialize()` upgrades to head under a pg advisory lock so
concurrent workers don't race.

**Every operation must be inspector-guarded.** The baseline `0001` calls
`SQLModel.metadata.create_all` on a fresh database, so every later revision runs
against a schema that may already contain what it is adding. Verify against both
a fresh volume and an upgraded database. `0005_spaces.py` is the current
template.

**Test seam:** unit tests inject SQLite engines directly. Runtime settings do
not accept SQLite, but migration revisions must remain inspector-guarded so the
fast fresh/upgrade tests continue to exercise both starting shapes.

## STO-005 — PostgreSQL is the only application and checkpoint store

**Applies to:** `report_store/__init__.py`, `report_store/sql.py`,
`chat_graph.initialize_chat_checkpoints`, Compose, and persistence settings

The report-store facade remains stable for REST, MCP, CLI, and workflow callers,
but it constructs `SQLModelReportStore` directly. LangGraph checkpoints always
use `AsyncPostgresSaver`; the application and checkpoint databases remain
independently configurable because their retention, migrations, permissions,
backup, and restore policies differ.

This removes runtime backend selection rather than changing a default. Startup
rejects every removed selector and persistence-specific DynamoDB/offload
setting before initializing either database. Silently ignoring a legacy
setting could make an upgraded deployment accept traffic against an empty
PostgreSQL database, which is data loss disguised as a successful rollout.

PostgreSQL is part of the default development stack. Alembic remains the sole
application-schema owner (STO-004), and LangGraph owns its checkpoint schema
behind a PostgreSQL advisory lock. Runtime URLs must be PostgreSQL; injected
SQLite engines remain only as a fast unit-test seam.

The release boundary and backup/rollback procedure are documented under
"Migrating from DynamoDB to PostgreSQL" in the upgrade guide. Legacy checkpoint
history is explicitly disposable and is not copied into PostgreSQL.

## STO-006 — Plugin packages are immutable revisions over content-addressed blobs

**Applies to:** `PluginRecord`, `PluginVersionRecord`, `PluginBlobRecord`,
`PluginFileRecord`, `PluginDraftRecord`

PostgreSQL stores package manifests, indexed skills, file manifests, and SHA-256
addressed file bytes. Publishing creates one immutable revision atomically;
authoring mutates a separate single draft.

**Why:** package files must remain byte-identical for MCP resource reads and
sandbox execution throughout a turn, while references and assets often repeat
unchanged between versions. Mutable filesystem paths would make a revision URI
lie after an edit, and copying every file into every revision would pay the full
package size for small metadata changes.
