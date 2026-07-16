"""Seizu CLI entry point."""

from pathlib import Path

import typer

from seizu_cli import auth, state
from seizu_cli import config as cli_config
from seizu_cli.commands import auth as auth_commands
from seizu_cli.commands import reports, scheduled_queries, seed, skillsets, toolsets, workflows

app = typer.Typer(
    help="Seizu CLI — manage reports, workflows, and toolsets via the Seizu API.",
    no_args_is_help=True,
)
app.add_typer(reports.app, name="reports")
app.add_typer(scheduled_queries.app, name="scheduled-queries")
app.add_typer(workflows.app, name="workflows")
app.add_typer(toolsets.app, name="toolsets")
app.add_typer(skillsets.app, name="skillsets")
app.add_typer(auth_commands.app, name="auth")


@app.callback()
def main(
    api_url: str | None = typer.Option(
        None,
        envvar="SEIZU_API_URL",
        help=("Seizu API base URL. Overrides the 'api_url' setting in ~/.config/seizu/seizu.conf."),
    ),
    token: str | None = typer.Option(
        None,
        envvar="SEIZU_TOKEN",
        help=("Bearer token for API authentication. If omitted, a stored token from 'seizu login' is used."),
    ),
    credentials_file: Path | None = typer.Option(
        None,
        envvar="SEIZU_CREDENTIALS_FILE",
        help=("Path to a JSON credentials file. Forces file-based token storage even if an OS keyring is available."),
    ),
    config_file: Path | None = typer.Option(
        None,
        "--config-file",
        envvar="SEIZU_CONFIG_FILE",
        help=("Path to the CLI config file. Defaults to ~/.config/seizu/seizu.conf."),
    ),
) -> None:
    """Seizu CLI — manage reports, workflows, and toolsets via the Seizu API.

    Configuration is read from ~/.config/seizu/seizu.conf (YAML).
    CLI flags and environment variables take precedence over config-file values.

    \b
    Example ~/.config/seizu/seizu.conf:
        api_url: https://seizu.example.com
        seed_file: ~/dashboards/reporting.yaml

    Authentication is handled via the Device Authorization Grant flow.
    Run 'seizu login' once; credentials are stored in the OS-native keyring
    and loaded automatically on every subsequent command.

    \b
    Quick start:
        seizu login                    # authenticate (opens browser URL)
        seizu reports list             # list reports
        seizu reports clone <id> <name> # clone a report
        seizu scheduled-queries list   # list scheduled queries
        seizu toolsets list            # list toolsets
        seizu toolsets tools list <toolset_id>
        seizu toolsets tools call <toolset_id> <tool_id> --arg limit=10

    \b
    To use a different deployment:
        seizu --api-url https://seizu.example.com login
        seizu --api-url https://seizu.example.com reports list

    \b
    To generate client libraries in other languages from the OpenAPI spec:
        make generate_openapi           # exports schema/openapi.json
        make generate_client LANG=go    # generates a Go client
        make generate_client LANG=typescript-fetch
        make generate_client LANG=java

    See https://openapi-generator.tech/docs/generators for all supported languages.
    """
    cfg = cli_config.load_config(config_file)

    # Resolution order: CLI flag → env var → config file → built-in default
    resolved_api_url = api_url or cfg.api_url or "http://localhost:8080"

    state.api_url = resolved_api_url
    state.credentials_file = credentials_file
    state.seed_file = cfg.seed_file

    if token:
        # Explicit --token / SEIZU_TOKEN takes precedence over stored credentials.
        state.token = token
    else:
        # Fall back to stored credentials for this API URL.
        state.token = auth.load_token(resolved_api_url, credentials_file)

    state.reset_client()


@app.command("login")
def login_cmd() -> None:
    """Authenticate via the Device Authorization Grant (RFC 8628).

    Fetches the OIDC configuration from the API, then starts a Device
    Authorization flow.  A URL and short code are displayed; open the URL
    in your browser, enter the code, and the CLI will receive an access
    token automatically.

    The token is stored in the OS-native keyring and loaded automatically on
    subsequent commands. Pass --credentials-file PATH to store in a plain JSON
    file instead.

    \b
    Example:
        seizu login
        seizu --api-url https://seizu.example.com login
        seizu --credentials-file ~/seizu-creds.json login
    """
    auth_commands.login()


@app.command("logout")
def logout_cmd() -> None:
    """Remove stored credentials for the current API URL."""
    auth_commands.logout()


@app.command("whoami")
def whoami_cmd() -> None:
    """Show the currently authenticated user."""
    auth_commands.whoami()


@app.command("seed")
def seed_cmd(
    config: str | None = typer.Option(
        None,
        envvar="REPORTING_CONFIG_FILE",
        help=(
            "Path to the YAML dashboard config file. "
            "Defaults to 'seed_file' in ~/.config/seizu/seizu.conf, "
            "then ~/.config/seizu/reporting-dashboard.yaml."
        ),
    ),
    force: bool = typer.Option(
        False,
        help="Update existing records even if their content is unchanged.",
    ),
    dry_run: bool = typer.Option(
        False,
        help="Preview what would be created or updated without writing anything.",
    ),
) -> None:
    """Seed reports, workflows, tools, and skills from YAML via the API."""
    resolved_config = config or state.seed_file or cli_config.default_seed_file()
    seed.seed_cmd(config=resolved_config, force=force, dry_run=dry_run)


@app.command("export")
def export_cmd(
    config: str | None = typer.Option(
        None,
        envvar="REPORTING_CONFIG_FILE",
        help=(
            "Path to the YAML dashboard config file. "
            "Defaults to 'seed_file' in ~/.config/seizu/seizu.conf, "
            "then ~/.config/seizu/reporting-dashboard.yaml."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        help="Print the resulting YAML without overwriting the config file.",
    ),
) -> None:
    """Export reports, workflows, tools, and skills back into a YAML config file."""
    resolved_config = config or state.seed_file or cli_config.default_seed_file()
    seed.export_cmd(config=resolved_config, dry_run=dry_run)
