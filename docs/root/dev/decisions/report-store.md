# Report store decisions (`STO`)

Decisions about how reports, versions and related entities are persisted across
the two backends. For choosing and configuring a backend, see the
[backend install docs](../../install/backend.md).

Primary code: `reporting/services/report_store/`.

## STO-001 — The backend is transparent behind `get_store()`

**Applies to:** `report_store/__init__.py`

Import `from reporting.services import report_store` and call through the
module. `ReportStore` (`base.py`) is the ABC; `dynamodb.py` and `sql.py`
implement it; `REPORT_STORE_BACKEND` selects one (default `dynamodb`).

**Don't:** branch on the backend outside the store package. A caller that knows
which store it is talking to is a caller that will drift when the other one
changes.

## STO-002 — The `REPORT_LIST` item is a full duplicate, not a pointer

**Applies to:** `_report_record`, `_report_record_items`, `_REPORT_RECORD_FIELDS`
in `report_store/dynamodb.py`

Each report's `REPORT_LIST` item duplicates its `#METADATA` item in full, and
`list_reports`/`list_space_reports` read **only** the duplicate.

**Why it is dangerous:** a field written to `#METADATA` but not to `REPORT_LIST`
fails **silently** — the report is correct when fetched by id and wrong in every
listing.

**Do:** add new report fields to `_REPORT_RECORD_FIELDS` so both copies are
built from one dict. Never write one copy at a time.

## STO-003 — The spaces GSI is optional by design and keyed on an existing attribute

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

**Note:** SQLite dev DBs created before nullable user emails keep the old
`NOT NULL`. Recreate the DB to test emailless users on SQLite.
