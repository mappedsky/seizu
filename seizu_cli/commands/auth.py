"""CLI commands for authentication (login / logout / whoami)."""

import sys

import typer
from rich.console import Console

from seizu_cli import auth, state
from seizu_cli.client import APIError

app = typer.Typer(help="Authenticate with the Seizu API.", no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)


def _die(exc: Exception) -> None:
    if isinstance(exc, APIError):
        err_console.print(f"[red]Error {exc.status_code}[/red]: {exc}")
    else:
        err_console.print(f"[red]Error[/red]: {exc}")
    sys.exit(1)


@app.command("login")
def login() -> None:
    """Authenticate via the Device Authorization Grant (RFC 8628).

    Opens a browser-friendly URL for you to authorize, then stores the
    access token in the OS-native keyring (macOS Keychain, Windows Credential
    Manager, Linux SecretService/KWallet). Pass --credentials-file PATH to
    store in a plain JSON file instead.

    \b
    Example:
        seizu login
        seizu --api-url https://seizu.example.com login
        seizu --credentials-file ~/seizu-creds.json login
    """
    api_url = state.api_url

    try:
        store = auth.get_store(state.credentials_file)
    except Exception as exc:
        _die(exc)
        return

    console.print(f"Authenticating with [bold]{api_url}[/bold]")

    try:
        token = auth.device_authorize(api_url)
    except Exception as exc:
        _die(exc)
        return

    store.save_token(api_url, token)
    console.print(f"\n[green]Logged in.[/green] Token saved to [dim]{store.description()}[/dim]")


@app.command("logout")
def logout() -> None:
    """Remove stored credentials for the current API URL."""
    api_url = state.api_url
    store = auth.get_store(state.credentials_file)
    removed = store.clear_token(api_url)
    if removed:
        console.print(f"[yellow]Logged out[/yellow] from [bold]{api_url}[/bold]")
    else:
        console.print(f"No stored credentials found for [bold]{api_url}[/bold]")


@app.command("whoami")
def whoami() -> None:
    """Show the currently authenticated user."""
    try:
        data = state.get_client().get("/api/v1/me")
    except Exception as exc:
        _die(exc)
        return

    user = data["user"]
    console.print(f"[bold]User ID[/bold]: {user['user_id']}")
    if user.get("display_name"):
        console.print(f"[bold]Name[/bold]: {user['display_name']}")
    console.print(f"[bold]Email[/bold]: {user['email']}")
    console.print(f"[bold]Last login[/bold]: {user.get('last_login', '')}")
    console.print(f"[bold]Role[/bold]: {user.get('role') or '(none)'}")
    permissions = data.get("permissions", [])
    console.print(f"[bold]Permissions[/bold]: {', '.join(permissions) if permissions else '(none)'}")
