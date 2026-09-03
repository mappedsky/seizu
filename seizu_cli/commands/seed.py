"""seed / export commands — bulk-load or dump YAML config via the Seizu API."""

import re
import sys
from pathlib import Path
from typing import Any, NamedTuple

from rich.console import Console

from seizu_cli import schema, state
from seizu_cli.client import APIError
from seizu_cli.plugin_package import build_plugin_package

console = Console()
err_console = Console(stderr=True)

SEED_COMMENT = "Imported from YAML dashboard config"
SEED_UPDATE_COMMENT = "Updated from YAML dashboard config"

#: Built-in MCP toolsets surface through ``/api/v1/toolsets`` as synthetic rows
#: with ids like ``__builtin_graph__``. They ship with the application and the
#: write path rejects the prefix outright, so they must not reach the YAML —
#: their ids do not even satisfy the lower_snake_case key validators. Matched by
#: prefix here rather than importing ``mcp_builtins.synthetic``: the CLI is
#: published as its own package and must not depend on backend code.
BUILTIN_ID_PREFIX = "__builtin_"


def _slugify(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_") or "report"


def _die(exc: Exception) -> None:
    if isinstance(exc, APIError):
        err_console.print(f"[red]Error {exc.status_code}[/red]: {exc}")
    else:
        err_console.print(f"[red]Error[/red]: {exc}")
    sys.exit(1)


def _list_reports() -> list[dict[str, Any]]:
    return state.get_client().get("/api/v1/reports").get("reports", [])


def _publish_report(report_id: str) -> None:
    """Make a seeded report public, unless this identity does not own it.

    A report's access is its owner's to change, and durable identity is
    ``(iss, sub)`` -- so a report seeded while the auth profile was on cannot be
    published by the local dev identity afterwards, and the server is right to
    refuse. That is not a reason to abandon the run: seeding is additive, the
    report keeps the visibility it already has, and everything after reports --
    toolsets, skillsets, workflows, scheduled queries -- still needs to happen.
    One report nobody can republish used to stop all of it.
    """
    try:
        state.get_client().put(f"/api/v1/reports/{report_id}/visibility", json={"access": {"scope": "public"}})
    except APIError as exc:
        if exc.status_code != 403:
            raise
        err_console.print(
            f"[yellow]Skipped[/yellow]: report {report_id} is owned by another identity, "
            "so its visibility was left as it is. Seeding continues."
        )


def _pin_report(report_id: str, pinned: bool) -> None:
    state.get_client().put(f"/api/v1/reports/{report_id}/pin", json={"pinned": pinned})


def _get_report(report_id: str) -> dict[str, Any] | None:
    try:
        return state.get_client().get(f"/api/v1/reports/{report_id}")
    except APIError as exc:
        if exc.status_code == 404:
            return None
        raise


def _is_builtin_id(value: str) -> bool:
    return value.startswith(BUILTIN_ID_PREFIX)


def _list_spaces() -> list[dict[str, Any]]:
    return state.get_client().get("/api/v1/spaces").get("spaces", [])


def _list_subspaces(space_id: str) -> list[dict[str, Any]]:
    return state.get_client().get(f"/api/v1/spaces/{space_id}/subspaces").get("subspaces", [])


def _space_tree(space_id: str) -> dict[str, Any]:
    return state.get_client().get(f"/api/v1/spaces/{space_id}/tree")


def _set_report_space(report_id: str, space_id: str, subspace_id: str | None) -> None:
    state.get_client().put(
        f"/api/v1/reports/{report_id}/space",
        json={"space_id": space_id, "subspace_id": subspace_id},
    )


def _set_space_overview(space_id: str, report_id: str) -> None:
    state.get_client().put(f"/api/v1/spaces/{space_id}/overview", json={"report_id": report_id})


def _space_content_changed(existing: dict[str, Any], name: str, description: str) -> bool:
    return existing.get("name") != name or existing.get("description", "") != description


def _list_scheduled_queries() -> list[dict[str, Any]]:
    return state.get_client().get("/api/v1/scheduled-queries").get("scheduled_queries", [])


def _list_workflows() -> list[dict[str, Any]]:
    return state.get_client().get("/api/v1/workflows").get("workflows", [])


def _sq_content_changed(
    existing: dict[str, Any],
    resolved_cypher: str,
    params: list[dict[str, Any]],
    frequency: int | None,
    schedule: dict[str, Any] | None,
    watch_scans: list[dict[str, Any]],
    enabled: bool,
    actions: list[dict[str, Any]],
) -> bool:
    return (
        existing.get("cypher") != resolved_cypher
        or existing.get("params") != params
        or existing.get("frequency") != frequency
        or existing.get("schedule") != schedule
        or existing.get("watch_scans") != watch_scans
        or existing.get("enabled", True) != enabled
        or existing.get("actions") != actions
    )


def _list_toolsets() -> list[dict[str, Any]]:
    return state.get_client().get("/api/v1/toolsets").get("toolsets", [])


def _list_tools(toolset_id: str) -> list[dict[str, Any]]:
    return state.get_client().get(f"/api/v1/toolsets/{toolset_id}/tools").get("tools", [])


def _list_skillsets() -> list[dict[str, Any]]:
    return state.get_client().get("/api/v1/skillsets").get("skillsets", [])


def _list_plugins() -> list[dict[str, Any]]:
    return state.get_client().get("/api/v1/plugins").get("plugins", [])


def _list_model_profiles() -> list[dict[str, Any]]:
    return state.get_client().get("/api/v1/model-profiles").get("profiles", [])


def _list_skills(skillset_id: str) -> list[dict[str, Any]]:
    return state.get_client().get(f"/api/v1/skillsets/{skillset_id}/skills").get("skills", [])


def _toolset_content_changed(
    existing: dict[str, Any],
    name: str,
    description: str,
    enabled: bool,
) -> bool:
    return (
        existing.get("name") != name
        or existing.get("description", "") != description
        or existing.get("enabled", True) != enabled
    )


def _tool_content_changed(
    existing: dict[str, Any],
    name: str,
    description: str,
    cypher: str,
    parameters: list[dict[str, Any]],
    enabled: bool,
) -> bool:
    return (
        existing.get("name") != name
        or existing.get("description", "") != description
        or existing.get("cypher") != cypher
        or existing.get("parameters", []) != parameters
        or existing.get("enabled", True) != enabled
    )


def _skillset_content_changed(
    existing: dict[str, Any],
    name: str,
    description: str,
    enabled: bool,
) -> bool:
    return (
        existing.get("name") != name
        or existing.get("description", "") != description
        or existing.get("enabled", True) != enabled
    )


def _skill_content_changed(
    existing: dict[str, Any],
    name: str,
    description: str,
    template: str,
    parameters: list[dict[str, Any]],
    triggers: list[str],
    tools_required: list[str],
    enabled: bool,
) -> bool:
    return (
        existing.get("name") != name
        or existing.get("description", "") != description
        or existing.get("template") != template
        or existing.get("parameters", []) != parameters
        or existing.get("triggers", []) != triggers
        or existing.get("tools_required", []) != tools_required
        or existing.get("enabled", True) != enabled
    )


def seed_cmd(config: str, force: bool, dry_run: bool) -> None:
    """Seed application configuration and Agent Plugin packages from YAML."""
    loaded = schema.load_file(config)

    if (
        not loaded.reports
        and not loaded.workflows
        and not loaded.scheduled_queries
        and not loaded.toolsets
        and not loaded.skillsets
        and not loaded.plugins
        and not loaded.spaces
        and not loaded.model_profiles
    ):
        console.print(
            "No model profiles, spaces, reports, workflows, toolsets, skillsets, or plugins found in config file. "
            "Nothing to do."
        )
        return

    # Profiles precede every resource that may capture one in a future schema.
    console.print("Seeding model profiles...")
    _seed_model_profiles(loaded, force=force, dry_run=dry_run)
    console.print("")

    # Spaces first: filing a report needs its space to already exist.
    console.print("Seeding spaces...")
    seeded_spaces = _seed_spaces(loaded, force=force, dry_run=dry_run)
    console.print("")

    try:
        existing_list = _list_reports()
    except Exception as exc:
        _die(exc)
        return

    existing_by_name: dict[str, dict[str, Any]] = {r["name"]: r for r in existing_list}
    # Space membership is read from this snapshot rather than re-fetched: the
    # report pass never changes it, so it is still current when filing runs.
    existing_by_id: dict[str, dict[str, Any]] = {r["report_id"]: r for r in existing_list}

    created = updated = skipped = 0
    seeded_ids: dict[str, str] = {}

    for report_key, report in loaded.reports.items():
        # pinned/space/subspace are parent metadata the seeder applies through
        # their own endpoints; none of them belong inside a stored version.
        report_config_dict = report.model_dump(exclude_none=True, exclude={"pinned", "space", "subspace"})
        existing = existing_by_name.get(report.name)

        if existing:
            if not force:
                latest = _get_report(existing["report_id"])
                if latest and latest.get("config") == report_config_dict:
                    try:
                        if existing.get("access", {}).get("scope") != "public":
                            _publish_report(existing["report_id"])
                        if report.pinned is not None and existing.get("pinned") != report.pinned:
                            _pin_report(existing["report_id"], report.pinned)
                    except Exception as exc:
                        _die(exc)
                        return
                    console.print(f"[dim][skip][/dim] '{report.name}' (config unchanged)")
                    skipped += 1
                    seeded_ids[report_key] = existing["report_id"]
                    continue

            if dry_run:
                console.print(f"[yellow][dry-run][/yellow] would update report '{report.name}' (key: {report_key})")
                updated += 1
                seeded_ids[report_key] = existing["report_id"]
                continue

            try:
                state.get_client().post(
                    f"/api/v1/reports/{existing['report_id']}/versions",
                    json={"config": report_config_dict, "comment": SEED_UPDATE_COMMENT},
                )
                _publish_report(existing["report_id"])
                if report.pinned is not None:
                    _pin_report(existing["report_id"], report.pinned)
            except Exception as exc:
                _die(exc)
                return
            seeded_ids[report_key] = existing["report_id"]
            console.print(
                f"[blue][updated][/blue] '{existing['report_id']}'  name='{report.name}'  yaml_key='{report_key}'"
            )
            updated += 1
            continue

        if dry_run:
            console.print(f"[yellow][dry-run][/yellow] would create report '{report.name}' (key: {report_key})")
            created += 1
            continue

        try:
            new_report = state.get_client().post("/api/v1/reports", json={"name": report.name})
            state.get_client().post(
                f"/api/v1/reports/{new_report['report_id']}/versions",
                json={"config": report_config_dict, "comment": SEED_COMMENT},
            )
            _publish_report(new_report["report_id"])
            if report.pinned is not None:
                _pin_report(new_report["report_id"], report.pinned)
        except Exception as exc:
            _die(exc)
            return

        seeded_ids[report_key] = new_report["report_id"]
        console.print(
            f"[green][created][/green] '{new_report['report_id']}'  name='{report.name}'  yaml_key='{report_key}'"
        )
        created += 1

    if loaded.dashboard and not dry_run:
        dashboard_id = seeded_ids.get(loaded.dashboard)
        if dashboard_id:
            try:
                state.get_client().put(f"/api/v1/reports/{dashboard_id}/dashboard")
            except Exception as exc:
                _die(exc)
                return
            console.print(f"[green][dashboard][/green] set to '{dashboard_id}' (key: {loaded.dashboard})")
        else:
            msg = (
                f"[yellow][warn][/yellow] dashboard key '{loaded.dashboard}'"
                " was not seeded, dashboard pointer not updated"
            )
            console.print(msg)

    console.print(f"\nReports: created={created} updated={updated} skipped={skipped}")

    # Membership and the overview pointer are parent metadata applied after the
    # version save, never part of a report's config — restoring an old version
    # must not relocate a report. Both run last of the report pass: filing needs
    # the report to exist and be public, and an overview needs it filed.
    if loaded.spaces:
        console.print("\nFiling reports into spaces...")
        _apply_report_spaces(loaded, seeded_ids, seeded_spaces, existing_by_id, force=force, dry_run=dry_run)
        _apply_space_overviews(loaded, seeded_ids, seeded_spaces, force=force, dry_run=dry_run)

    if loaded.workflows:
        console.print("\nSeeding workflows...")
        _seed_workflows(loaded, force=force, dry_run=dry_run)
    else:
        console.print("\nSeeding scheduled queries (deprecated)...")
        _seed_scheduled_queries(loaded, force=force, dry_run=dry_run)

    console.print("\nSeeding toolsets...")
    _seed_toolsets(loaded, force=force, dry_run=dry_run)

    console.print("\nSeeding skillsets...")
    _seed_skillsets(loaded, force=force, dry_run=dry_run)

    console.print("\nSeeding plugins...")
    _seed_plugins(loaded, config_path=Path(config), force=force, dry_run=dry_run)

    if dry_run:
        console.print("\n(dry-run, no writes performed)")


def _seed_model_profiles(config: Any, force: bool, dry_run: bool) -> None:
    """Reconcile model profiles by their API-unique names.

    The YAML key is a local reference because profile ids are generated by the
    server. The desired default is processed first: selecting it atomically
    clears the old default, so later updates cannot transiently leave the store
    without an enabled default.
    """
    if not config.model_profiles:
        console.print("  No model profiles in config, skipping.")
        return

    try:
        existing_by_name = {item["name"]: item for item in _list_model_profiles()}
    except Exception as exc:
        _die(exc)
        return

    created = updated = skipped = 0
    definitions = sorted(
        config.model_profiles.items(),
        key=lambda item: (not item[1].is_default, item[0]),
    )
    comparable_fields = (
        "name",
        "description",
        "enabled",
        "is_default",
        "primary",
        "economy",
        "stage_overrides",
        "user_reasoning_efforts",
        "default_reasoning_effort",
        "run_cost_budget_usd",
    )

    for profile_key, definition in definitions:
        payload = definition.model_dump(mode="json")
        current = existing_by_name.get(definition.name)
        changed = current is None or force or any(current.get(key) != payload[key] for key in comparable_fields)

        if not changed:
            console.print(f"  [dim][skip][/dim] model profile '{definition.name}' (unchanged)")
            skipped += 1
            continue

        if dry_run:
            verb = "create" if current is None else "update"
            console.print(
                f"  [yellow][dry-run][/yellow] would {verb} model profile '{definition.name}' (key: {profile_key})"
            )
            created += current is None
            updated += current is not None
        else:
            try:
                if current is None:
                    result = state.get_client().post("/api/v1/model-profiles", json=payload)
                    profile_id = result["profile_id"]
                    created += 1
                else:
                    profile_id = current["profile_id"]
                    state.get_client().put(
                        f"/api/v1/model-profiles/{profile_id}",
                        json={**payload, "comment": SEED_UPDATE_COMMENT},
                    )
                    updated += 1
            except Exception as exc:
                _die(exc)
                return
            console.print(
                f"  [green][saved][/green] '{profile_id}'  name='{definition.name}'  yaml_key='{profile_key}'"
            )

        if definition.is_default:
            # Creating or updating the target causes this same transition in
            # the store. Keep the snapshot current so the old default is not
            # redundantly updated solely to clear its flag.
            for item in existing_by_name.values():
                item["is_default"] = item.get("name") == definition.name

    console.print(f"  Model profiles: created={created} updated={updated} skipped={skipped}")


class SeededSpaces(NamedTuple):
    """What the report pass needs to know after spaces have been seeded.

    ``space_ids`` / ``subspace_ids`` map YAML keys to server ids. A value is
    ``None`` when the id is not known, which happens only on a dry run for a
    record that would have been created — callers print rather than write.

    ``created_keys`` names the spaces this run created. They cannot already
    carry an overview pointer, so the overview pass can skip reading one.
    """

    space_ids: dict[str, str | None]
    subspace_ids: dict[tuple[str, str], str | None]
    created_keys: set[str]


def _seed_spaces(
    config: Any,
    force: bool,
    dry_run: bool,
) -> SeededSpaces:
    """Create or update the spaces and sub-spaces declared in *config*.

    Spaces are matched by name, like reports: their ids are server-generated
    snowflakes, so the YAML key is a local handle that never reaches the API.
    The match is exact, which is also how the API decides whether a name is
    taken — see ``find_duplicate_space_name``. Anything looser would have to be
    kept in step with the server by hand, and would make the seeder claim an
    existing record the API would have let it create.

    Nothing is ever deleted here; a space dropped from the YAML is left alone,
    matching how reports and toolsets are seeded.
    """
    space_ids: dict[str, str | None] = {}
    subspace_ids: dict[tuple[str, str], str | None] = {}
    created_keys: set[str] = set()
    seeded = SeededSpaces(space_ids, subspace_ids, created_keys)

    if not config.spaces:
        console.print("  No spaces in config, skipping.")
        return seeded

    try:
        existing_spaces = {item["name"]: item for item in _list_spaces()}
    except Exception as exc:
        _die(exc)
        return seeded

    created = updated = skipped = 0

    for space_key, space_def in config.spaces.items():
        description = space_def.description or ""
        existing = existing_spaces.get(space_def.name)

        if existing:
            space_id = existing["space_id"]
            space_ids[space_key] = space_id
            if not force and not _space_content_changed(existing, space_def.name, description):
                console.print(f"  [dim][skip][/dim] space '{space_def.name}' (unchanged)")
                skipped += 1
            elif dry_run:
                console.print(f"  [yellow][dry-run][/yellow] would update space '{space_def.name}' (key: {space_key})")
                updated += 1
            else:
                try:
                    state.get_client().put(
                        f"/api/v1/spaces/{space_id}",
                        json={"name": space_def.name, "description": description},
                    )
                except Exception as exc:
                    _die(exc)
                    return seeded
                console.print(f"  [blue][updated][/blue] '{space_id}'  name='{space_def.name}'  yaml_key='{space_key}'")
                updated += 1
        elif dry_run:
            console.print(f"  [yellow][dry-run][/yellow] would create space '{space_def.name}' (key: {space_key})")
            space_ids[space_key] = None
            created += 1
        else:
            try:
                result = state.get_client().post(
                    "/api/v1/spaces",
                    json={"name": space_def.name, "description": description},
                )
            except Exception as exc:
                _die(exc)
                return seeded
            space_ids[space_key] = result["space_id"]
            created_keys.add(space_key)
            console.print(
                f"  [green][created][/green] '{result['space_id']}'  name='{space_def.name}'  yaml_key='{space_key}'"
            )
            created += 1

        space_id = space_ids.get(space_key)
        if not space_def.subspaces:
            continue
        if space_id is None:
            for sub_key, sub_def in space_def.subspaces.items():
                console.print(
                    f"    [yellow][dry-run][/yellow] would create sub-space '{sub_def.name}' (key: {sub_key})"
                )
                subspace_ids[(space_key, sub_key)] = None
            continue

        existing_subspaces: dict[str, dict[str, Any]] = {}
        if existing:
            # A space this run just created has none, so only an existing space
            # is worth a round trip.
            try:
                existing_subspaces = {item["name"]: item for item in _list_subspaces(space_id)}
            except Exception as exc:
                _die(exc)
                return seeded

        for sub_key, sub_def in space_def.subspaces.items():
            existing_sub = existing_subspaces.get(sub_def.name)
            if existing_sub:
                # Sub-spaces hold nothing but a name, so a match by name is
                # always already up to date.
                subspace_ids[(space_key, sub_key)] = existing_sub["subspace_id"]
                console.print(f"    [dim][skip][/dim] sub-space '{sub_def.name}' (unchanged)")
                continue
            if dry_run:
                console.print(
                    f"    [yellow][dry-run][/yellow] would create sub-space '{sub_def.name}' (key: {sub_key})"
                )
                subspace_ids[(space_key, sub_key)] = None
                continue
            try:
                result = state.get_client().post(
                    f"/api/v1/spaces/{space_id}/subspaces",
                    json={"name": sub_def.name},
                )
            except Exception as exc:
                _die(exc)
                return seeded
            subspace_ids[(space_key, sub_key)] = result["subspace_id"]
            console.print(
                f"    [green][created][/green] '{result['subspace_id']}'  name='{sub_def.name}'  yaml_key='{sub_key}'"
            )

    console.print(f"  Spaces: created={created} updated={updated} skipped={skipped}")
    return seeded


def _apply_report_spaces(
    config: Any,
    seeded_ids: dict[str, str],
    spaces: SeededSpaces,
    existing_reports: dict[str, dict[str, Any]],
    force: bool,
    dry_run: bool,
) -> None:
    """File the seeded reports into the spaces their YAML declares.

    Runs after the report pass because filing needs the report to exist and to
    be public — the report pass publishes everything it touches.

    A report already filed where the YAML says is skipped: the filing endpoint
    is a metadata write that stamps ``updated_at``/``updated_by``, so writing it
    unconditionally would churn every filed report on every re-seed. The
    membership comes from the report list fetched before the report pass, which
    is why ``existing_reports`` is keyed by report id.

    A report whose YAML omits ``space`` is left where it is rather than being
    pulled out of a space, the same "only act when the key is present" rule
    ``pinned`` follows. Removing a report from a space is a deliberate act, and
    a config written before spaces existed should not perform one.
    """
    targets = [(key, report) for key, report in config.reports.items() if report.space is not None]
    if not targets:
        return

    filed = unchanged = 0
    for report_key, report in targets:
        space_id = spaces.space_ids.get(report.space)
        subspace_id = spaces.subspace_ids.get((report.space, report.subspace)) if report.subspace else None
        report_id = seeded_ids.get(report_key)
        location = f"{report.space}/{report.subspace}" if report.subspace else report.space

        if dry_run:
            console.print(f"  [yellow][dry-run][/yellow] would file report '{report.name}' into space '{location}'")
            filed += 1
            continue

        if report_id is None or space_id is None:
            console.print(f"  [yellow][warn][/yellow] report '{report.name}' was not seeded, not filed into a space")
            continue

        # A report this run created is not in the list, and has no membership.
        current = existing_reports.get(report_id, {})
        if not force and current.get("space_id") == space_id and current.get("subspace_id") == subspace_id:
            console.print(f"  [dim][skip][/dim] report '{report.name}' (already in space '{location}')")
            unchanged += 1
            continue

        try:
            _set_report_space(report_id, space_id, subspace_id)
        except Exception as exc:
            _die(exc)
            return
        console.print(f"  [green][filed][/green] report '{report.name}' → space '{location}'")
        filed += 1

    console.print(f"  Report space membership: filed={filed} unchanged={unchanged}")


def _apply_space_overviews(
    config: Any,
    seeded_ids: dict[str, str],
    spaces: SeededSpaces,
    force: bool,
    dry_run: bool,
) -> None:
    """Point each space at its overview report.

    Last, because the target has to exist and be filed in the space first.

    A pointer that already resolves to the right report is left alone, so a
    re-seed does not restamp every configured space. Reading it costs a tree
    fetch, because the tree is the only endpoint that reports the pointer — a
    read in place of a write, and only for a space that declares an overview and
    was not created by this run.
    """
    for space_key, space_def in config.spaces.items():
        if space_def.overview is None:
            continue
        space_id = spaces.space_ids.get(space_key)
        report_id = seeded_ids.get(space_def.overview)

        if dry_run:
            console.print(
                f"  [yellow][dry-run][/yellow] would set overview of space '{space_key}' to '{space_def.overview}'"
            )
            continue
        if space_id is None or report_id is None:
            console.print(
                f"  [yellow][warn][/yellow] overview report '{space_def.overview}' for space"
                f" '{space_key}' was not seeded, overview not set"
            )
            continue

        # A space this run created cannot already have a pointer, so skip the read.
        if not force and space_key not in spaces.created_keys:
            try:
                current = (_space_tree(space_id).get("space") or {}).get("overview_report_id")
            except Exception as exc:
                _die(exc)
                return
            if current == report_id:
                console.print(f"  [dim][skip][/dim] space '{space_key}' overview (unchanged)")
                continue

        try:
            _set_space_overview(space_id, report_id)
        except Exception as exc:
            _die(exc)
            return
        console.print(f"  [green][overview][/green] space '{space_key}' → report '{space_def.overview}'")


def _seed_scheduled_queries(
    config: Any,
    force: bool,
    dry_run: bool,
) -> None:
    if not config.scheduled_queries:
        console.print("  No scheduled queries in config, skipping.")
        return

    try:
        existing_items: dict[str, dict[str, Any]] = {item["name"]: item for item in _list_scheduled_queries()}
    except Exception as exc:
        _die(exc)
        return

    created = updated = skipped = 0

    for sq in config.scheduled_queries:
        resolved_cypher: str = config.queries.get(sq.cypher, sq.cypher)
        params = [p.model_dump() for p in sq.params]
        watch_scans = [ws.model_dump() for ws in sq.watch_scans]
        actions = [a.model_dump() for a in sq.actions]
        enabled = sq.enabled if sq.enabled is not None else True
        frequency: int | None = sq.frequency
        schedule: dict[str, Any] | None = sq.schedule.model_dump() if sq.schedule else None

        existing = existing_items.get(sq.name)

        if existing:
            changed = force or _sq_content_changed(
                existing,
                resolved_cypher,
                params,
                frequency,
                schedule,
                watch_scans,
                enabled,
                actions,
            )
            if not changed:
                console.print(f"  [dim][skip][/dim] scheduled query '{sq.name}' (unchanged)")
                skipped += 1
                continue
            if dry_run:
                console.print(f"  [yellow][dry-run][/yellow] would update scheduled query '{sq.name}'")
                updated += 1
                continue
            try:
                state.get_client().put(
                    f"/api/v1/scheduled-queries/{existing['scheduled_query_id']}",
                    json={
                        "name": sq.name,
                        "cypher": resolved_cypher,
                        "params": params,
                        "frequency": frequency,
                        "schedule": schedule,
                        "watch_scans": watch_scans,
                        "enabled": enabled,
                        "actions": actions,
                        "comment": SEED_UPDATE_COMMENT,
                    },
                )
            except Exception as exc:
                _die(exc)
                return
            console.print(f"  [blue][updated][/blue] '{existing['scheduled_query_id']}'  name='{sq.name}'")
            updated += 1
            continue

        if dry_run:
            console.print(f"  [yellow][dry-run][/yellow] would create scheduled query '{sq.name}'")
            created += 1
            continue

        try:
            result = state.get_client().post(
                "/api/v1/scheduled-queries",
                json={
                    "name": sq.name,
                    "cypher": resolved_cypher,
                    "params": params,
                    "frequency": frequency,
                    "schedule": schedule,
                    "watch_scans": watch_scans,
                    "enabled": enabled,
                    "actions": actions,
                },
            )
        except Exception as exc:
            _die(exc)
            return
        console.print(f"  [green][created][/green] '{result['scheduled_query_id']}'  name='{sq.name}'")
        created += 1

    console.print(f"  Scheduled queries: created={created} updated={updated} skipped={skipped}")


def _seed_workflows(config: Any, force: bool, dry_run: bool) -> None:
    try:
        existing = {item["name"]: item for item in _list_workflows()}
    except Exception as exc:
        _die(exc)
        return
    created = updated = skipped = 0
    for definition in config.workflows:
        payload = definition.model_dump()
        for stage in payload["stages"]:
            for activity in stage["activities"]:
                if activity["type"] == "query":
                    cypher = activity["parameters"].get("cypher", "")
                    activity["parameters"]["cypher"] = config.queries.get(cypher, cypher)
        current = existing.get(definition.name)
        comparable = {
            key: payload[key]
            for key in (
                "name",
                "schedule",
                "watch_scans",
                "enabled",
                "stages",
                "trigger_workflows",
            )
        }
        changed = force or current is None or any(current.get(key) != value for key, value in comparable.items())
        if not changed:
            console.print(f"  [dim][skip][/dim] workflow '{definition.name}' (unchanged)")
            skipped += 1
            continue
        if dry_run:
            verb = "create" if current is None else "update"
            console.print(f"  [yellow][dry-run][/yellow] would {verb} workflow '{definition.name}'")
            created += current is None
            updated += current is not None
            continue
        try:
            if current is None:
                result = state.get_client().post("/api/v1/workflows", json=payload)
                created += 1
                workflow_id = result["workflow_id"]
            else:
                payload["comment"] = SEED_UPDATE_COMMENT
                workflow_id = current["workflow_id"]
                state.get_client().put(f"/api/v1/workflows/{workflow_id}", json=payload)
                updated += 1
        except Exception as exc:
            _die(exc)
            return
        console.print(f"  [green][saved][/green] '{workflow_id}'  name='{definition.name}'")
    console.print(f"  Workflows: created={created} updated={updated} skipped={skipped}")


def _seed_toolsets(
    config: Any,
    force: bool,
    dry_run: bool,
) -> None:
    if not config.toolsets:
        console.print("  No toolsets in config, skipping.")
        return

    try:
        existing_toolsets: dict[str, dict[str, Any]] = {item["toolset_id"]: item for item in _list_toolsets()}
    except Exception as exc:
        _die(exc)
        return

    ts_created = ts_updated = ts_skipped = 0

    for ts_key, ts_def in config.toolsets.items():
        existing_ts = existing_toolsets.get(ts_key)
        description = ts_def.description or ""
        enabled = ts_def.enabled if ts_def.enabled is not None else True

        if existing_ts:
            changed = force or _toolset_content_changed(existing_ts, ts_def.name, description, enabled)
            if not changed:
                console.print(f"  [dim][skip][/dim] toolset '{ts_def.name}' (unchanged)")
                ts_skipped += 1
                toolset_id = existing_ts["toolset_id"]
            elif dry_run:
                console.print(f"  [yellow][dry-run][/yellow] would update toolset '{ts_def.name}' (key: {ts_key})")
                ts_updated += 1
                toolset_id = existing_ts["toolset_id"]
            else:
                try:
                    state.get_client().put(
                        f"/api/v1/toolsets/{existing_ts['toolset_id']}",
                        json={
                            "name": ts_def.name,
                            "description": description,
                            "enabled": enabled,
                            "comment": SEED_UPDATE_COMMENT,
                        },
                    )
                except Exception as exc:
                    _die(exc)
                    return
                console.print(
                    f"  [blue][updated][/blue] '{existing_ts['toolset_id']}'  name='{ts_def.name}'  yaml_key='{ts_key}'"
                )
                ts_updated += 1
                toolset_id = existing_ts["toolset_id"]
        elif dry_run:
            console.print(f"  [yellow][dry-run][/yellow] would create toolset '{ts_def.name}' (key: {ts_key})")
            ts_created += 1
            toolset_id = None
        else:
            try:
                result = state.get_client().post(
                    "/api/v1/toolsets",
                    json={
                        "toolset_id": ts_key,
                        "name": ts_def.name,
                        "description": description,
                        "enabled": enabled,
                    },
                )
            except Exception as exc:
                _die(exc)
                return
            console.print(
                f"  [green][created][/green] '{result['toolset_id']}'  name='{ts_def.name}'  yaml_key='{ts_key}'"
            )
            ts_created += 1
            toolset_id = ts_key

        if toolset_id is None or not ts_def.tools:
            continue

        try:
            existing_tools: dict[str, dict[str, Any]] = {t["tool_id"]: t for t in _list_tools(toolset_id)}
        except Exception as exc:
            _die(exc)
            return

        for tool_key, tool_def in ts_def.tools.items():
            tool_description = tool_def.description or ""
            tool_enabled = tool_def.enabled if tool_def.enabled is not None else True
            tool_params = [p.model_dump() for p in tool_def.parameters]
            existing_tool = existing_tools.get(tool_key)

            if existing_tool:
                tool_changed = force or _tool_content_changed(
                    existing_tool,
                    tool_def.name,
                    tool_description,
                    tool_def.cypher,
                    tool_params,
                    tool_enabled,
                )
                if not tool_changed:
                    console.print(f"    [dim][skip][/dim] tool '{tool_def.name}' (unchanged)")
                elif dry_run:
                    console.print(
                        f"    [yellow][dry-run][/yellow] would update tool '{tool_def.name}' (key: {tool_key})"
                    )
                else:
                    try:
                        state.get_client().put(
                            f"/api/v1/toolsets/{toolset_id}/tools/{existing_tool['tool_id']}",
                            json={
                                "name": tool_def.name,
                                "description": tool_description,
                                "cypher": tool_def.cypher,
                                "parameters": tool_params,
                                "enabled": tool_enabled,
                                "comment": SEED_UPDATE_COMMENT,
                            },
                        )
                    except Exception as exc:
                        _die(exc)
                        return
                    console.print(
                        f"    [blue][updated][/blue] '{existing_tool['tool_id']}'"
                        f"  name='{tool_def.name}'  yaml_key='{tool_key}'"
                    )
            elif dry_run:
                console.print(f"    [yellow][dry-run][/yellow] would create tool '{tool_def.name}' (key: {tool_key})")
            else:
                try:
                    result = state.get_client().post(
                        f"/api/v1/toolsets/{toolset_id}/tools",
                        json={
                            "tool_id": tool_key,
                            "name": tool_def.name,
                            "description": tool_description,
                            "cypher": tool_def.cypher,
                            "parameters": tool_params,
                            "enabled": tool_enabled,
                        },
                    )
                except Exception as exc:
                    _die(exc)
                    return
                console.print(
                    f"    [green][created][/green] '{result['tool_id']}'  name='{tool_def.name}'  yaml_key='{tool_key}'"
                )

    console.print(f"  Toolsets: created={ts_created} updated={ts_updated} skipped={ts_skipped}")


def _seed_skillsets(
    config: Any,
    force: bool,
    dry_run: bool,
) -> None:
    if not config.skillsets:
        console.print("  No skillsets in config, skipping.")
        return

    try:
        existing_skillsets: dict[str, dict[str, Any]] = {item["skillset_id"]: item for item in _list_skillsets()}
    except Exception as exc:
        _die(exc)
        return

    ss_created = ss_updated = ss_skipped = 0

    for ss_key, ss_def in config.skillsets.items():
        existing_ss = existing_skillsets.get(ss_key)
        description = ss_def.description or ""
        enabled = ss_def.enabled if ss_def.enabled is not None else True

        if existing_ss:
            changed = force or _skillset_content_changed(existing_ss, ss_def.name, description, enabled)
            if not changed:
                console.print(f"  [dim][skip][/dim] skillset '{ss_def.name}' (unchanged)")
                ss_skipped += 1
                skillset_id = existing_ss["skillset_id"]
            elif dry_run:
                console.print(f"  [yellow][dry-run][/yellow] would update skillset '{ss_def.name}' (key: {ss_key})")
                ss_updated += 1
                skillset_id = existing_ss["skillset_id"]
            else:
                try:
                    state.get_client().put(
                        f"/api/v1/skillsets/{existing_ss['skillset_id']}",
                        json={
                            "name": ss_def.name,
                            "description": description,
                            "enabled": enabled,
                            "comment": SEED_UPDATE_COMMENT,
                        },
                    )
                except Exception as exc:
                    _die(exc)
                    return
                console.print(
                    f"  [blue][updated][/blue] '{existing_ss['skillset_id']}'"
                    f"  name='{ss_def.name}'  yaml_key='{ss_key}'"
                )
                ss_updated += 1
                skillset_id = existing_ss["skillset_id"]
        elif dry_run:
            console.print(f"  [yellow][dry-run][/yellow] would create skillset '{ss_def.name}' (key: {ss_key})")
            ss_created += 1
            skillset_id = None
        else:
            try:
                state.get_client().post(
                    "/api/v1/skillsets",
                    json={
                        "skillset_id": ss_key,
                        "name": ss_def.name,
                        "description": description,
                        "enabled": enabled,
                    },
                )
            except Exception as exc:
                _die(exc)
                return
            console.print(f"  [green][created][/green] '{ss_key}'  name='{ss_def.name}'  yaml_key='{ss_key}'")
            ss_created += 1
            skillset_id = ss_key

        if skillset_id is None or not ss_def.skills:
            continue

        try:
            existing_skills: dict[str, dict[str, Any]] = {s["skill_id"]: s for s in _list_skills(skillset_id)}
        except Exception as exc:
            _die(exc)
            return

        for skill_key, skill_def in ss_def.skills.items():
            skill_description = skill_def.description or ""
            skill_enabled = skill_def.enabled if skill_def.enabled is not None else True
            skill_params = [p.model_dump() for p in skill_def.parameters]
            skill_triggers = skill_def.triggers
            skill_tools_required = skill_def.tools_required
            existing_skill = existing_skills.get(skill_key)

            if existing_skill:
                skill_changed = force or _skill_content_changed(
                    existing_skill,
                    skill_def.name,
                    skill_description,
                    skill_def.template,
                    skill_params,
                    skill_triggers,
                    skill_tools_required,
                    skill_enabled,
                )
                if not skill_changed:
                    console.print(f"    [dim][skip][/dim] skill '{skill_def.name}' (unchanged)")
                elif dry_run:
                    console.print(
                        f"    [yellow][dry-run][/yellow] would update skill '{skill_def.name}' (key: {skill_key})"
                    )
                else:
                    try:
                        state.get_client().put(
                            f"/api/v1/skillsets/{skillset_id}/skills/{existing_skill['skill_id']}",
                            json={
                                "name": skill_def.name,
                                "description": skill_description,
                                "template": skill_def.template,
                                "parameters": skill_params,
                                "triggers": skill_triggers,
                                "tools_required": skill_tools_required,
                                "enabled": skill_enabled,
                                "comment": SEED_UPDATE_COMMENT,
                            },
                        )
                    except Exception as exc:
                        _die(exc)
                        return
                    console.print(
                        f"    [blue][updated][/blue] '{existing_skill['skill_id']}'"
                        f"  name='{skill_def.name}'  yaml_key='{skill_key}'"
                    )
            elif dry_run:
                console.print(
                    f"    [yellow][dry-run][/yellow] would create skill '{skill_def.name}' (key: {skill_key})"
                )
            else:
                try:
                    result = state.get_client().post(
                        f"/api/v1/skillsets/{skillset_id}/skills",
                        json={
                            "skill_id": skill_key,
                            "name": skill_def.name,
                            "description": skill_description,
                            "template": skill_def.template,
                            "parameters": skill_params,
                            "triggers": skill_triggers,
                            "tools_required": skill_tools_required,
                            "enabled": skill_enabled,
                        },
                    )
                except Exception as exc:
                    _die(exc)
                    return
                console.print(
                    f"    [green][created][/green] '{result['skill_id']}'"
                    f"  name='{skill_def.name}'  yaml_key='{skill_key}'"
                )

    console.print(f"  Skillsets: created={ss_created} updated={ss_updated} skipped={ss_skipped}")


def _plugin_skills_differ(plugin_id: str, plugin_def: Any) -> bool:
    """Whether any stated skill is not already in the state the config asks for."""
    try:
        listed = state.get_client().get(f"/api/v1/plugins/{plugin_id}/skills")["skills"]
    except Exception:
        return True
    live = {item["skill_id"]: item["enabled"] for item in listed}
    return any(live.get(skill_id) != wanted for skill_id, wanted in plugin_def.skills.items())


def _seed_plugins(
    config: Any,
    *,
    config_path: Path,
    force: bool,
    dry_run: bool,
) -> None:
    """Validate and install package sources named by the seed configuration."""
    del force  # Package digests make identical installs idempotent, including forced seeds.
    if not config.plugins:
        console.print("  No plugins in config, skipping.")
        return

    try:
        existing = {item["plugin_id"]: item for item in _list_plugins()}
    except Exception as exc:
        _die(exc)
        return

    created = updated = skipped = 0
    for plugin_id, plugin_def in config.plugins.items():
        source = Path(plugin_def.source).expanduser()
        if not source.is_absolute():
            source = config_path.resolve().parent / source
        try:
            filename, content = build_plugin_package(source)
            upload = {"package": (filename, content, "application/zip")}
            validation = state.get_client().post("/api/v1/plugins/validate", files=upload)
        except Exception as exc:
            _die(exc)
            return

        if not validation.get("valid"):
            messages = "; ".join(item.get("message", "invalid package") for item in validation.get("diagnostics", []))
            _die(ValueError(f"plugin '{plugin_id}' failed validation: {messages or 'invalid package'}"))
            return
        if validation.get("plugin_id") != plugin_id:
            _die(
                ValueError(
                    f"plugin seed key '{plugin_id}' does not match package plugin ID {validation.get('plugin_id')!r}"
                )
            )
            return

        current = existing.get(plugin_id)
        package_changed = current is None or current.get("package_digest") != validation.get("package_digest")
        enabled_changed = current is not None and current.get("enabled", True) != plugin_def.enabled
        # Whether a skill is on is an operator's choice rather than package
        # content, so it is stated here and applied on every seed (AGT-041).
        skills_changed = bool(plugin_def.skills) and (current is None or _plugin_skills_differ(plugin_id, plugin_def))
        if not package_changed and not enabled_changed and not skills_changed:
            console.print(f"  [dim][skip][/dim] plugin '{plugin_id}' (unchanged)")
            skipped += 1
            continue
        if dry_run:
            action = "install" if current is None else "update"
            console.print(
                f"  [yellow][dry-run][/yellow] would {action} plugin '{plugin_id}' (enabled={plugin_def.enabled})"
            )
            if current is None:
                created += 1
            else:
                updated += 1
            continue

        try:
            if package_changed:
                current = state.get_client().post("/api/v1/plugins/install", files=upload)
            if current is not None and current.get("enabled", True) != plugin_def.enabled:
                current = state.get_client().put(
                    f"/api/v1/plugins/{plugin_id}",
                    json={"enabled": plugin_def.enabled},
                )
            for skill_id, skill_enabled in plugin_def.skills.items():
                state.get_client().put(
                    f"/api/v1/plugins/{plugin_id}/skills/{skill_id}",
                    json={"enabled": skill_enabled},
                )
        except Exception as exc:
            _die(exc)
            return

        if plugin_id in existing:
            console.print(f"  [blue][updated][/blue] plugin '{plugin_id}' (enabled={plugin_def.enabled})")
            updated += 1
        else:
            console.print(f"  [green][created][/green] plugin '{plugin_id}' (enabled={plugin_def.enabled})")
            created += 1

    console.print(f"  Plugins: created={created} updated={updated} skipped={skipped}")


def _export_spaces(existing_cfg: Any) -> tuple[dict[str, Any], dict[str, str], dict[str, str], dict[str, str], int]:
    """Fetch every space and return the pieces the report pass needs.

    Returns ``(spaces, space_id_to_key, subspace_id_to_key, overview_report_id_to_space_key, failed)``.
    The overview map is keyed by report id and resolved to a report *key* only
    after the report pass has assigned keys.

    Sub-space ids are globally unique, so one flat map covers every space.
    """
    space_name_to_key = {s.name: k for k, s in existing_cfg.spaces.items()}

    try:
        space_list = _list_spaces()
    except Exception as exc:
        _die(exc)
        return {}, {}, {}, {}, 1

    spaces: dict[str, Any] = {}
    space_id_to_key: dict[str, str] = {}
    subspace_id_to_key: dict[str, str] = {}
    overview_by_report_id: dict[str, str] = {}
    failed = 0

    for item in sorted(space_list, key=lambda s: s["name"]):
        space_id = item["space_id"]
        try:
            # The tree, not GET /spaces/<id>: every other space response blanks
            # the overview pointer, because only the tree can resolve it.
            tree = _space_tree(space_id)
        except Exception as exc:
            err_console.print(
                f"[yellow][warn][/yellow] Could not fetch tree for space '{item['name']}': {exc} — skipping space."
            )
            failed += 1
            continue

        key = space_name_to_key.get(item["name"]) or _slugify(item["name"])
        base_key = key
        suffix = 2
        while key in spaces:
            key = f"{base_key}_{suffix}"
            suffix += 1

        existing_def = existing_cfg.spaces.get(key)
        sub_name_to_key = {s.name: k for k, s in existing_def.subspaces.items()} if existing_def else {}

        subspaces: dict[str, Any] = {}
        for sub in sorted(tree.get("subspaces", []), key=lambda s: s["name"]):
            sub_key = sub_name_to_key.get(sub["name"]) or _slugify(sub["name"])
            base_sub_key = sub_key
            sub_suffix = 2
            while sub_key in subspaces:
                sub_key = f"{base_sub_key}_{sub_suffix}"
                sub_suffix += 1
            subspaces[sub_key] = schema.SubspaceDef(name=sub["name"])
            subspace_id_to_key[sub["subspace_id"]] = sub_key

        spaces[key] = schema.SpaceDef(
            name=item["name"],
            description=item.get("description", ""),
            subspaces=subspaces,
        )
        space_id_to_key[space_id] = key

        overview_report_id = (tree.get("space") or {}).get("overview_report_id")
        if overview_report_id:
            overview_by_report_id[overview_report_id] = key

        console.print(f"[green][export][/green] space '{item['name']}' ({len(subspaces)} sub-spaces) → key='{key}'")

    return spaces, space_id_to_key, subspace_id_to_key, overview_by_report_id, failed


def export_cmd(config: str, dry_run: bool) -> None:
    """Export the API's seedable configuration back into *config* YAML."""
    try:
        existing_cfg = schema.load_file(config)
    except FileNotFoundError:
        err_console.print(f"[yellow][warn][/yellow] Config file '{config}' not found, starting from empty config.")
        existing_cfg = schema.ReportingConfig()

    profile_name_to_key = {profile.name: key for key, profile in existing_cfg.model_profiles.items()}
    new_model_profiles: dict[str, Any] = {}
    try:
        profile_list = _list_model_profiles()
    except Exception as exc:
        _die(exc)
        return
    for item in sorted(profile_list, key=lambda profile: profile["name"]):
        key = profile_name_to_key.get(item["name"]) or _slugify(item["name"])
        base_key = key
        suffix = 2
        while key in new_model_profiles and new_model_profiles[key].name != item["name"]:
            key = f"{base_key}_{suffix}"
            suffix += 1
        try:
            new_model_profiles[key] = schema.ModelProfileDef.model_validate(
                {field: item[field] for field in schema.ModelProfileDef.model_fields}
            )
        except Exception as exc:
            _die(ValueError(f"Invalid model profile '{item.get('name', '')}': {exc}"))
            return
        console.print(f"[green][export][/green] model profile '{item['name']}' → key='{key}'")

    name_to_key = {r.name: k for k, r in existing_cfg.reports.items()}

    try:
        report_list = _list_reports()
    except Exception as exc:
        _die(exc)
        return

    dashboard_id: str | None = None
    try:
        dashboard_data = state.get_client().get("/api/v1/reports/dashboard")
        dashboard_id = dashboard_data.get("report_id")
    except APIError as exc:
        if exc.status_code != 404:
            err_console.print(f"[yellow][warn][/yellow] Could not fetch dashboard pointer: {exc}")

    # Spaces first: their keys have to exist before a report can name one.
    new_spaces, space_id_to_key, subspace_id_to_key, overview_by_report_id, space_failed = _export_spaces(existing_cfg)

    new_reports: dict[str, Any] = {}
    dashboard_key: str | None = None
    overview_keys: dict[str, str] = {}
    exported = failed = 0

    for item in sorted(report_list, key=lambda r: r["name"]):
        latest = _get_report(item["report_id"])
        if not latest:
            err_console.print(f"[yellow][warn][/yellow] No version found for '{item['name']}', skipping.")
            failed += 1
            continue

        try:
            report_obj = schema.Report.model_validate(latest["config"])
        except Exception as exc:
            err_console.print(f"[yellow][warn][/yellow] Invalid config for '{item['name']}': {exc} — skipping.")
            failed += 1
            continue

        key = name_to_key.get(item["name"]) or _slugify(item["name"])
        base_key = key
        suffix = 2
        while key in new_reports and new_reports[key].name != item["name"]:
            key = f"{base_key}-{suffix}"
            suffix += 1

        new_reports[key] = report_obj
        if item.get("pinned"):
            report_obj.pinned = True
        # Membership rides on the report list item, not the version config: it is
        # parent metadata, and a version never carries it.
        space_key = space_id_to_key.get(item.get("space_id") or "")
        if space_key:
            report_obj.space = space_key
            subspace_key = subspace_id_to_key.get(item.get("subspace_id") or "")
            # A sub-space deleted out from under its members leaves a dangling
            # id; the tree normalises that to "ungrouped", so drop it here too.
            if subspace_key and subspace_key in new_spaces[space_key].subspaces:
                report_obj.subspace = subspace_key
        elif item.get("space_id"):
            err_console.print(
                f"[yellow][warn][/yellow] Report '{item['name']}' is in a space that was not exported;"
                " its membership is not represented in the YAML."
            )
        if item["report_id"] in overview_by_report_id:
            overview_keys[overview_by_report_id[item["report_id"]]] = key
        if dashboard_id and item["report_id"] == dashboard_id:
            dashboard_key = key
        console.print(f"[green][export][/green] report '{item['name']}' → key='{key}'")
        exported += 1

    # Resolved last: an overview names a report key, which only exists once the
    # report pass has run. A pointer at a report that failed to export is left
    # unset rather than emitted dangling — the config validator rejects those.
    for space_key, space_def in new_spaces.items():
        overview_key = overview_keys.get(space_key)
        if overview_key is not None and new_reports[overview_key].space == space_key:
            space_def.overview = overview_key

    # Export canonical workflows (legacy scheduled-query records are
    # normalized by the API before they reach the CLI).
    new_workflows: list[Any] = []
    workflow_failed = 0
    try:
        workflow_list = _list_workflows()
    except Exception as exc:
        _die(exc)
        return
    for item in sorted(workflow_list, key=lambda value: value["name"]):
        try:
            new_workflows.append(
                schema.Workflow.model_validate(
                    {
                        key: item[key]
                        for key in (
                            "name",
                            "schedule",
                            "watch_scans",
                            "enabled",
                            "stages",
                            "trigger_workflows",
                        )
                    }
                )
            )
        except Exception as exc:
            err_console.print(f"[yellow][warn][/yellow] Invalid workflow '{item.get('name', '')}': {exc} — skipping.")
            workflow_failed += 1
            continue
        console.print(f"[green][export][/green] workflow '{item['name']}'")

    # Export toolsets
    new_toolsets: dict[str, Any] = {}
    ts_exported = ts_failed = ts_builtin = 0

    try:
        toolset_list = _list_toolsets()
    except Exception as exc:
        _die(exc)
        return

    for ts_item in sorted(toolset_list, key=lambda t: t["name"]):
        ts_key = ts_item["toolset_id"]

        # Built-ins ship with the application: their ids fail the YAML key
        # validators, and seeding one back would be rejected by the write path
        # anyway. Nothing about them belongs in a config of user-defined state.
        if _is_builtin_id(ts_key):
            ts_builtin += 1
            continue

        try:
            tools_data = _list_tools(ts_item["toolset_id"])

            new_tools: dict[str, Any] = {}
            for tool in sorted(tools_data, key=lambda t: t["name"]):
                tool_key = tool["tool_id"]
                params = [schema.ToolParamDef.model_validate(p) for p in tool.get("parameters", [])]
                new_tools[tool_key] = schema.ToolDef(
                    name=tool["name"],
                    description=tool.get("description", ""),
                    cypher=tool["cypher"],
                    parameters=params,
                    enabled=tool.get("enabled", True),
                )

            # Inside the try with the fetch: a toolset whose ids or tools fail
            # validation must cost one toolset, not the whole export.
            new_toolsets[ts_key] = schema.ToolsetDef(
                name=ts_item["name"],
                description=ts_item.get("description", ""),
                enabled=ts_item.get("enabled", True),
                tools=new_tools,
            )
        except Exception as exc:
            err_console.print(
                f"[yellow][warn][/yellow] Could not export toolset '{ts_item['name']}': {exc} — skipping toolset."
            )
            ts_failed += 1
            new_toolsets.pop(ts_key, None)
            continue

        console.print(f"[green][export][/green] toolset '{ts_item['name']}' ({len(new_tools)} tools) → key='{ts_key}'")
        ts_exported += 1

    if ts_builtin:
        console.print(f"[dim][skip][/dim] {ts_builtin} built-in toolset(s) (shipped with Seizu, not seedable)")

    # Export skillsets
    new_skillsets: dict[str, Any] = {}
    ss_exported = ss_failed = 0

    try:
        skillset_list = _list_skillsets()
    except Exception as exc:
        _die(exc)
        return

    for ss_item in sorted(skillset_list, key=lambda s: s["name"]):
        ss_key = ss_item["skillset_id"]
        try:
            skills_data = _list_skills(ss_item["skillset_id"])

            new_skills: dict[str, Any] = {}
            for skill in sorted(skills_data, key=lambda s: s["name"]):
                skill_key = skill["skill_id"]
                params = [schema.ToolParamDef.model_validate(p) for p in skill.get("parameters", [])]
                new_skills[skill_key] = schema.SkillDef(
                    name=skill["name"],
                    description=skill.get("description", ""),
                    template=skill["template"],
                    parameters=params,
                    triggers=skill.get("triggers", []),
                    tools_required=skill.get("tools_required", []),
                    enabled=skill.get("enabled", True),
                )

            # Constructed inside the try for the same reason as toolsets: a
            # skillset that fails validation must cost one skillset, not the run.
            new_skillsets[ss_key] = schema.SkillsetDef(
                name=ss_item["name"],
                description=ss_item.get("description", ""),
                enabled=ss_item.get("enabled", True),
                skills=new_skills,
            )
        except Exception as exc:
            err_console.print(
                f"[yellow][warn][/yellow] Could not export skillset '{ss_item['name']}': {exc} — skipping skillset."
            )
            ss_failed += 1
            new_skillsets.pop(ss_key, None)
            continue

        console.print(
            f"[green][export][/green] skillset '{ss_item['name']}' ({len(new_skills)} skills) → key='{ss_key}'"
        )
        ss_exported += 1

    updated_cfg = schema.ReportingConfig(
        queries=existing_cfg.queries,
        model_profiles=new_model_profiles,
        dashboard=dashboard_key if dashboard_key is not None else existing_cfg.dashboard,
        spaces=new_spaces,
        reports=new_reports,
        scheduled_queries=[],
        workflows=new_workflows,
        toolsets=new_toolsets,
        skillsets=new_skillsets,
        plugins=existing_cfg.plugins,
    )
    yaml_content = schema.dump_yaml(updated_cfg)

    summary = (
        f"\nDone. model_profiles: exported={len(new_model_profiles)}  "
        f"spaces: exported={len(new_spaces)} failed={space_failed}  "
        f"reports: exported={exported} failed={failed}  "
        f"workflows: exported={len(new_workflows)} failed={workflow_failed}  "
        f"toolsets: exported={ts_exported} failed={ts_failed} builtin_skipped={ts_builtin}  "
        f"skillsets: exported={ss_exported} failed={ss_failed}  "
    )

    if dry_run:
        console.print("\n--- YAML output (dry-run, not written) ---\n")
        console.print(yaml_content)
        console.print(summary + "(dry-run, file not written)")
        return

    with open(config, "w") as f:
        f.write(yaml_content)
    console.print(summary + f"→ wrote '{config}'")
