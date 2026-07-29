# Spaces

## Purpose

A Seizu deployment accumulates reports faster than a flat list can organise them. **Spaces** group
related reports — one per team, per programme, or per data source — and **sub-spaces** organise
reports within a space.

Both are optional. A report can sit in no space, in a space, or in a space and one of its sub-spaces;
a sub-space can never be set without a space.

Spaces are visible to everyone with `spaces:read`. They carry no access scope of their own, and the
reports listed inside a space are still filtered by each report's own visibility, so a private report
belonging to another user never appears.

## The overview report

Creating a space also creates a report named after it. This **overview report** is the space's landing
page, and is an ordinary report in every other respect: it appears in the global Reports list, can be
edited with the normal report editor, cloned, pinned, and set as the dashboard.

Four things it cannot do, because the space points at it:

- It cannot be moved to another space or removed from its own.
- It cannot be deleted directly — delete the space instead.
- It cannot be made private. Spaces are globally visible, so a private overview would leave every
  other user looking at an empty space.
- It cannot lose its overview status.

Attempting any of these returns HTTP 409 with an explanatory message.

## Managing spaces

### Creating a space

Open **Spaces** in the sidebar and choose **New space**. Supply a name and an optional description;
Seizu creates the space along with its overview report in a single write, then opens the new space.

Names are checked for duplicates case-insensitively at creation and rename time. The check is
best-effort — it is not backed by a database constraint, so two simultaneous creates can still produce
two spaces with the same name.

### Editing a space

The **Edit** row action renames a space and updates its description. Renaming a space does **not**
rename its overview report; edit that report directly if you want the names to stay in step.

### Deleting a space

Deleting is blocked while a space still holds member reports. Move them out first; then deleting the
space also deletes its overview report and any sub-spaces. A blocked delete returns 409 and the reason
is shown in the confirmation dialog.

Sub-spaces do not block the delete. They are only grouping labels, and once no member reports remain
nothing references them.

Emptiness is evaluated across *all* reports in the space, including ones the deleting user cannot see.
A space holding another user's private report cannot be deleted, which is what prevents that report
from being orphaned.

## Managing sub-spaces

Sub-spaces exist only as grouping labels inside the space detail page's sidebar. They have no detail
page, no description, and no content of their own.

- **Create** one with the **+** button at the top of the space sidebar.
- **Rename** or **delete** one from the menu on its heading.
- Deleting a sub-space keeps its reports in the space; they move to the ungrouped list at the top.
- Deleting the space removes its sub-spaces along with it.

An empty sub-space still shows its heading so it stays manageable.

## Filing reports into a space

From the **Reports** list, use **Move to space…** on any report. From inside a report, the same action
is in the overflow menu next to **Edit Report**.

The dialog has two selects. The sub-space select stays disabled until a space is chosen and resets
whenever the space changes — matching the API rule that a sub-space must belong to the chosen space.

**Moving a report to a different space clears its sub-space** unless you pick a new one in the same
action. The API uses replace semantics: the request describes the report's desired final state rather
than a partial update.

Within a space, the sidebar's per-report menu offers **Move to sub-space…** and **Remove from space**.

Cloning a report inside a space produces a clone in the same space and sub-space. A clone is never an
overview report.

## The space detail page

`/app/spaces/<space_id>` renders the overview report, with a sidebar listing the space's other
reports: ungrouped ones first, then one group per sub-space.

Selecting a report navigates to `/app/spaces/<space_id>/reports/<report_id>`, so in-space reports are
deep-linkable and browser back works. Opening a report that is not in the space shows an explicit
message rather than silently redirecting.

## Permissions

| Permission | Grants | Built-in role |
|---|---|---|
| `spaces:read` | List and view spaces and their trees | Viewer and above |
| `spaces:write` | Create and rename spaces and sub-spaces | Editor and above |
| `spaces:delete` | Delete spaces and sub-spaces | Editor and above |

Sub-spaces deliberately reuse the `spaces:*` permissions rather than having their own — unlike a tool
inside a toolset, a sub-space is a label with no independently callable surface.

Filing a report into a space requires only `reports:write`. Filing is a report edit, and since spaces
are globally visible there is nothing to leak by letting any report author choose a space.

## API

```
GET    /api/v1/spaces
POST   /api/v1/spaces
GET    /api/v1/spaces/<space_id>
PUT    /api/v1/spaces/<space_id>
DELETE /api/v1/spaces/<space_id>
GET    /api/v1/spaces/<space_id>/tree
GET    /api/v1/spaces/<space_id>/subspaces
POST   /api/v1/spaces/<space_id>/subspaces
PUT    /api/v1/spaces/<space_id>/subspaces/<subspace_id>
DELETE /api/v1/spaces/<space_id>/subspaces/<subspace_id>
PUT    /api/v1/reports/<report_id>/space
```

`GET .../tree` returns the space, its sub-spaces, and the reports visible to the caller in one
response — the space detail page's single fetch.

Spaces are not versioned, so there are no `/versions` endpoints.

`PUT /api/v1/reports/<report_id>/space` takes `{"space_id": ..., "subspace_id": ...}`. Both fields
describe the desired final state; omitting `subspace_id` clears it. An invalid pairing (a sub-space
with no space, an unknown space, or a sub-space belonging to a different space) returns 400.

## CLI

```
seizu spaces list
seizu spaces show <space_id>
```

`show` prints the space along with its reports and their sub-space grouping. Both accept
`--output json`.

Spaces are created and organised from the web UI; the CLI commands are read-only, intended for
verifying a deployment's setup.

## Seeding

Spaces are **not** represented in the YAML config, so `seizu seed` neither creates nor updates them.

This has one consequence worth knowing before you rely on `seizu export`:

- Overview reports are skipped on export. They are created with their space, so exporting them as
  ordinary top-level reports would make the next seed create duplicate standalone copies.
- Space membership of ordinary reports is not exported either, so re-seeding an exported config will
  not restore which space a report was in.

`export` prints a warning for both cases. If you rely on YAML as the source of truth for a deployment,
treat space organisation as UI-managed state until a `spaces:` config section exists.

## Storage

Spaces and sub-spaces are stored in the report store alongside reports, in whichever backend
`REPORT_STORE_BACKEND` selects.

- **DynamoDB** — `SPACE#{id}` / `#METADATA` with a `SPACE_LIST` index entry, and
  `SUBSPACE#{id}` / `#METADATA` with a per-space `SUBSPACE_LIST#{space_id}` index entry. Reports carry
  `space_id`, `subspace_id`, and `space_overview` attributes.

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
- **PostgreSQL** — `spaces` and `subspaces` tables, plus three columns on `reports`. Added by the
  `0005_spaces` Alembic revision, which runs automatically at startup.

No configuration or feature flag is required; Spaces is always available.
