# Spaces

## Purpose

A Seizu deployment accumulates reports faster than a flat list can organise them. **Spaces** group
related reports — one per team, per programme, or per data source — and **sub-spaces** organise
reports within a space.

Both are optional. A report can sit in no space, in a space, or in a space and one of its sub-spaces;
a sub-space can never be set without a space.

Spaces are visible to everyone with `spaces:read`. They carry no access scope of their own.

**A report filed in a space is public.** A space is a shared container, so a draft cannot be filed into
one: publish it first. In the other direction, a report has to be removed from its space before it can
be unpublished. Creating a report from inside a space publishes it as part of the create. This is why a
space never holds a report that some of its viewers cannot open — and why a space can always be emptied
by whoever can see it.

## The space overview

A space can nominate one of its reports as its **overview** — the report shown when you open the
space. It is a pointer, nothing more: the nominated report stays an ordinary report that can be
edited, cloned, published, moved, or deleted like any other, with no special rules attached to it.

Set it from the star action next to any report in the space sidebar, or clear it from the same menu.
Requires `spaces:write`.

Having no overview is a normal state, and it is what a new space starts in. The detail page then shows
the space name and prompts you to pick one.

The pointer is resolved **lazily**, which is what keeps the nominated report ordinary. If it is moved
out of the space or deleted, the space simply reads as having no overview — nothing needs to be cleaned
up, and nothing about the report is restricted to prevent it.

Only `GET /api/v1/spaces/<id>/tree` reports the pointer, because only it has the caller's report list
to resolve it against — plus `PUT /api/v1/spaces/<id>/overview`, which echoes back what the caller just
set. `GET /api/v1/spaces` and `GET /api/v1/spaces/<id>` always return `overview_report_id: null`.

## Managing spaces

### Creating a space

Open **Spaces** in the sidebar and choose **New space**. Supply a name and an optional description.
The space starts empty, with no reports and no overview.

Names are checked for duplicates at creation and rename time, matched **exactly** — surrounding
whitespace is trimmed first, but case is significant, so `Security` and `security` can coexist. The
check is best-effort: it is not backed by a database constraint, so two simultaneous creates can still
produce two spaces with the same name.

Exact matching is also what lets `seizu seed` decide whether a YAML space already exists without
reimplementing the server's rule — see [Seeding](#seeding).

### Editing a space

The **Edit** row action renames a space and updates its description. Renaming a space does not touch
its reports.

### Deleting a space

Deleting is blocked while a space still holds reports. Move them out first; then the space and its
sub-spaces are removed. A blocked delete returns 409 and the reason is shown in the confirmation
dialog.

**Deleting a space never deletes a report.** Sub-spaces do not block the delete either — they are only
grouping labels, and once no reports remain nothing references them.

Emptiness is evaluated across *all* reports in the space, without the deleting user's visibility
filter. Members are public, so this is belt and braces rather than load-bearing — but it means the
check can never read a space as empty when it is not.

## Managing sub-spaces

Sub-spaces exist only as grouping labels inside the space detail page's sidebar. They have no detail
page, no description, and no content of their own.

- **Create** one with **New sub-space** in the space sidebar's footer.
- **Rename** or **delete** one from the menu on its heading.
- Deleting a sub-space keeps its reports in the space; they move to the ungrouped list at the top.
- Deleting the space removes its sub-spaces along with it.

An empty sub-space still shows its heading so it stays manageable.

## Filing reports into a space

From the **Reports** list, use **Move to space…** on any report. From inside a report, the same action
is in the overflow menu next to **Edit Report**. From inside a space, **New report** in the sidebar's
footer creates a report already filed in that space — published, since space members are public.

**Move to space…** is disabled on a draft, with a tooltip saying to publish it first, and the Reports
list's bulk **Move to space** is disabled while the selection contains any draft. **Unpublish** is
disabled on a report that is in a space.

The dialog has two selects. The sub-space select stays disabled until a space is chosen and resets
whenever the space changes — matching the API rule that a sub-space must belong to the chosen space.

**Moving a report to a different space clears its sub-space** unless you pick a new one in the same
action. The API uses replace semantics: the request describes the report's desired final state rather
than a partial update.

Within a space, the sidebar's per-report menu offers **Set as space overview**, **Move to
sub-space…** and **Remove from space**.

Cloning a report inside a space produces a clone in the same space and sub-space.

## The space detail page

`/app/spaces/<space_id>` renders the space's overview report, if one is set. The sidebar is headed by
the space name and lists every report in it — ungrouped first, then one group per sub-space — with the
overview report marked by a star. Its footer holds **New sub-space**, **New report**, and **Go to all
reports**.

Selecting a report navigates to `/app/spaces/<space_id>/reports/<report_id>`, so in-space reports are
deep-linkable and browser back works. Opening a report that is not in the space shows an explicit
message rather than silently redirecting.

## Permissions

| Permission | Grants | Built-in role |
|---|---|---|
| `spaces:read` | List and view spaces and their trees | Viewer and above |
| `spaces:write` | Create and rename spaces and sub-spaces, and set the overview | Editor and above |
| `spaces:delete` | Delete spaces and sub-spaces | Editor and above |

Sub-spaces deliberately reuse the `spaces:*` permissions rather than having their own — unlike a tool
inside a toolset, a sub-space is a label with no independently callable surface.

Filing a report into a space requires only `reports:write`. Filing is a report edit, and since spaces
are globally visible there is nothing to leak by letting any report author choose a space.

A `spaces:write` holder without `reports:write` can still pin and clear the overview: the sidebar's
per-report menu opens for either permission and disables the actions the caller lacks.

## API

```
GET    /api/v1/spaces
POST   /api/v1/spaces
GET    /api/v1/spaces/<space_id>
PUT    /api/v1/spaces/<space_id>
DELETE /api/v1/spaces/<space_id>
PUT    /api/v1/spaces/<space_id>/overview
GET    /api/v1/spaces/<space_id>/tree
GET    /api/v1/spaces/<space_id>/subspaces
POST   /api/v1/spaces/<space_id>/subspaces
PUT    /api/v1/spaces/<space_id>/subspaces/<subspace_id>
DELETE /api/v1/spaces/<space_id>/subspaces/<subspace_id>
PUT    /api/v1/reports/<report_id>/space
```

`GET .../tree` returns the space, its sub-spaces, and the reports visible to the caller in one
response — the space detail page's single fetch. Dangling references are blanked to `null` in that
response: a `subspace_id` whose sub-space is gone, and an `overview_report_id` that is not among the
returned reports. Along with `PUT .../overview` below, it is the only endpoint that reports the pointer:
the list and get endpoints return `null` for it, since without a report list there is nothing to
resolve it against.

`PUT .../overview` takes `{"report_id": ...}`; the target must be a report filed in that space, and
`null` clears the pointer. Its response echoes `overview_report_id` — the value the caller just set —
which is why it is the one non-tree endpoint that reports the pointer.

Spaces are not versioned, so there are no `/versions` endpoints.

`PUT /api/v1/reports/<report_id>/space` takes `{"space_id": ..., "subspace_id": ...}`. Both fields
describe the desired final state; omitting `subspace_id` clears it. An invalid pairing (a sub-space
with no space, an unknown space, or a sub-space belonging to a different space) returns 400. Filing a
private report returns **409** — publish it first. Removing a report from a space (`space_id: null`) is
always allowed, whatever its visibility.

`PUT /api/v1/reports/<report_id>/visibility` returns **409** when it would make a report private while
it is filed in a space. `POST /api/v1/reports` and `POST /api/v1/reports/<id>/clone` create a public
report when the new report lands in a space, and a draft otherwise. The same rules apply to the
`reports__create`, `reports__clone`, and `reports__update_visibility` MCP tools. In chat, creating into
a space asks for confirmation first, and cloning always does.

## CLI

```
seizu spaces list
seizu spaces show <space_id>
```

`show` prints the space along with its reports, their sub-space grouping, and which one is the
overview. `list` does not show the overview, because the list endpoint does not return the pointer.
Both accept `--output json`.

Spaces are created and organised from the web UI; the CLI commands are read-only, intended for
verifying a deployment's setup.

## MCP tools

The `spaces` built-in group exposes the same surface to MCP clients and the chat agent:

| Tool | Permission |
|---|---|
| `spaces__list`, `spaces__get`, `spaces__get_tree`, `spaces__list_subspaces` | `spaces:read` |
| `spaces__create`, `spaces__update`, `spaces__set_overview` | `spaces:write` |
| `spaces__create_subspace`, `spaces__update_subspace` | `spaces:write` |
| `spaces__delete`, `spaces__delete_subspace` | `spaces:delete` |
| `spaces__set_report_space` | `reports:write` |

They behave exactly like the REST endpoints below — the same validation code runs for both — and return
`{"error": ...}` where the route would return a 4xx. `spaces__get` and `spaces__list` blank the overview
pointer for the same reason the routes do; only `spaces__get_tree` and `spaces__set_overview` report it.

Filing a report keeps the route's permission (`reports:write` alone) but lives in the `spaces` group, so
disabling the group through `MCP_ENABLED_BUILTINS` removes the whole feature.

**Every mutating tool in the group is confirmation-gated in chat.** A space is a shared, globally visible
container: creating, renaming or deleting one is visible to every user, and filing a report into one
publishes it. None of them carry the "creates a private draft" exception `reports__create` relies on.

Spaces are not versioned, so unlike toolsets or reports the group has no `*_versions` tools.

## Seeding

Spaces round-trip through the YAML config. The top-level `spaces:` section declares them, and each
report names its placement:

```yaml
spaces:
  security:
    name: Security
    description: Vulnerability and posture reporting
    overview: security_overview
    subspaces:
      vulnerabilities:
        name: Vulnerabilities

reports:
  security_overview:
    name: Security Overview
    space: security
    rows: []

  open_cves:
    name: Open CVEs
    space: security
    subspace: vulnerabilities
    rows: []
```

The YAML keys (`security`, `vulnerabilities`) are local handles used to wire the sections together.
They never reach the API: space ids are server-generated, so the seeder matches spaces and sub-spaces
**by exact name**, exactly as it matches reports — and exactly how the API itself decides whether a
name is taken, so the seeder never claims a record the server would have let it create.

**Re-seeding an unchanged config writes nothing.** A report already filed where the YAML says, and an
overview already pointing at the right report, are both left alone: those endpoints stamp
`updated_at`/`updated_by`, so rewriting them every run would churn the audit trail for no change. Pass
`--force` to write regardless.

Three cross-references are validated when the file loads, before any writes happen — a typo fails the
whole seed rather than half of it:

- a report's `space` must be a key in `spaces`;
- a report's `subspace` must be defined in that space (and requires `space`);
- a space's `overview` must be a report that declares that same space.

`space` and `subspace` are seed metadata, like `pinned`. They are applied through
`PUT /api/v1/reports/<id>/space` after the report version is saved, and are **never** written into the
stored report config — otherwise restoring an old version would relocate the report.

Ordering is handled for you: spaces and sub-spaces are created first (filing needs the space to exist),
then reports, then membership, then the overview pointers (which need the report filed).

A report whose YAML omits `space` is **left where it is** rather than pulled out of its space — the same
"only act when the key is present" rule `pinned` follows. Removing a report from a space is a deliberate
act, so use the UI, the API, or `spaces__set_report_space`. Nothing is ever deleted by seeding: a space
dropped from the YAML is left alone.

`seizu export` writes the `spaces:` section, each report's `space`/`subspace`, and each space's
`overview`, so an exported config re-seeds to the same organisation. Existing keys in the file being
overwritten are reused, so hand-chosen names survive an export.

## Storage

Spaces and sub-spaces are stored in the report store alongside reports, in whichever backend
`REPORT_STORE_BACKEND` selects.

- **DynamoDB** — `SPACE#{id}` / `#METADATA` with a `SPACE_LIST` index entry, and
  `SUBSPACE#{id}` / `#METADATA` with a per-space `SUBSPACE_LIST#{space_id}` index entry. Reports carry
  `space_id` and `subspace_id` attributes; the overview pointer lives on the space.

  Listing a space's reports uses a global secondary index, **`space_reports_index`**, keyed
  `space_id` (hash) + `SK` (range) with an `ALL` projection. It is sparse — only items that carry a
  `space_id` appear — and queries add a `begins_with(SK, "REPORT#")` condition so they read only each
  report's `REPORT_LIST` copy, not its `#METADATA` copy or the sub-space items that also carry the
  attribute.

  When `DYNAMODB_CREATE_TABLE` is enabled the app creates the index, and adds it to an existing table
  on startup if it is missing. **Tables managed outside the app (the default, since
  `DYNAMODB_CREATE_TABLE` is `false`) need the index added to your Terraform or CloudFormation.**
  Until it exists, Seizu falls back to reading the whole report list and filtering in memory, logging
  `Space reports GSI unavailable`: correct results, but read capacity proportional to your *total*
  report count rather than the space's. Measured on a 51-report table, the index cut a space page from
  2.0 to 0.5 RCU, and the gap grows linearly.

  No backfill is needed when adding the index to a populated table: it keys on the `space_id`
  attribute that member reports already carry, so DynamoDB indexes them during the normal build.
- **PostgreSQL** — `spaces` and `subspaces` tables, plus `space_id` and `subspace_id` on `reports`.
  Added by the `0005_spaces` Alembic revision, which runs automatically at startup.

No configuration or feature flag is required; Spaces is always available.
