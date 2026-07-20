# Backend Installation & Configuration

## Demo/Quickstart/Development

If you're just wanting to quickly evaluate or demo Seizu, please see the [quickstart documentation](quickstart.html).

## Installation using docker image

```bash
# first setup your environment in an env file, according to the configuration instructions
docker pull ghcr.io/mappedsky/seizu:latest
docker run --env-file <your-env-file> ghcr.io/mappedsky/seizu:latest
```

## Installation using Python packages

Seizu also publishes Python wheels for environments where running without Docker is useful.

The `seizu` package includes the FastAPI backend, Temporal workflow worker, CLI, shared schema models, and the generated frontend bundle. The packaged frontend includes the full Vite build output: `index.html`, JavaScript, CSS, manifest, favicon, and any other files emitted into `build/` at release time.

```bash
python -m venv .venv
. .venv/bin/activate
pip install seizu

# Web/API process
seizu-server

# Workflow scheduler and worker, usually run as a separate process
seizu-temporal-worker
```

The separately published `seizu-cli` package installs only the CLI and shared schema code:

```bash
pip install seizu-cli
seizu --api-url https://seizu.example.com reports list
```

See the [CLI documentation](cli.html) for authentication, configuration, seed/export, and common command examples.

## Backend configuration

### Basic configuration

When using the docker image, the defaults should be sufficient for basic configuration.

* ``DEBUG``: Whether or not seizu is run in debug mode. This should never be set outside of development; default: ``False``
* ``HOST``: IP address to listen on; default: ``0.0.0.0``
* ``PORT``: Port to listen on; default: ``8080``
* ``STATIC_FOLDER``: location of the React app build directory. In the Docker image this is ``/build``. In the Python wheel, Seizu defaults to the packaged frontend at ``reporting/static_dist``. Set this explicitly to serve a different build directory.

### Frontend configuration

seizu passes configuration to the frontend via a configuration endpoint.
Report and dashboard configurations are stored in the configured report store, which supports DynamoDB and SQL backends. Use ``seizu seed`` to populate the store from a YAML file.

### Neo4j configuration

* ``NEO4J_URI``: the URL to connect to neo4j; default: ``bolt://localhost:7687``
* ``NEO4J_USER``: the username to use to connect; default: ``None``
* ``NEO4J_PASSWORD``: the password to use to connect; default: ``None``
* ``NEO4J_MAX_CONNECTION_LIFETIME``: maximum duration in seconds a driver will keep a connection before removing it from its pool; default: ``3600``
* ``NEO4J_NOTIFICATIONS_MIN_SEVERITY``: minimum severity for Neo4j query notifications logged by the driver (``WARNING``, ``INFORMATION``, ``OFF``). Set to ``OFF`` to suppress schema warnings when the database is not fully populated; default: ``WARNING``
* ``QUERY_VALIDATOR_ALLOWED_PROCEDURES``: comma-separated list of extra Neo4j procedures the Cypher validator permits in addition to Seizu's built-in read-only schema procedures. Entries are normalized lowercase and may be exact names such as ``apoc.meta.stats`` or namespace prefixes ending in a dot such as ``apoc.`` or ``gds.``. This setting only permits ``CALL`` procedure invocations; dangerous function namespaces such as ``apoc.cypher.*`` and ``gds.*`` remain blocked. Empty by default.

### Report storage configuration

* ``REPORT_STORE_BACKEND``: storage backend to use for Seizu-managed configuration objects, including reports, dashboards, scheduled queries, roles, toolsets, tools, skillsets, and skills. Supported values: ``dynamodb`` (default), ``sqlmodel``
* ``REPORT_QUERY_SIGNING_SECRET``: cryptographically random secret used to sign report-query capability tokens. Use at least 32 bytes of entropy, 64 bytes preferred. Encode it as hex or base64, store it in a secret manager or deployment env var, and keep it stable across restarts so existing report tokens remain valid until they expire. If you use hex, 32 bytes becomes 64 characters and 64 bytes becomes 128 characters; if you use base64, 32 bytes is typically 44 characters with padding. Rotate it if exposed; rotation invalidates outstanding report tokens.

### DynamoDB configuration

Used when ``REPORT_STORE_BACKEND=dynamodb``. In production, standard AWS credential resolution applies (instance profile, environment variables, etc.).

* ``DYNAMODB_TABLE_NAME``: name of the DynamoDB table; default: ``seizu-reports``
* ``DYNAMODB_REGION``: AWS region for DynamoDB; default: ``us-east-1``
* ``DYNAMODB_ENDPOINT_URL``: override the DynamoDB endpoint URL (e.g. ``http://dynamodb:8000`` for local DynamoDB); default: ``""`` (uses AWS endpoint)
* ``DYNAMODB_CREATE_TABLE``: when ``true``, creates the table automatically on startup if it does not exist. Enable in local development; default: ``false``
* ``SNOWFLAKE_MACHINE_ID``: Snowflake ID generator machine ID (0–1023). Set a unique value per instance when running multiple replicas to avoid ID collisions; default: ``1``

### SQL configuration

Used when ``REPORT_STORE_BACKEND=sqlmodel``.

* ``SQL_DATABASE_URL``: SQLAlchemy database URL without credentials. Any SQLAlchemy-compatible database is supported. Credential-bearing URLs remain supported for backward compatibility. Examples:

  * ``postgresql://host:5432/seizu``
  * ``sqlite:///./seizu.db``

  default: ``""``
* ``SQL_DATABASE_USER``: optional username overlaid on ``SQL_DATABASE_URL``; default: ``""``
* ``SQL_DATABASE_PASSWORD``: optional password overlaid on ``SQL_DATABASE_URL``; default: ``""``. Store this value in a secret manager independently of the non-secret URL.

### Chat checkpoint storage

LangGraph chat history can use DynamoDB or PostgreSQL independently of the report store.

* ``CHAT_CHECKPOINT_BACKEND``: checkpoint backend. Supported values: ``dynamodb`` (default), ``postgres``
* ``CHAT_CHECKPOINT_CREATE_TABLE``: create or migrate the configured checkpoint tables during startup; default: ``false``

#### DynamoDB checkpoint configuration

Used when ``CHAT_CHECKPOINT_BACKEND=dynamodb``. In production, standard AWS credential resolution applies (instance profile, environment variables, etc.).

* ``CHAT_CHECKPOINT_TABLE_NAME``: name of the DynamoDB checkpoint table; default: ``seizu-chat-checkpoints``
* ``CHAT_CHECKPOINT_TTL_SECONDS``: checkpoint lifetime in seconds. Set to ``0`` to disable automatic expiration; default: ``0``
* ``CHAT_CHECKPOINT_ENABLE_COMPRESSION``: compress serialized checkpoint payloads; default: ``true``
* ``CHAT_CHECKPOINT_S3_BUCKET``: S3 bucket used to offload checkpoint payloads larger than 350 KB; default: ``""``
* ``CHAT_CHECKPOINT_S3_ENDPOINT_URL``: override the S3 endpoint URL (for example, ``http://minio:9000`` for local development); default: ``""`` (uses the AWS endpoint)
* ``CHAT_CHECKPOINT_S3_KEY_PREFIX``: object key prefix for checkpoint payloads stored in S3; default: ``seizu/langgraph``

#### SQL checkpoint configuration

Used when ``CHAT_CHECKPOINT_BACKEND=postgres``. The current SQL checkpointer requires PostgreSQL.

* ``CHAT_CHECKPOINT_DATABASE_URL``: checkpoint database URL without credentials. A dedicated database is recommended so checkpoint migrations, retention, backups, and deletion remain isolated from application tables. Credential-bearing URLs remain supported for backward compatibility. Example:

  * ``postgresql://host:5432/seizu-chat-checkpoints``

  default: ``SQL_DATABASE_URL``
* ``CHAT_CHECKPOINT_DATABASE_USER``: optional checkpoint username. Defaults to ``SQL_DATABASE_USER``.
* ``CHAT_CHECKPOINT_DATABASE_PASSWORD``: optional checkpoint password. Defaults to ``SQL_DATABASE_PASSWORD`` and can be managed as an independent secret.
* ``CHAT_CHECKPOINT_DATABASE_POOL_MIN_SIZE``: minimum async database connections per application process; default: ``1``
* ``CHAT_CHECKPOINT_DATABASE_POOL_MAX_SIZE``: maximum async database connections per application process; default: ``10``

For local development, ``make sqlmodel_enable`` selects both the SQLModel report store and PostgreSQL chat checkpoints. Compose idempotently creates a dedicated ``seizu-chat-checkpoints`` database before starting Seizu. ``make sqlmodel_disable`` restores both to DynamoDB.

### Auth configuration

#### OIDC / JWT configuration

seizu validates JWTs using `PyJWKClient` against any standard OIDC JWKS endpoint. Set ``JWKS_URL`` to your provider's JWKS JSON endpoint and configure the frontend OIDC settings so the browser can complete the PKCE flow.

* ``JWKS_URL``: JWKS JSON endpoint used to validate JWTs (e.g. ``https://idp.example.com/application/o/seizu/jwks/``); default: ``""``
* ``JWT_HEADER_NAME``: request header carrying the token; default: ``Authorization``
* ``JWT_EMAIL_CLAIM``: optional JWT claim for the user's email address; default: ``email``
* ``JWT_USERNAME_CLAIM``: optional JWT claim for the user's preferred username; default: ``preferred_username``
* ``JWT_ISSUER``: optional issuer to validate in the JWT; default: ``""`` (skips issuer validation)
* ``JWT_AUDIENCE``: optional audience to validate; must match the OIDC client ID when using providers (like Authentik) that always set ``aud``; default: ``""``
* ``ALLOWED_JWT_ALGORITHMS``: comma-separated list of allowed JWT signing algorithms; default: ``RS256,ES256,ES512``
* ``OIDC_AUTHORITY``: OIDC provider base URL; passed to the frontend via ``GET /api/v1/config`` and also added to the ``connect-src`` Content-Security-Policy directive so the browser can reach the discovery document and token endpoint; default: ``""``
* ``OIDC_CLIENT_ID``: OIDC client ID; passed to the frontend; default: ``""``
* ``OIDC_REDIRECT_URI``: OIDC callback URL; passed to the frontend via ``GET /api/v1/config`` but **not used by the frontend** — the browser derives the redirect URI from ``window.location.origin`` so the PKCE callback always returns to the same origin that initiated the flow; default: ``""``
* ``OIDC_SCOPE``: OIDC scope; ``offline_access`` is required so the IDP issues a refresh token for the BFF flow; default: ``openid email offline_access``
* ``OIDC_AUTHORIZE_EXTRA_PARAMS``: comma-separated ``key=value`` pairs merged into the authorize request, for provider knobs the scope can't express. Google, for example, only issues a refresh token with ``access_type=offline,prompt=consent`` instead of the ``offline_access`` scope; default: ``""``
* ``OIDC_ENABLE_TOKEN_INTROSPECTION``: validate opaque (non-JWT) access tokens via RFC 7662 introspection when local JWT validation fails. Required for IDPs (such as Google) that issue opaque access tokens; pairs with a confidential client. The introspection response must include ``active: true``, the configured subject claim, and either an ``aud`` value or ``client_id`` matching Seizu's configured audience/client. If the response omits the issuer claim, Seizu uses the configured provider issuer from discovery. Email and preferred username are optional profile data; default: ``False``
* ``OIDC_INTROSPECTION_ENDPOINT_AUTH_METHOD``: Authlib client-auth method for the introspection endpoint; default: the value of ``OIDC_TOKEN_ENDPOINT_AUTH_METHOD``
* ``OIDC_DISCOVERY_CACHE_TTL_SECONDS``: how long to cache the OIDC discovery document before re-fetching, bounding endpoint/JWKS staleness without a restart; default: ``3600``
* ``OIDC_VALIDATE_ID_TOKEN``: validate the ID token from the BFF code exchange (signature via the discovery JWKS, audience, issuer, and the login nonce). Secure by default; disable only for non-conformant providers; default: ``True``
* ``DEVELOPMENT_ONLY_REQUIRE_AUTH``: whether or not to require authentication. This option should only be changed in development; default: ``True``
* ``DEVELOPMENT_ONLY_AUTH_USER_EMAIL``: the email address of the fake user when authentication is disabled. This option should only be changed in development; default: ``testuser``

For browser sessions, Seizu stores the IDP refresh token and the ID token in an encrypted, HttpOnly session cookie. The ID token is kept so logout can send it back to the provider as ``id_token_hint`` for RP-initiated logout. Configure the OIDC provider to issue compact Seizu-specific ID tokens: include standard identity claims, optional display profile claims, and one Seizu role claim, but avoid all-groups, nested-groups, permissions arrays, or large profile/custom claims. Large ID tokens can exceed browser or proxy cookie limits and cause login, refresh, or logout failures. As a practical target, keep Seizu ID tokens below roughly 2 KB, especially when the provider issues long refresh tokens.

#### Security / cookie settings

* ``TALISMAN_FORCE_HTTPS``: redirect HTTP requests to HTTPS and enable HSTS. Set to ``False`` when running behind an SSL-terminating load balancer or in local development; default: ``True``

### RBAC configuration

Seizu uses Role-Based Access Control (RBAC) to restrict API and MCP access. Every authenticated request has a role resolved from the JWT, which maps to a set of granular permissions.

#### Built-in roles

| Role | Capabilities |
|------|-------------|
| **seizu-viewer** | Read reports and dashboard. No ad-hoc query console or query history access. |
| **seizu-editor** | All Viewer capabilities + create/edit/delete reports, set default dashboard |
| **seizu-admin** | All Editor capabilities + manage toolsets, tools, skillsets, skills, scheduled queries, and user-defined roles |

#### Role claim

Seizu reads the user's role from a single JWT claim set by the OIDC provider. Configure your provider to embed the role name (e.g. ``"seizu-admin"``) as a claim in every issued token. Most providers support this via property mappings or claim enrichment rules on group membership.

* ``RBAC_ROLE_CLAIM``: JWT claim name that holds the user's Seizu role; default: ``seizu_role``
* ``RBAC_DEFAULT_ROLE``: Role assigned when the JWT has no ``RBAC_ROLE_CLAIM``. Set to ``""`` to deny access to users without an explicit role claim. Valid values: ``"seizu-viewer"``, ``"seizu-editor"``, ``"seizu-admin"``, or any user-defined role name; default: ``"seizu-viewer"``

Prefer mapping provider groups to a single Seizu role claim instead of sending full group membership to Seizu. This keeps tokens small, avoids exposing unrelated group names to Seizu, and makes user-defined role resolution independent of provider-specific group naming.

If a user needs ad-hoc Cypher access, create a narrow custom role that includes `query:execute` and assign it only to trusted operators. Keep general report consumers on `seizu-viewer` so they can use signed report panels without console access.

**Authentik example** — create a Property Mapping with expression:

```python
seizu_group_role_map = {
    "seizu-admins": "seizu-admin",
    "seizu-editors": "seizu-editor",
}
for group in request.user.groups.all():
    if group.name in seizu_group_role_map:
        return seizu_group_role_map[group.name]
return "seizu-viewer"
```

Bind the mapping to the Seizu OAuth2 provider as a custom token property mapping with scope ``openid``.

#### User-defined roles

Admins can create and update custom roles with arbitrary permission subsets in the UI, via the API (``POST /api/v1/roles`` and ``PUT /api/v1/roles/<id>``), or through the MCP built-in role tools (for example, ``roles__create`` and ``roles__update``). When a JWT contains a user-defined role name in ``RBAC_ROLE_CLAIM``, Seizu does a single database lookup to resolve its permissions. Built-in role resolution requires no database I/O.

### MCP server

Seizu exposes a [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server at ``/api/v1/mcp``, allowing LLM agents such as Claude to query the Neo4j graph database using user-defined tools and a set of built-in management tools.

* ``MCP_ENABLED``: Enable or disable the MCP server endpoint. Set to ``False`` to turn off the endpoint entirely; default: ``True``
* ``MCP_ENABLED_BUILTINS``: Controls which built-in tool groups are exposed. User-defined toolsets are always available regardless of this setting. Three modes:

  * Unset or empty (default) — all built-in groups are enabled.
  * ``none`` — all built-in groups are disabled; only user-defined toolsets are visible.
  * Comma-separated list (e.g. ``graph,reports``) — only the listed groups are enabled.

  Known groups: ``graph``, ``reports``, ``scheduled_queries``, ``toolsets``, ``roles``.

#### Connecting MCP clients

Point MCP clients at the backend endpoint directly:

```text
https://your-seizu-host/api/v1/mcp
```

For local development, this is usually:

```text
http://localhost:8080/api/v1/mcp
```

The frontend development server does not proxy MCP traffic, so do not use port ``3000`` for MCP clients. If Seizu is behind a reverse proxy or load balancer, use the public backend URL and set ``MCP_RESOURCE_URL`` to the same MCP endpoint so OAuth discovery metadata advertises the reachable URL.

##### Claude Code

Add Seizu as an HTTP MCP server:

```bash
claude mcp add --transport http --callback-port 8888 seizu https://your-seizu-host/api/v1/mcp
```

The fixed callback port is useful for OAuth because the redirect URI must be registered with the OIDC provider:

```text
http://localhost:8888/callback
```

For the development Authentik stack this callback is pre-configured. For other OIDC providers, add it to the client manually.

##### Codex

Add Seizu as a streamable HTTP MCP server:

```bash
codex mcp add seizu --url https://your-seizu-host/api/v1/mcp
```

This writes an entry like the following to ``~/.codex/config.toml``:

```toml
[mcp_servers.seizu]
url = "https://your-seizu-host/api/v1/mcp"
```

If Seizu requires OAuth and the MCP OAuth metadata endpoint is enabled, authenticate the configured server:

```bash
codex mcp login seizu
```

For token-based automation, configure Codex to read a bearer token from an environment variable:

```bash
codex mcp add seizu --url https://your-seizu-host/api/v1/mcp --bearer-token-env-var SEIZU_TOKEN
```

#### MCP OAuth metadata (optional)

When ``MCP_OAUTH_AUTHORIZATION_ENDPOINT`` and ``MCP_OAUTH_TOKEN_ENDPOINT`` are set, Seizu publishes an [RFC 8414](https://datatracker.ietf.org/doc/html/rfc8414) OAuth 2.0 Authorization Server Metadata document at ``/api/v1/mcp/.well-known/oauth-authorization-server``. MCP clients that support in-client authentication (e.g. Claude Desktop) can use this endpoint to discover the OIDC provider and authenticate users without a pre-issued token.

* ``MCP_OAUTH_AUTHORIZATION_ENDPOINT``: OIDC authorization endpoint URL; default: ``""`` (metadata endpoint disabled)
* ``MCP_OAUTH_TOKEN_ENDPOINT``: OIDC token endpoint URL; default: ``""`` (metadata endpoint disabled)
* ``MCP_OAUTH_ISSUER``: Issuer value for the metadata document. Defaults to ``JWT_ISSUER`` if unset; default: ``""``

### Sandbox delegation

The sandbox delegation feature lets the chat agent run Python code, execute shell commands, and read/write files in an isolated ephemeral sandbox. See the [sandbox documentation](sandbox.html) for architecture details, provider options, and development setup.

* ``SANDBOX_ENABLED``: Enable the ``sandbox__delegate`` chat tool; default: ``False``
* ``SANDBOX_API_KEY``: API key for the sandbox provider. Required for E2B cloud; leave empty for self-hosted deployments that use internal auth; default: ``""``
* ``SANDBOX_DOMAIN``: Sandbox service hostname. Empty → E2B cloud. Set to your cluster ingress hostname for self-hosted deployments (e.g. OpenKruise Agents); default: ``""``
* ``SANDBOX_ALLOW_INTERNET``: Allow sandboxes to make outbound internet connections. Off by default for a hardened posture; enable only when a task legitimately needs network access; default: ``False``
* ``SANDBOX_TIMEOUT_SECONDS``: Maximum wall-clock seconds for one sandbox task before it is aborted; default: ``120``
* ``SANDBOX_MAX_OUTPUT_BYTES``: Byte cap applied both to each inner tool result fed back to the sandbox agent and to the final result returned to the chat agent; larger output is truncated; default: ``50000``
* ``SANDBOX_LLM_MODEL``: LiteLLM model ID for the inner sandbox subagent. Empty → inherits ``CHAT_LLM_MODEL``; default: ``""``

### Scheduled queries

* ``WORKFLOW_ACTIVITY_MODULES``: Comma-separated Python import locations for activities hosted by the Temporal worker; defaults to SQS, Slack, and StatsD. Code-defined workflows are top-level activity types registered in ``WORKFLOW_REGISTRY``, not activity modules.
* ``WORKFLOW_QUERY_MAX_ROWS``: Default maximum rows retained by each query activity; default: ``200``.
* ``WORKFLOW_RESULT_MAX_BYTES``: Maximum serialized bytes retained for an activity output or forwarded input. List values are truncated at a complete-row boundary; oversized scalar/object outputs fail the activity; default: ``1000000``.
* ``WORKFLOW_WATCH_POLL_SECONDS``: Temporal watch-schedule polling interval; default: ``20``.
* ``WORKFLOW_RECONCILE_SECONDS``: How often stored desired state is reconciled to Temporal Schedules; default: ``30``.
* ``SCHEDULED_QUERY_MODULES``: Deprecated compatibility fallback for ``WORKFLOW_ACTIVITY_MODULES``.

### StatsD configuration

The ``statsd`` scheduled query action module sends numeric query results to a StatsD server.
Note that the StatsD support uses DogStatsD tag extensions, so your StatsD server must also support tags (e.g. Telegraf with ``datadog_extensions = true``).

* ``STATSD_HOST``: The hostname of the StatsD server; default: ``None`` (module logs a warning and skips when unset)
* ``STATSD_PORT``: The port of the StatsD server; default: ``8125``
* ``STATSD_CONSTANT_TAGS``: A comma-separated list of ``tag_name:tag_value`` tags attached to every metric; default: ``None``

### Logging configuration

seizu ships with a sane json structured logging configuration, and good defaults, but you can override them via a config file.
Note that this setting is for the workers.
You'll also need to change gunicorn's logging configuration file setting to change the web process.

* ``LOG_CONFIG_FILE``: Location of the logging configuration file. In the Docker image this defaults to ``/home/seizu/seizu/logging.conf``. In the Python wheel, Seizu defaults to the packaged ``reporting/logging.conf``.
