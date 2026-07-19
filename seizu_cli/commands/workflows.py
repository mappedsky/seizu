"""CLI commands for managing Temporal-backed workflows."""

import json
import sys
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from seizu_cli import state
from seizu_cli.client import APIError

app = typer.Typer(help="Manage workflows.", no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)


def _die(exc: Exception) -> None:
    if isinstance(exc, APIError):
        err_console.print(f"[red]Error {exc.status_code}[/red]: {exc}")
    else:
        err_console.print(f"[red]Error[/red]: {exc}")
    sys.exit(1)


def _detail(data: dict[str, Any], as_json: bool) -> None:
    if as_json:
        console.print_json(json.dumps(data))
        return
    console.print(f"[bold]ID[/bold]: {data['workflow_id']}")
    console.print(f"[bold]Name[/bold]: {data['name']}")
    console.print(f"[bold]Enabled[/bold]: {data.get('enabled', True)}")
    console.print(f"[bold]Version[/bold]: {data.get('current_version', data.get('version'))}")
    stages = data.get("stages", [])
    console.print(f"[bold]Stages[/bold]: {len(stages)}")
    console.print(f"[bold]Activities[/bold]: {sum(len(stage.get('activities', [])) for stage in stages)}")
    console.print(f"[bold]Schedule sync[/bold]: {data.get('schedule_sync_status', '')}")


@app.command("list")
def list_workflows(
    output: str = typer.Option("table", "--output", "-o", help="Output format: table or json."),
) -> None:
    """List workflows."""
    try:
        data = state.get_client().get("/api/v1/workflows")
    except Exception as exc:
        _die(exc)
        return
    if output == "json":
        console.print_json(json.dumps(data))
        return
    table = Table(show_header=True, header_style="bold")
    for column in ("ID", "Name", "Enabled", "Stages", "Activities", "Schedule sync", "Version"):
        table.add_column(column)
    for item in data.get("workflows", []):
        table.add_row(
            item["workflow_id"],
            item["name"],
            "yes" if item.get("enabled", True) else "no",
            str(len(item.get("stages", []))),
            str(sum(len(stage.get("activities", [])) for stage in item.get("stages", []))),
            item.get("schedule_sync_status", ""),
            str(item.get("current_version", "")),
        )
    console.print(table)


@app.command("get")
def get_workflow(
    workflow_id: str,
    output: str = typer.Option("table", "--output", "-o"),
) -> None:
    """Get a workflow by ID."""
    try:
        data = state.get_client().get(f"/api/v1/workflows/{workflow_id}")
    except Exception as exc:
        _die(exc)
        return
    _detail(data, output == "json")


@app.command("run")
def run_workflow(workflow_id: str) -> None:
    """Start an immediate Temporal workflow run."""
    try:
        data = state.get_client().post(f"/api/v1/workflows/{workflow_id}/run")
    except Exception as exc:
        _die(exc)
        return
    console.print(f"[green]Started[/green]: {data['temporal_workflow_id']}")


@app.command("delete")
def delete_workflow(workflow_id: str, yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    """Delete a workflow definition."""
    if not yes:
        typer.confirm(f"Delete workflow {workflow_id!r}?", abort=True)
    try:
        state.get_client().delete(f"/api/v1/workflows/{workflow_id}")
    except Exception as exc:
        _die(exc)
        return
    console.print(f"[red]Deleted[/red]: {workflow_id}")


@app.command("versions")
def list_versions(
    workflow_id: str,
    output: str = typer.Option("table", "--output", "-o"),
) -> None:
    """List workflow versions."""
    try:
        data = state.get_client().get(f"/api/v1/workflows/{workflow_id}/versions")
    except Exception as exc:
        _die(exc)
        return
    if output == "json":
        console.print_json(json.dumps(data))
        return
    table = Table(show_header=True, header_style="bold")
    for column in ("Version", "Created by", "Created at", "Comment"):
        table.add_column(column)
    for version in data.get("versions", []):
        table.add_row(
            str(version["version"]),
            version["created_by"],
            version["created_at"],
            version.get("comment") or "",
        )
    console.print(table)
