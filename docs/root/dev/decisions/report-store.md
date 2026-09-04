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
`PluginFileRecord`

PostgreSQL stores package manifests, indexed skills, file manifests, and SHA-256
addressed file bytes. Publishing creates one immutable revision atomically.
Blobs are shared between revisions and between plugins, so `delete_plugin`
collects the ones its deletion orphaned rather than cascading them.

**Why:** package files must remain byte-identical for MCP resource reads and
sandbox execution throughout a turn, while references and assets often repeat
unchanged between versions. Mutable filesystem paths would make a revision URI
lie after an edit, and copying every file into every revision would pay the full
package size for small metadata changes.

## STO-007 — Plugin seeds reference packages beside the YAML configuration

**Applies to:** `ReportingConfig.plugins`, `seed._seed_plugins`

Each plugin seed names a directory or ZIP relative to the YAML file, keyed by
its expected Seizu plugin ID. The seeder validates and installs it through the
ordinary package API, and content digests make repeat runs idempotent. Export
preserves declared sources but does not synthesize paths for other installs.

**Why:** binary references, executable scripts, and the package hierarchy do
not have a faithful or reviewable inline YAML representation. A relative source
keeps the package and configuration relocatable as one deployment artifact,
while the expected-ID key catches a package swapped into the wrong slot.

## STO-008 — Legacy skillset projection is single-writer at startup

**Applies to:** `SQLModelReportStore._migrate_legacy_skillsets`

PostgreSQL serializes the legacy-skillset-to-plugin projection with a
transaction advisory lock. Every Gunicorn worker still runs the idempotent
startup check, but only one may execute its check-and-create sequence at once.

**Why:** schema migration locking does not cover the data projection that
follows it. Two fresh workers can both observe a missing plugin and attempt the
same primary-key insert, turning an otherwise healthy multi-worker startup into
a worker boot failure.

## STO-009 — Production skillsets cut over through an explicit same-ID package

**Applies to:** `ReportingConfig.plugins`, `mcp_runtime.list_prompts_for_user`

A production skillset moves off the compatibility projection by seeding an
explicit Agent Plugin with the same skillset and skill IDs. Plugin prompts take
precedence while the legacy rows remain installed, so the packaged workflow can
be exercised in ordinary turns before the compatibility source is removed.
Legacy compatibility writes and deletion only update a package carrying the
projection ownership marker; after an explicit package takes over that ID, the
legacy surface cannot overwrite or delete it.

The plugin reserves its namespaced prompt before tool-dependency filtering. An
unavailable plugin skill is therefore absent rather than falling through to a
same-named legacy definition; listing and rendering always select the same
source.

**Why:** validating a differently named example package proves package mechanics
but not behavioral equivalence or fresh-install reconstruction. Same-ID shadowing
tests the real selection path while keeping the legacy definition as a rollback
until a package-only seed and end-to-end turn have both passed.

## STO-010 — Plugin edits are staged in the client and published as one package

**Applies to:** `POST /api/v1/plugins/{id}/publish`, `PluginPublishRequest`,
`read_plugin_blob`, `PluginEditor.tsx`

The editor loads a published revision, holds every edit in the browser, and
submits the complete package in one request. A file it did not change is sent as
its SHA-256 digest and resolved against blobs that plugin already stores; a
`base_revision` rides along and a stale one is refused with `409`.

**Why:** the server-side draft this replaced was written only when the author
pressed "Save to draft", so field edits were lost by ordinary back navigation,
and the draft was keyed by plugin ID alone — two authors editing one plugin
silently shared and clobbered it. Staging removes both: there is one submit, it
is atomic, and concurrency is one comparison rather than a second mutable copy
of the package. Digest retention is what makes it affordable — without it,
publishing a one-line edit would round-trip every asset through the browser.

Server-held drafts are not ruled out for the future; they are simply not part
of this shape. Anything that stores a partial package again has to answer what
this one answers: which identity owns the draft, and what happens to it when
the plugin is published from somewhere else in the meantime.

**Don't:** resolve a retained digest against any blob in the table — scope it to
the plugin, or a caller could attach content from a package it is not editing.

## STO-011 — Model profiles are versioned records with one enabled default

**Applies to:** `model_profiles`, `model_profile_versions`, migrations `0010`
and `0011`, `chat_sessions.model_profile_*`, `scheduled_chats.model_profile_id`

Model profiles use a mutable current record plus immutable version rows. A
partial unique index permits at most one default, and store mutations preserve
the stronger application invariant: when any enabled profiles exist, exactly
one is enabled and default. The first enabled profile is promoted automatically;
changing or deleting the current default is refused until another is selected.

Chat sessions store a nullable profile ID, selected reasoning effort, and lock
bit. Admission locks the row and fixes the profile family in the same transaction
as the first turn; later updates may change effort but not the family. Scheduled
chats store a nullable profile ID and use its default effort. Null means follow
the current default; it means environment configuration only while the catalog
has no enabled profiles. Run commands carry the resolved snapshot instead of a
foreign-key lookup.

**Why:** references need to survive a profile being disabled or deleted so the
next attempted run can ask for an explicit replacement, while an admitted run
must remain reproducible without depending on mutable catalog state. A database
constraint closes the concurrent two-default race; store checks supply the
exactly-one half that a partial index cannot express. Locking profile selection
inside admission prevents a concurrent session update from moving a turn to a
different model after its immutable command has been captured.

## STO-012 — Application identifiers are UUIDv7 strings

**Applies to:** `ReportStore.generate_id`, `report_store/sql.py`

Every server-generated application identifier uses a canonical UUIDv7 string.
Existing decimal identifiers remain valid because persisted and API identifier
fields stay strings; this changes generation only and requires no data rewrite.

**Why:** UUIDv7 preserves time-ordered identifiers without a per-instance
machine ID, and its canonical string representation crosses JSON/JavaScript
boundaries without integer precision loss. Snowflake identifiers required every
replica to coordinate a machine ID and were unsafe when consumers treated them
as JavaScript numbers.
