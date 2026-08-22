"""Agent Plugin package commands."""

import json
from pathlib import Path

import typer

from seizu_cli.plugin_package import build_plugin_package
from seizu_cli.state import get_client

app = typer.Typer(help="Manage Agent Plugins 1.0.0 packages.", no_args_is_help=True)


def _upload(endpoint: str, source: Path) -> dict:
    try:
        name, content = build_plugin_package(source)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return get_client().post(endpoint, files={"package": (name, content, "application/zip")})


@app.command("validate")
def validate(source: Path) -> None:
    """Validate a plugin directory or ZIP without installing it."""
    result = _upload("/api/v1/plugins/validate", source)
    typer.echo(json.dumps(result, indent=2))
    if not result.get("valid"):
        raise typer.Exit(1)


@app.command("install")
def install(source: Path) -> None:
    """Install or update a plugin from a directory or ZIP."""
    typer.echo(json.dumps(_upload("/api/v1/plugins/install", source), indent=2))


@app.command("list")
def list_plugins() -> None:
    typer.echo(json.dumps(get_client().get("/api/v1/plugins"), indent=2))


@app.command("show")
def show(plugin_id: str) -> None:
    typer.echo(json.dumps(get_client().get(f"/api/v1/plugins/{plugin_id}"), indent=2))


@app.command("enable")
def enable(plugin_id: str) -> None:
    typer.echo(json.dumps(get_client().put(f"/api/v1/plugins/{plugin_id}", json={"enabled": True}), indent=2))


@app.command("disable")
def disable(plugin_id: str) -> None:
    typer.echo(json.dumps(get_client().put(f"/api/v1/plugins/{plugin_id}", json={"enabled": False}), indent=2))


@app.command("remove")
def remove(plugin_id: str, yes: bool = typer.Option(False, "--yes")) -> None:
    if not yes and not typer.confirm(f"Delete plugin {plugin_id}?"):
        raise typer.Abort()
    get_client().delete(f"/api/v1/plugins/{plugin_id}")


@app.command("download")
def download(plugin_id: str, output: Path = typer.Option(..., "--output", "-o")) -> None:
    output.write_bytes(get_client().get_bytes(f"/api/v1/plugins/{plugin_id}/download"))
    typer.echo(str(output))
