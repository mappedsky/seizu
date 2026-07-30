"""CLI commands for inspecting spaces.

Read-only: spaces are created and organised from the web UI, and these
commands exist so a new deployment's space setup can be verified from a
terminal.
"""

import json
import sys

import typer
from rich.console import Console
from rich.table import Table

from seizu_cli import state
from seizu_cli.client import APIError

app = typer.Typer(help="Inspect spaces and their reports.", no_args_is_help=True)

console = Console()
err_console = Console(stderr=True)


def _die(exc: Exception) -> None:
    if isinstance(exc, APIError):
        err_console.print(f"[red]Error {exc.status_code}[/red]: {exc}")
    else:
        err_console.print(f"[red]Error[/red]: {exc}")
    sys.exit(1)


@app.command("list")
def list_spaces(
    output: str = typer.Option("table", "--output", "-o", help="Output format: table or json."),
) -> None:
    """List all spaces."""
    try:
        data = state.get_client().get("/api/v1/spaces")
    except Exception as exc:
        _die(exc)
        return

    if output == "json":
        console.print_json(json.dumps(data))
        return

    items = data.get("spaces", [])
    if not items:
        console.print("[dim]No spaces found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Description")
    table.add_column("Overview Report")
    table.add_column("Updated At")

    for space in items:
        table.add_row(
            space["space_id"],
            space["name"],
            space.get("description", ""),
            space.get("overview_report_id") or "—",
            space.get("updated_at", ""),
        )

    console.print(table)


@app.command("show")
def show_space(
    space_id: str = typer.Argument(help="Space ID."),
    output: str = typer.Option("table", "--output", "-o", help="Output format: table or json."),
) -> None:
    """Show a space with its sub-spaces and reports."""
    try:
        data = state.get_client().get(f"/api/v1/spaces/{space_id}/tree")
    except Exception as exc:
        _die(exc)
        return

    if output == "json":
        console.print_json(json.dumps(data))
        return

    space = data["space"]
    console.print(f"[bold]ID[/bold]: {space['space_id']}")
    console.print(f"[bold]Name[/bold]: {space['name']}")
    console.print(f"[bold]Description[/bold]: {space.get('description', '')}")
    console.print(f"[bold]Overview Report[/bold]: {space.get('overview_report_id') or '(none set)'}")

    subspace_names = {s["subspace_id"]: s["name"] for s in data.get("subspaces", [])}
    reports = data.get("reports", [])
    if not reports:
        console.print("\n[dim]No reports in this space.[/dim]")
        return

    table = Table(show_header=True, header_style="bold", title="Reports")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Sub-space")
    table.add_column("Overview")

    overview_id = space.get("overview_report_id")
    for report in reports:
        table.add_row(
            report["report_id"],
            report["name"],
            subspace_names.get(report.get("subspace_id") or "", "—"),
            "yes" if report["report_id"] == overview_id else "",
        )

    console.print(table)

    empty = [name for sid, name in subspace_names.items() if not any(r.get("subspace_id") == sid for r in reports)]
    if empty:
        console.print(f"\n[dim]Empty sub-spaces: {', '.join(empty)}[/dim]")
