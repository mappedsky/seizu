# Report store decisions (`STO`)

Decisions about how reports, versions and related entities are persisted. For
database configuration and the legacy transition boundary, see the
[backend install docs](../../install/backend.md).

Primary code: `reporting/services/report_store/`.

## STO-001 — The backend is transparent behind `get_store()` (superseded by STO-005)

**Applies to:** `report_store/__init__.py`

Import `from reporting.services import report_store` and call through the
module. `ReportStore` (`base.py`) is the ABC; `dynamodb.py` and `sql.py`
implement it; `REPORT_STORE_BACKEND` selects one (default `dynamodb`).

**Don't:** branch on the backend outside the store package. A caller that knows
which store it is talking to is a caller that will drift when the other one
changes.

## STO-002 — The `REPORT_LIST` item is a full duplicate, not a pointer (historical)

**Applies to:** `_report_record`, `_report_record_items`, `_REPORT_RECORD_FIELDS`
in `report_store/dynamodb.py`

Each report's `REPORT_LIST` item duplicates its `#METADATA` item in full, and
`list_reports`/`list_space_reports` read **only** the duplicate.

**Why it is dangerous:** a field written to `#METADATA` but not to `REPORT_LIST`
fails **silently** — the report is correct when fetched by id and wrong in every
listing.

**Do:** add new report fields to `_REPORT_RECORD_FIELDS` so both copies are
built from one dict. Never write one copy at a time.

## STO-003 — The spaces GSI is optional by design and keyed on an existing attribute (historical)

**Applies to:** `space_reports_index`, `_query_space_reports_via_index`

`space_id` hash + `SK` range, `ALL` projection. It backs `list_space_reports`
and the `delete_space` emptiness check, and is queried with
`begins_with(SK, "REPORT#")` because the report `#METADATA` copy and sub-space
items also carry `space_id`.

**Why optional:** production tables are IaC-managed (`DYNAMODB_CREATE_TABLE`
defaults false), so a missing index must not be an error.
`_query_space_reports_via_index` returns `None` and callers fall back to
filtering the whole `REPORT_LIST` partition. The availability probe is cached
per process.

**Why `space_id` rather than a dedicated attribute:** keying on the attribute
that already exists is what makes the index need **no backfill**.

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
"Transitioning from DynamoDB" in the backend install guide. Legacy checkpoint
history is explicitly disposable and is not copied into PostgreSQL.
