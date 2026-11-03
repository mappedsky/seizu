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

Set it with **Set as space overview** on any report's menu, in the space sidebar or on the contents
page, and clear it from the same menu. The overview is marked with an amber star. Requires
`spaces:write`.

Having no overview is a normal state, and it is what a new space starts in. Opening the space then
shows its contents page (see below) instead.

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

- **Create** one with **New sub-space** in the space sidebar's footer or on the contents page.
- **Rename** or **delete** one from the menu on its heading. The same menu offers **Add reports
  here…** and **New report here…**, which file straight into that sub-space.
- Deleting a sub-space keeps its reports in the space; they move to the top level.
- Deleting the space removes its sub-spaces along with it.

An empty sub-space still shows its heading so it stays manageable.

## Filing reports into a space

From inside a space, **Add reports** (in the sidebar's footer and on the contents page) opens a
picker listing every report you can see that is not already in the space, grouped as *Not in a
space*, *In another space* and *Drafts*. Search it, select several, choose a sub-space under **File
into**, and add them in one step:

- A report in another space **moves** here; the picker says which space it leaves.
- A **draft is published** as it is added, since space members are public; the picker warns before
  you commit. Only a draft's owner can publish it, so other people's drafts are listed but cannot be
  selected.
- If some reports fail, they stay selected with the reason shown, so a retry is one click.

**New report** creates a report already filed in the space (and, optionally, a sub-space) —
published, for the same reason.

From the **Reports** list, **Move to space…** on any report files it from the other direction. From
inside a report, the same action is in the overflow menu next to **Edit Report**.

On the Reports list, **Move to space…** is disabled on a draft, with a tooltip saying to publish it first, and the Reports
list's bulk **Move to space** is disabled while the selection contains any draft. **Unpublish** is
disabled on a report that is in a space.

The dialog has two selects. The sub-space select stays disabled until a space is chosen and resets
whenever the space changes — matching the API rule that a sub-space must belong to the chosen space.

**Moving a report to a different space clears its sub-space** unless you pick a new one in the same
action. The API uses replace semantics: the request describes the report's desired final state rather
than a partial update.

### Moving reports between sub-spaces

Within a space there are three ways to regroup reports, all needing `reports:write`:

- **Drag** a report onto a sub-space. On the contents page, drag by the grip at the left of the
  row; in the sidebar, drag the row itself. Every sub-space that would accept the report is outlined
  while you drag, and the top level appears as a target even when it is empty. Dragging a selected
  row on the contents page moves the whole selection. From the keyboard, focus a row's grip, press
  Space or Enter, use the arrow keys to step between sub-spaces, and press Space or Enter to drop
  (Escape cancels).
- **Select** reports on the contents page — row checkboxes, or the checkbox on a sub-space heading
  for all of its reports — and use **Move to…** or **Remove from space** in the bar that appears.
- **Each report's menu** names every other sub-space directly (**Move to Network**, **Move to top
  level**). A space with more than six sub-spaces offers **Move to sub-space…**, which opens a list,
  instead.

A moved report shows in its new place immediately. The moves are then saved one at a time and the
space reloads once at the end; a report that could not be moved returns to where it was, with the
reason shown. Each report's menu also offers **Set as space overview** and **Remove from space**.

Cloning a report inside a space produces a clone in the same space and sub-space.

## The space detail page

`/app/spaces/<space_id>` renders the space's overview report, if one is set, and its contents page
otherwise. `/app/spaces/<space_id>/contents` always shows the contents page; the sidebar's header,
the space name, links to it.

The **contents page** carries the space's name and description (editable with **Edit**), its report
and sub-space counts, and every member report grouped by sub-space with its last update. Empty
sub-spaces show inline **Add reports** and **New report** actions, and an empty space explains what to
do first.

The sidebar lists every report in the space — top level first, then one group per sub-space — with
the overview report marked by a star. Its footer holds **Add reports**, **New report** and **New
sub-space**, each shown only to users who hold the permission it needs.

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

Spaces and sub-spaces are stored in PostgreSQL alongside reports. The
`spaces` and `subspaces` tables, plus `space_id` and `subspace_id` on
`reports`, were added by the `0005_spaces` Alembic revision. Alembic upgrades
the schema automatically at startup.

No configuration or feature flag is required; Spaces is always available.
