# Spaces decisions (`SPC`)

Decisions behind spaces, sub-spaces and the overview pointer. For usage and
configuration, see the [spaces install docs](../../install/spaces.md).

Primary code: `reporting/schema/space_config.py`,
`reporting/services/spaces.py`, `reporting/routes/spaces.py`,
`reporting/services/report_store/`.

## SPC-001 — Spaces are flat records with no version history and no access scope

**Applies to:** `reporting/schema/space_config.py`

No `/versions` endpoints, and `list_spaces` is unfiltered.

**Why:** a space is a container, not content. Report-level visibility still
filters what `list_space_reports` returns, so the thing worth protecting is
protected without a second access model to keep consistent.

## SPC-002 — A report filed in a space is public, enforced in the store

**Applies to:** `SPACE_MEMBER_ACCESS`, `require_public_space_member`

Filing a draft is a 409, unpublishing a member is a 409, and creating a report
into a space publishes it. All three writes are enforced **in the store**:
`require_public_space_member` on `create_report` and `SELECT ... FOR UPDATE` in
PostgreSQL.

**Why in the store:** the route-level check alone loses a concurrent
unpublish/file race. The store raises `SpaceConflictError` (defined in
`schema/space_config.py` so the store and services can share it), which routes
map to 409.

**Why the invariant at all:** a space holding a report nobody else can see would
both block the space's deletion invisibly and leak the report's ID through the
overview pointer.

## SPC-003 — Membership is unversioned parent metadata

**Applies to:** `update_report_space`

Reports carry nullable `space_id`/`subspace_id`, denormalized onto
`ReportVersion` but **never written into a version item or into `config`**.
Moves go through `update_report_space` — a metadata mutation like
`update_report_visibility`, not a version save — with **replace semantics**, so
omitting `subspace_id` clears it.

**Why:** if membership lived in the version, restoring an old version would
relocate the report. Replace semantics are also what makes "moving to another
space drops the sub-space" fall out for free.

## SPC-004 — The overview is a lazily resolved nullable pointer

**Applies to:** `space.overview_report_id`, `GET /spaces/{id}/tree`

The pointer targets one of the space's own reports and is set via
`PUT /spaces/{id}/overview`. The target stays an ordinary report with no
protections. Resolution happens in `GET /spaces/{id}/tree`, which blanks it when
the target is deleted, moved out, or invisible to the caller.

**Why lazy:** it removes the need for any invariant on the target report.
Nothing has to be enforced at write time because nothing downstream trusts the
pointer.

Only the tree and `PUT /spaces/{id}/overview` (which echoes what the caller just
set) expose it. `GET /spaces`, `GET /spaces/{id}` and `PUT /spaces/{id}` blank
it, because outside the tree there is no report list to resolve it against.

## SPC-005 — `delete_space` returns a result type, not a bool

**Applies to:** `SpaceDeleteResult`

A deliberate break from the `bool` delete convention.

**Why:** "no such space" (404) and "still has reports" (409) are different
answers and a bool cannot carry both. Emptiness is evaluated **without**
visibility filtering — belt and braces, since members are public anyway
(SPC-002).

Only reports block the delete. Sub-spaces cascade with the space, and **no
report is ever deleted with a space**. `delete_subspace` writes nothing to
member reports: their dangling `subspace_id` is normalized to `None` by the tree
endpoint, which beats an unbounded non-transactional fan-out.

## SPC-006 — Every mutating space tool in chat is confirmation-gated

**Applies to:** the `spaces` MCP builtin group

No `chat_safe_without_confirmation` exception, and a test asserts this so a new
tool cannot quietly opt out.

**Why:** a space is a shared, globally visible container, and filing publishes.

Filing a report is `spaces__set_report_space` — in the `spaces` group, so
disabling the group drops the whole feature, but keeping the route's
`reports:write` permission. REST and MCP share the helpers in
`services/spaces.py` (duplicate-name guards, `without_overview`,
`with_resolved_overview`, `with_resolved_subspace`) specifically so they cannot
diverge on which responses carry the pointer.

## SPC-007 — YAML matches spaces by name, and an omitted `space` never unfiles

**Applies to:** `seizu_schema/reporting_config.py`, the seed path

Spaces and sub-spaces are matched **by name** on seed, like reports, since ids
are server-generated; YAML keys are local handles. The three cross-references
(report→space, report→sub-space, space overview→report filed in it) are
validated by a `ReportingConfig` model validator at load, so a typo fails before
any write.

`space`/`subspace` are seed metadata like `pinned`: applied via
`PUT /reports/{id}/space` after the version save and stripped from the stored
config (`exclude={"pinned", "space", "subspace"}`) — otherwise restoring a
version would relocate the report (SPC-003).

Seed order is spaces → reports → membership → overview. **An omitted `space`
never unfiles a report** (the `pinned` rule), and seeding deletes nothing.
