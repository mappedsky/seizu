# Quickstart

A full docker-compose setup is included that starts Neo4j, PostgreSQL, Telegraf,
Seizu and its workers, and can run Cartography to load the graph database.

First clone the seizu repo:

```bash
git clone https://github.com/mappedsky/seizu
cd seizu
```

Start the stack:

```bash
make up
```

`make up` streams logs to your terminal. Once fully started, the UI will be accessible at: http://localhost:3000

The backend API (and MCP server) is accessible at: http://localhost:8080

## Running on a VM or remote host

If the docker-compose stack is running on a virtual machine or remote host rather than your local machine, you must forward the relevant ports over SSH before the UI and MCP clients can reach the stack. Only ports 3000 and 8080 are exposed to the host by the default compose configuration:

| Port | Service |
|------|---------|
| 3000 | Frontend dev server (UI) |
| 8080 | Backend API and MCP server |
| 9000 | Authentik OIDC provider (only when the `auth` profile is active) |
| 8888 | Claude MCP OAuth callback (only when using Claude with auth enabled) |

Forward ports with SSH local port forwarding:

```bash
# Basic stack (no auth)
ssh -L 3000:localhost:3000 -L 8080:localhost:8080 user@vm-host

# With Authentik auth enabled
ssh -L 3000:localhost:3000 -L 8080:localhost:8080 -L 9000:localhost:9000 user@vm-host

# With Authentik auth enabled and Claude running on the VM
ssh -L 3000:localhost:3000 -L 8080:localhost:8080 -L 9000:localhost:9000 -L 8888:localhost:8888 user@vm-host
```

Add `-N` to open the tunnels without starting a shell, or `-f -N` to background them. Once the tunnels are up, http://localhost:3000 and http://localhost:8080 resolve to the remote stack as if it were running locally.

## Seeding reports

After starting the stack for the first time, seed the example reports into PostgreSQL from the YAML config:

```bash
make seed_dashboard
```

This reads `.config/dev/seizu/reporting-dashboard.yaml`, creates each report in
the application database, and sets the dashboard pointer. After resetting the
PostgreSQL volume, re-run `make seed_dashboard` to repopulate it.

To reset the database and reseed:

```bash
make drop_db
make up
make seed_dashboard
```

## Loading CVE data

The quickstart configuration is based around the NIST CVE data. Load the full CVE database:

```bash
make sync_cve
```

## Loading GitHub data

To sync GitHub organization and repository data into the graph, create a [GitHub Personal Access Token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens). Use a **classic** PAT so that all data is syncable — fine-grained tokens cannot read GitHub Packages. For a complete sync, grant these scopes:

- `repo` (or `public_repo` for public repositories only) — repository files, commit history, dependency manifests, collaborators, and branch protection rules
- `read:org` — organization membership and team data
- `read:user` — user profile information
- `user:email` — user email addresses
- `security_events` _(optional)_ — Dependabot alerts for private repositories
- `read:packages` _(optional)_ — GitHub Container Registry packages, image manifests, layers, tags, and SLSA attestations

For the full set of supported permissions — including fine-grained token and GitHub App alternatives — see Cartography's [GitHub module configuration docs](https://cartography-cncf.github.io/cartography/modules/github/config.html).

Cartography reads its GitHub configuration as a **base64-encoded JSON object**, not a bare token. The bare GitHub token belongs in that JSON object's `token` field; the resulting encoded object goes in `CARTOGRAPHY_GITHUB_CONFIG`. Build it from your token and organization name (replace both placeholders):

```bash
printf '%s' '{"organization":[{"token":"<your_github_pat>","url":"https://api.github.com/graphql","name":"<your_org_name>"}]}' | base64 | tr -d '\n'
```

Put the resulting string in `.env`:

```
CARTOGRAPHY_GITHUB_CONFIG=<base64_value_from_above>
```

`CARTOGRAPHY_GITHUB_TOKEN` remains a deprecated compatibility alias for the
old Seizu setting. Despite that historical name, its value must also be the
complete base64-encoded configuration, not a bare PAT.

Then run:

```bash
make sync_github
```

## Enriching CVE metadata

Other modules — GitHub, for example — create references to CVEs in the graph without the full CVE details. `sync_cve_metadata` enriches those referenced CVEs with data from the NIST NVD database, so run it **after** the module that introduced the references:

```bash
make sync_github          # creates CVE references
make sync_cve_metadata    # enriches them
```

Setting a free [NVD API key](https://nvd.nist.gov/developers/request-an-api-key) in `.env` is optional but strongly recommended — it makes the sync considerably faster. With a key, the module fetches the individual referenced CVEs; without one, it falls back to pulling an entire year of CVE data at a time.

```
CARTOGRAPHY_NIST_NVD_TOKEN=<your_nvd_api_key>
```

## Enabling the chat assistant

Chat is off by default. It needs a model and its API key; everything else has a
working default.

```
CHAT_ENABLED=true
CHAT_LLM_MODEL=anthropic/claude-sonnet-4-6
ANTHROPIC_API_KEY=sk-ant-...
```

```bash
make down && make up
```

`CHAT_LLM_MODEL` is any model id LiteLLM understands, so an OpenAI, Gemini or
DeepSeek model works the same way with its own key. `CHAT_LLM_PROVIDER=mock` is
the default and only echoes input — it cannot call tools, so a real provider is
required for anything useful.

Open http://localhost:3000/app/chat and ask something about your graph, such as
*"Which repositories have the most critical CVEs?"*. In the default
unauthenticated dev mode every request gets all permissions, so there is nothing
to grant; with auth enabled, `chat:use` gates the UI and `chat:tools:call` lets
the agent run tools.

Chat history needs the PostgreSQL checkpoint database, which the compose stack
provisions for you. For per-user model choice, an economy fallback or spend
caps, create a profile under **Model Profiles** in the sidebar. See
[Chat Assistant](chat.html).

### Adding the sandbox

The sandbox lets the assistant run code, and is what makes skills that ship
scripts runnable. It is off by default and needs a sandbox provider —
[E2B](https://e2b.dev)'s free tier is the quickest way to try it:

```
SANDBOX_ENABLED=true
SANDBOX_API_KEY=e2b_...
```

```bash
make down && make up
```

Ask the assistant *"Can you run a Python script that prints the first 10
Fibonacci numbers?"* to confirm it works. Sandboxes have no outbound internet
unless you set `SANDBOX_ALLOW_INTERNET=true`. See [Sandbox](sandbox.html),
including the self-hosted option if you would rather not use a hosted provider.

## Running workflows

Workflows need no enabling: `make up` already starts Temporal and the
`seizu-temporal-worker` that executes them, and `make seed_dashboard` installs
several examples. Open **Workflows** in the sidebar, pick one, and use **Run
now**; the run and each of its stages appear in the workflow's history, and in
the Temporal UI at http://localhost:8233.

A workflow runs ordered stages, with the activities inside one stage running in
parallel. Each activity publishes a named output that later stages can consume,
so a query stage can feed a notification stage.

To restrict which code-defined workflows are available, set
`TEMPORAL_ENABLED_WORKFLOWS` to a comma-separated list on **both** the web
service and the worker; unset means all of them. Note that the `agent_chat`
workflow runs an AI session, so it needs chat configured as above. See
[Workflows](workflows.html) and [Built-in workflows](built-in-workflows.html).

## Enabling external MCP tools

Seizu's chat assistant can use tools from other MCP servers, reached through an
identity-aware proxy. The compose stack bundles two upstream servers to try this
against: the GitHub MCP server and a deps.dev package-metadata server.

Add a [fine-grained GitHub PAT](https://github.com/settings/tokens?type=beta)
and the proxy configuration to `.env`:

```
DEV_GITHUB_MCP_TOKEN=<fine-grained-development-pat>
DEV_GITHUB_MCP_READ_ONLY=1

MCP_EXTERNAL_ENABLED=true
MCP_EXTERNAL_PROXIES=[{"name":"github","url":"http://external-mcp-github-auth:8080/mcp","upstream_urls":["https://mcp.github.test/mcp"],"transport":"streamable_http","auth_mode":"header_delegation"},{"name":"deps","url":"http://external-mcp-deps:8080/mcp","upstream_urls":["https://mcp.deps.test/mcp"],"transport":"streamable_http","auth_mode":"header_delegation","require_confirmation":false}]
```

```bash
make external_mcp_enable
make down && make up
```

Discovered tools are namespaced `ext__<proxy>__<tool>` and appear on the
**MCP Toolsets** page as read-only entries, so you can see what each proxy
offers. They are available to the chat assistant; Seizu does not re-export them
from its own MCP endpoint.

In this local setup every GitHub call uses the one development PAT, so treat it
as a shared service account and scope it to what you need for testing. See
[External MCP](external-mcp.html) for per-user identity and the OAuth path.

## Testing authentication

The stack includes an embedded [Authentik](https://goauthentik.io/) OIDC provider. To enable it, use `auth_enable_bootstrap`, which generates `SESSION_TOKEN_ENCRYPTION_KEY` and `REPORT_QUERY_SIGNING_SECRET` into your `.env` file (skipping either if already set) and then enables auth:

```bash
make auth_enable_bootstrap && make down && make up
```

If you have already run `auth_enable_bootstrap` before and just want to re-enable auth without regenerating secrets:

```bash
make auth_enable && make down && make up
```

On first run, Authentik takes about two minutes to initialize. Once ready, visit http://localhost:3000 and log in with one of the seeded Seizu users:

- **Admin:** `seizu-admin` / `seizu`
- **Editor:** `seizu-editor` / `seizu`
- **Viewer:** `seizu-viewer` / `seizu`

Everything reaches Authentik as `localhost:9000` — the browser and any MCP or
CLI client directly, and the backend through `scripts/dev_oidc_loopback.py`,
which the `seizu` container starts alongside gunicorn and which forwards its
own loopback `:9000` on to `authentik-server:9000`. That is deliberate: Authentik
derives the `iss` claim from the request's `Host` header, so a backend reaching
it under a second hostname would mint a different issuer than your MCP client
gets, and the same login would become two Seizu users with separate reports,
chat threads and confirmations. See
[the authentication decisions](../dev/decisions/authentication.md).

To disable auth and return to the default unauthenticated mode:

```bash
make auth_disable && make down && make up
```
