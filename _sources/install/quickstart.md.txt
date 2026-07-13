# Quickstart

A full docker-compose setup is included that can start neo4j, dynamodb, telegraf, seizu (and its workers), and can provide a quick way of running cartography to load your neo4j database with data.

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

The default quickstart stack uses DynamoDB Local as Seizu's report store. After starting the stack for the first time, seed the example reports from the YAML config:

```bash
make seed_dashboard
```

This reads `.config/dev/seizu/reporting-dashboard.yaml`, creates each report in the configured report store, and sets the dashboard pointer. In the default quickstart stack, that store is DynamoDB Local; after resetting the DynamoDB volume, re-run `make seed_dashboard` to repopulate.

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

Cartography reads its GitHub configuration as a **base64-encoded JSON object**, not a bare token, so `CARTOGRAPHY_GITHUB_TOKEN` must hold that encoded value. Build it from your token and organization name (replace both placeholders):

```bash
printf '%s' '{"organization":[{"token":"<your_github_pat>","url":"https://api.github.com/graphql","name":"<your_org_name>"}]}' | base64 | tr -d '\n'
```

Put the resulting string in `.env`:

```
CARTOGRAPHY_GITHUB_TOKEN=<base64_value_from_above>
```

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
NIST_NVD_TOKEN=<your_nvd_api_key>
```

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

To disable auth and return to the default unauthenticated mode:

```bash
make auth_disable && make down && make up
```
