# CLI

Seizu includes a `seizu` command-line client for managing reports, scheduled queries, MCP toolsets, MCP skillsets, and seed/export workflows through the REST API.

## Installation

For a packaged installation, install the CLI-only package:

```bash
python -m pip install seizu-cli
```

The full `seizu` server package also installs the same `seizu` command.

When working from the source checkout with the Docker quickstart, run the CLI inside the backend container:

```bash
docker compose run --rm seizu uv run --frozen --no-sync python -m seizu_cli --help
```

For repeated local use, the examples below show the installed `seizu` command. In the Docker quickstart, replace `seizu` with:

```bash
docker compose run --rm seizu uv run --frozen --no-sync python -m seizu_cli
```

## Connecting to a Seizu API

The CLI talks to the Seizu API. If no API URL is provided, it uses `http://localhost:8080`.

Use `--api-url` for one command:

```bash
seizu --api-url https://seizu.example.com reports list
```

Or set `SEIZU_API_URL`:

```bash
export SEIZU_API_URL=https://seizu.example.com
seizu reports list
```

You can also create `~/.config/seizu/seizu.conf`:

```yaml
api_url: https://seizu.example.com
seed_file: ~/dashboards/reporting-dashboard.yaml
```

Configuration resolution is:

1. CLI flags
2. Environment variables
3. `~/.config/seizu/seizu.conf`
4. Built-in defaults

## Authentication

Unauthenticated development stacks do not require a login. For authenticated deployments, run:

```bash
seizu login
```

The CLI uses the OAuth Device Authorization Grant. It prints a browser URL and a short code; approve the login in the browser, then the CLI stores the token in the OS keyring. Use `--credentials-file` when a keyring is unavailable:

```bash
seizu --credentials-file ~/.config/seizu/credentials.json login
```

Check or clear the current login:

```bash
seizu whoami
seizu logout
```

For automation, pass a bearer token directly:

```bash
SEIZU_TOKEN=<access_token> seizu reports list
```

## Common Commands

List and inspect reports:

```bash
seizu reports list
seizu reports get <report_id>
seizu reports versions <report_id>
seizu reports version-get <report_id> <version>
```

Create, clone, publish, and manage dashboard reports:

```bash
seizu reports create "Investigation Dashboard"
seizu reports clone <report_id> "Copy of Investigation Dashboard"
seizu reports publish <report_id>
seizu reports unpublish <report_id>
seizu reports set-dashboard <report_id>
seizu reports delete <report_id>
```

Scheduled queries can be listed, inspected, deleted, and reviewed by version:

```bash
seizu scheduled-queries list
seizu scheduled-queries get <scheduled_query_id>
seizu scheduled-queries versions <scheduled_query_id>
seizu scheduled-queries version-get <scheduled_query_id> <version>
seizu scheduled-queries run <scheduled_query_id>
seizu scheduled-queries delete <scheduled_query_id>
```

For MCP toolsets and skillsets:

```bash
seizu toolsets list
seizu toolsets tools list <toolset_id>
seizu toolsets tools call <toolset_id> <tool_id> --arg limit=10

seizu skillsets list
seizu skillsets skills render <skillset_id> <skill_id> --args-json '{"node_id":"abc"}'
```

Most list and get commands support JSON output:

```bash
seizu reports list --output json
seizu toolsets tools call <toolset_id> <tool_id> --output json
```

Run `seizu --help` or `seizu <group> --help` for the full command list.

## Seed and Export

The CLI can seed reports, scheduled queries, toolsets, and skillsets from the same YAML configuration format used by the quickstart:

```bash
seizu seed --config path/to/reporting-dashboard.yaml
seizu seed --dry-run
seizu seed --force
```

Export writes the latest API state back into the YAML file:

```bash
seizu export --config path/to/reporting-dashboard.yaml
seizu export --dry-run
```

When `--config` is omitted, the CLI uses `seed_file` from `~/.config/seizu/seizu.conf`, then falls back to `~/.config/seizu/reporting-dashboard.yaml`.

## Permissions

CLI commands use the same RBAC permissions as the web UI and REST API. For example, `seizu-viewer` can list and read public objects, `seizu-editor` can author reports, and `seizu-admin` can manage toolsets, tools, skillsets, skills, scheduled queries, roles, and administrative objects.
