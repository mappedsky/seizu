"""CLI commands for managing scheduled queries."""

import json
import sys
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from seizu_cli import state
from seizu_cli.client import APIError

app = typer.Typer(help="Manage scheduled queries.", no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)


def _die(exc: Exception) -> None:
    if isinstance(exc, APIError):
        err_console.print(f"[red]Error {exc.status_code}[/red]: {exc}")
    else:
        err_console.print(f"[red]Error[/red]: {exc}")
    sys.exit(1)


def _print_sq_detail(data: dict[str, Any], as_json: bool) -> None:
    if as_json:
        console.print_json(json.dumps(data))
        return
    console.print(f"[bold]ID[/bold]: {data['scheduled_query_id']}")
    console.print(f"[bold]Name[/bold]: {data['name']}")
    console.print(f"[bold]Enabled[/bold]: {data.get('enabled', True)}")
    console.print(f"[bold]Frequency[/bold]: {data.get('frequency')}")
    console.print(f"[bold]Schedule[/bold]: {json.dumps(data['schedule']) if data.get('schedule') else None}")
    console.print(f"[bold]Version[/bold]: {data.get('current_version', data.get('version'))}")
    console.print(f"[bold]Created By[/bold]: {data['created_by']}")
    console.print(f"[bold]Updated By[/bold]: {data.get('updated_by', '')}")
    console.print(f"[bold]Cypher[/bold]\n{data['cypher']}")


@app.command("list")
def list_scheduled_queries(
    output: str = typer.Option("table", "--output", "-o", help="Output format: table or json."),
) -> None:
    """List all scheduled queries."""
    try:
        data = state.get_client().get("/api/v1/scheduled-queries")
    except Exception as exc:
        _die(exc)
        return

    if output == "json":
        console.print_json(json.dumps(data))
        return

    items = data.get("scheduled_queries", [])
    if not items:
        console.print("[dim]No scheduled queries found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Enabled")
    table.add_column("Frequency (s)", justify="right")
    table.add_column("Version", justify="right")
    table.add_column("Updated At")

    for q in items:
        table.add_row(
            q["scheduled_query_id"],
            q["name"],
            "yes" if q.get("enabled", True) else "no",
            str(q.get("frequency") or ""),
            str(q.get("current_version", "")),
            q.get("updated_at", ""),
        )

    console.print(table)


@app.command("get")
def get_scheduled_query(
    sq_id: str = typer.Argument(help="Scheduled query ID."),
    output: str = typer.Option("table", "--output", "-o", help="Output format: table or json."),
) -> None:
    """Get a scheduled query by ID."""
    try:
        data = state.get_client().get(f"/api/v1/scheduled-queries/{sq_id}")
    except Exception as exc:
        _die(exc)
        return
    _print_sq_detail(data, as_json=(output == "json"))


@app.command("run")
def run_scheduled_query(
    sq_id: str = typer.Argument(help="Scheduled query ID."),
) -> None:
    """Request an immediate run of a scheduled query (picked up by the worker)."""
    try:
        data = state.get_client().post(f"/api/v1/scheduled-queries/{sq_id}/run")
    except Exception as exc:
        _die(exc)
        return
    console.print(f"[green]Run requested[/green]: {sq_id} at {data.get('run_requested_at', '')}")


@app.command("delete")
def delete_scheduled_query(
    sq_id: str = typer.Argument(help="Scheduled query ID."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Delete a scheduled query."""
    if not yes:
        typer.confirm(f"Delete scheduled query {sq_id!r}?", abort=True)
    try:
        state.get_client().delete(f"/api/v1/scheduled-queries/{sq_id}")
    except Exception as exc:
        _die(exc)
        return
    console.print(f"[red]Deleted[/red]: {sq_id}")


@app.command("versions")
def list_versions(
    sq_id: str = typer.Argument(help="Scheduled query ID."),
    output: str = typer.Option("table", "--output", "-o", help="Output format: table or json."),
) -> None:
    """List all versions of a scheduled query."""
    try:
        data = state.get_client().get(f"/api/v1/scheduled-queries/{sq_id}/versions")
    except Exception as exc:
        _die(exc)
        return

    if output == "json":
        console.print_json(json.dumps(data))
        return

    versions = data.get("versions", [])
    table = Table(show_header=True, header_style="bold")
    table.add_column("Version", justify="right")
    table.add_column("Created By")
    table.add_column("Created At")
    table.add_column("Comment")

    for v in versions:
        table.add_row(
            str(v["version"]),
            v["created_by"],
            v["created_at"],
            v.get("comment") or "",
        )
    console.print(table)


@app.command("version-get")
def get_version(
    sq_id: str = typer.Argument(help="Scheduled query ID."),
    version: int = typer.Argument(help="Version number."),
    output: str = typer.Option("table", "--output", "-o", help="Output format: table or json."),
) -> None:
    """Get a specific version of a scheduled query."""
    try:
        data = state.get_client().get(f"/api/v1/scheduled-queries/{sq_id}/versions/{version}")
    except Exception as exc:
        _die(exc)
        return
    _print_sq_detail(data, as_json=(output == "json"))
