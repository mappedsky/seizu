from importlib import resources

from reporting.utils.settings import bool_env, float_env, int_env, list_env, str_env


def _parse_kv_pairs(items: list[str]) -> dict[str, str]:
    """Parse a list of ``key=value`` strings into a dict.

    Used for env vars that carry a small map as a comma-separated list (e.g.
    ``OIDC_AUTHORIZE_EXTRA_PARAMS``). Entries without ``=`` or with an empty
    key are skipped. The value may contain ``=``; only the first is the
    separator.
    """
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            continue
        key, _, value = item.partition("=")
        key = key.strip()
        if key:
            result[key] = value.strip()
    return result


def _default_static_folder() -> str:
    if resources.files("reporting").joinpath("static_dist", "index.html").is_file():
        return str(resources.files("reporting").joinpath("static_dist"))
    return "/build"


def _default_logging_config() -> str:
    packaged_config = resources.files("reporting").joinpath("logging.conf")
    if packaged_config.is_file():
        return str(packaged_config)
    return "/home/seizu/seizu/logging.conf"


# Whether or not reporting is run in debug mode. Never run reporting in debug
# mode outside of development!
DEBUG = bool_env("DEBUG", False)
# The host the ASGI app should use.
HOST = str_env("HOST", "0.0.0.0")
# The port the ASGI app should use.
PORT = int_env("PORT", 8080)
# The location of the react app build directory
STATIC_FOLDER = str_env("STATIC_FOLDER", _default_static_folder())

# The hostname of the statsd server (used by the statsd scheduled query action module)
STATSD_HOST = str_env("STATSD_HOST")
# The port of the statsd server
STATSD_PORT = int_env("STATSD_PORT", 8125)
# A comma separated list of tag_name:tag_value tags to apply to every stat
STATSD_CONSTANT_TAGS = list_env("STATSD_CONSTANT_TAGS")

# The location of the logging configuration file
LOG_CONFIG_FILE = str_env(
    "LOG_CONFIG_FILE",
    _default_logging_config(),
)

# Standard JWKS endpoint used to validate JWTs. Must be a JSON endpoint returning a
# {"keys": [...]} JWK Set. Works with any standard OIDC provider.
# Example: https://authentik.example.com/application/o/myapp/jwks/
# Example: https://cognito-idp.{region}.amazonaws.com/{userPoolId}/.well-known/jwks.json
JWKS_URL = str_env("JWKS_URL", "")
# Algorithms we allow for JWT signing
ALLOWED_JWT_ALGORITHMS = list_env("ALLOWED_JWT_ALGORITHMS", ["RS256", "ES256", "ES512"])
# The request header from which the JWT is read.
# Use "Authorization" (default) for standard Bearer token auth (e.g. OIDC PKCE).
# Use "x-amzn-oidc-data" for backwards compatibility with AWS ALB OIDC headers.
JWT_HEADER_NAME = str_env("JWT_HEADER_NAME", "Authorization")
# Optional JWT claim that contains the user's email address.
JWT_EMAIL_CLAIM = str_env("JWT_EMAIL_CLAIM", "email")
# Optional JWT claim that contains the user's preferred username.
JWT_USERNAME_CLAIM = str_env("JWT_USERNAME_CLAIM", "preferred_username")
# The JWT claim that contains the user's subject identifier.
# The OIDC standard claim is "sub" and it should not be changed in most cases.
JWT_SUB_CLAIM = str_env("JWT_SUB_CLAIM", "sub")
# The JWT claim that contains the token issuer.
# The OIDC standard claim is "iss" and it should not be changed in most cases.
JWT_ISS_CLAIM = str_env("JWT_ISS_CLAIM", "iss")
# Optional issuer to validate in the JWT. Leave empty to skip issuer validation.
JWT_ISSUER = str_env("JWT_ISSUER", "")
# Optional audience to validate in the JWT. Leave empty to skip audience validation.
JWT_AUDIENCE = str_env("JWT_AUDIENCE", "")
# OIDC configuration surfaced to the frontend via GET /api/v1/config.
# When DEVELOPMENT_ONLY_REQUIRE_AUTH is True, these are included in the config
# response so the frontend can build its UserManager without build-time env vars.
OIDC_AUTHORITY = str_env("OIDC_AUTHORITY", "")
# Internal authority URL used by the server to fetch OIDC discovery documents.
# In most deployments this equals OIDC_AUTHORITY. Set this when the server
# cannot reach the public OIDC_AUTHORITY hostname (e.g. docker dev environments
# with split internal/external hostnames). Defaults to OIDC_AUTHORITY when unset.
OIDC_INTERNAL_AUTHORITY = str_env("OIDC_INTERNAL_AUTHORITY", "")
OIDC_CLIENT_ID = str_env("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = str_env("OIDC_CLIENT_SECRET", "")
OIDC_TOKEN_ENDPOINT_AUTH_METHOD = str_env("OIDC_TOKEN_ENDPOINT_AUTH_METHOD", "none")
OIDC_REVOCATION_ENDPOINT_AUTH_METHOD = str_env(
    "OIDC_REVOCATION_ENDPOINT_AUTH_METHOD",
    OIDC_TOKEN_ENDPOINT_AUTH_METHOD,
)
OIDC_REDIRECT_URI = str_env("OIDC_REDIRECT_URI", "")
# Default includes offline_access so the BFF gets a refresh_token and can
# renew silently via direct POST to the token endpoint.
OIDC_SCOPE = str_env("OIDC_SCOPE", "openid email offline_access")
# Extra query parameters appended to the OIDC authorization request, as a
# comma-separated list of key=value pairs. Use for provider-specific knobs
# that the standard scope can't express. The canonical example is Google,
# which issues a refresh token only when the authorize request carries
# "access_type=offline" (and "prompt=consent" to re-issue one on repeat
# logins) rather than honoring the offline_access scope:
#   OIDC_AUTHORIZE_EXTRA_PARAMS="access_type=offline,prompt=consent"
OIDC_AUTHORIZE_EXTRA_PARAMS = _parse_kv_pairs(list_env("OIDC_AUTHORIZE_EXTRA_PARAMS", []))
# Enable RFC 7662 token introspection as a fallback when a Bearer token is
# not a verifiable JWT. Required for IDPs that issue opaque access tokens
# (e.g. Google, some Okta/Auth0 configurations without an API audience).
# Introspection authenticates to the IDP with the configured client
# credentials, so it generally pairs with a confidential client.
OIDC_ENABLE_TOKEN_INTROSPECTION = bool_env("OIDC_ENABLE_TOKEN_INTROSPECTION", False)
# Authlib client-auth method for the introspection endpoint. Defaults to the
# token-endpoint method (authlib uses that for introspection by default).
OIDC_INTROSPECTION_ENDPOINT_AUTH_METHOD = str_env(
    "OIDC_INTROSPECTION_ENDPOINT_AUTH_METHOD",
    OIDC_TOKEN_ENDPOINT_AUTH_METHOD,
)
# How long (seconds) to cache the IDP's OIDC discovery document before
# re-fetching. Endpoints rarely move, so a long TTL is fine; a non-infinite
# one means rotated endpoints/JWKS recover without a process restart.
OIDC_DISCOVERY_CACHE_TTL_SECONDS = int_env("OIDC_DISCOVERY_CACHE_TTL_SECONDS", 3600)
# Validate the OIDC ID token returned by the BFF code exchange (signature via
# the discovery JWKS, audience, issuer, and the login nonce). Secure by
# default; disable only for non-conformant providers whose ID token can't be
# verified server-side.
OIDC_VALIDATE_ID_TOKEN = bool_env("OIDC_VALIDATE_ID_TOKEN", True)

# Whether or not to require authentication.
# This option should only be changed in development.
DEVELOPMENT_ONLY_REQUIRE_AUTH = bool_env("DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
# The email address of the fake user when authentication is disabled.
# This option should only be changed in development.
DEVELOPMENT_ONLY_AUTH_USER_EMAIL = str_env(
    "DEVELOPMENT_ONLY_AUTH_USER_EMAIL",
    "testuser",
)

# URI to connect to neo4j
NEO4J_URI = str_env("NEO4J_URI", "bolt://localhost:7687")

# Minimum severity level for Neo4j query notifications logged by the driver.
# Valid values: WARNING (default), INFORMATION, OFF.
# Set to OFF to suppress schema warnings about missing labels/properties when
# the database is not fully populated (e.g. in development).
NEO4J_NOTIFICATIONS_MIN_SEVERITY = str_env("NEO4J_NOTIFICATIONS_MIN_SEVERITY", "WARNING")

# Username to connect to neo4j
NEO4J_USER = str_env("NEO4J_USER")

# Password to use for neo4j connection
NEO4J_PASSWORD = str_env("NEO4J_PASSWORD")

# Maximum duration in seconds a driver will keep a connection before being
# removed from its connection pool.
NEO4J_MAX_CONNECTION_LIFETIME = int_env("NEO4J_MAX_CONNECTION_LIFETIME", 3600)

# Timeout in seconds for establishing a Neo4j TCP connection.
NEO4J_CONNECTION_TIMEOUT = int_env("NEO4J_CONNECTION_TIMEOUT", 10)

# Timeout in seconds for Neo4j query execution (server-side transaction timeout).
NEO4J_QUERY_TIMEOUT = int_env("NEO4J_QUERY_TIMEOUT", 30)

# Procedures the Cypher query validator permits, in addition to the built-in
# read-only schema procedures allowed by default (db.labels, db.propertyKeys,
# db.schema.*, etc.). Each comma-separated entry is either an exact procedure
# name (e.g. "apoc.meta.stats") or a namespace prefix ending in a dot (e.g.
# "apoc." or "gds."). This only permits CALL procedure invocations; dangerous
# function namespaces such as `apoc.cypher.*` / `gds.*` remain blocked.
# Empty by default — only side-effect-free schema procedures are allowed.
# Note: write/schema/DBMS procedures stay blocked by the EXPLAIN read-only
# check regardless of this setting.
QUERY_VALIDATOR_ALLOWED_PROCEDURES = list_env("QUERY_VALIDATOR_ALLOWED_PROCEDURES", [])

# Shared secret used to sign report-query capability tokens.
# Required in normal authenticated deployments. Use a cryptographically random
# value with at least 32 bytes of entropy; 64 bytes is preferred. Encode as hex
# or base64, store it in a secret manager or env var, and keep it stable across
# restarts so report tokens remain valid until they expire. If you use hex,
# 32 bytes = 64 characters and 64 bytes = 128 characters. If you use base64,
# 32 bytes is typically 44 characters with padding. Rotate if exposed.
# In development auth-disabled mode, Seizu can fall back to an in-process
# default so local work still runs.
REPORT_QUERY_SIGNING_SECRET = str_env("REPORT_QUERY_SIGNING_SECRET", "")

# AES-256-GCM key used to encrypt the IDP refresh token stored in the
# browser session cookie. Must be exactly 32 bytes after base64 decoding.
# Generate with: python -c 'import base64,os;print(base64.b64encode(os.urandom(32)).decode())'
# Rotate if exposed; rotation invalidates all outstanding browser sessions
# (users will be forced to log in again).
SESSION_TOKEN_ENCRYPTION_KEY = str_env("SESSION_TOKEN_ENCRYPTION_KEY", "")

# Name of the session cookie that carries the encrypted IDP refresh token.
SESSION_COOKIE_NAME = str_env("SESSION_COOKIE_NAME", "seizu_session")

# Lifetime of the session cookie, in seconds. The cookie is rolling: each
# successful /api/v1/auth/refresh re-issues it with this Max-Age reset,
# capped by the IDP refresh token's own absolute expiry (recorded in the
# cookie at login). Default: 18 hours.
SESSION_COOKIE_MAX_AGE_SECONDS = int_env("SESSION_COOKIE_MAX_AGE_SECONDS", 18 * 60 * 60)

# Whether to revoke the OIDC refresh token on logout in addition to clearing
# the session cookie. Set False for IDPs that don't advertise or support
# RFC 7009 revocation. Failures are caught and logged; the user's local
# logout still succeeds.
OIDC_REVOKE_REFRESH_TOKEN_ON_LOGOUT = bool_env("OIDC_REVOKE_REFRESH_TOKEN_ON_LOGOUT", True)

# Fallback absolute upper bound on the session, in seconds, used when the
# IDP's token response doesn't advertise ``refresh_expires_in``. Most IDPs
# do advertise it; Authentik's default refresh-token lifetime is 30 days,
# which we mirror here. This is the cap on rolling re-issues — the cookie
# never extends past iat + this many seconds without the IDP confirming.
OIDC_REFRESH_TOKEN_FALLBACK_TTL_SECONDS = int_env(
    "OIDC_REFRESH_TOKEN_FALLBACK_TTL_SECONDS",
    30 * 24 * 60 * 60,
)

# Whether or not scheduled queries should be enabled.
ENABLE_SCHEDULED_QUERIES = bool_env("ENABLE_SCHEDULED_QUERIES", True)
# The frequency in seconds for how often we'll attempt to run scheduled queries
SCHEDULED_QUERY_FREQUENCY = int_env("SCHEDULED_QUERY_FREQUENCY", 20)
# Scheduled query modules
SCHEDULED_QUERY_MODULES = list_env(
    "SCHEDULED_QUERY_MODULES",
    [
        "reporting.scheduled_query_modules.sqs",
        "reporting.scheduled_query_modules.slack",
        "reporting.scheduled_query_modules.statsd",
        "reporting.scheduled_query_modules.temporal",
    ],
)
# NOTE: scheduled query module settings are defined within the modules themselves

# Whether scheduled chats (recurring headless agent runs managed from the chat
# UI) are enabled: gates the /api/v1/chat/schedules routes, the frontend UI,
# and the scheduled chats worker. Requires CHAT_ENABLED.
CHAT_SCHEDULES_ENABLED = bool_env("CHAT_SCHEDULES_ENABLED", True)
# How often (seconds) the scheduled chats worker polls for due schedules.
CHAT_SCHEDULES_POLL_SECONDS = int_env("CHAT_SCHEDULES_POLL_SECONDS", 20)
# Timeout in seconds for one scheduled headless agent session.
CHAT_SCHEDULE_TIMEOUT_SECONDS = int_env("CHAT_SCHEDULE_TIMEOUT_SECONDS", 600)

# Temporal server address (host:port of the frontend/gRPC endpoint), e.g.
# "temporal:7233" in docker compose.
TEMPORAL_ADDRESS = str_env("TEMPORAL_ADDRESS", "localhost:7233")
# Temporal namespace workflows run in. The start-dev server provides "default".
TEMPORAL_NAMESPACE = str_env("TEMPORAL_NAMESPACE", "default")
# Task queue the Seizu temporal worker polls and the scheduled query temporal
# action submits workflows to.
TEMPORAL_TASK_QUEUE = str_env("TEMPORAL_TASK_QUEUE", "seizu-workflows")
# Whether the temporal worker process (python -m reporting.temporal_worker)
# should run. Lets the same image/deployment disable the worker via env.
TEMPORAL_WORKER_ENABLED = bool_env("TEMPORAL_WORKER_ENABLED", True)
# Maximum number of scheduled query result rows forwarded into a workflow
# (Temporal payloads are capped at ~2MB; excess rows are dropped with a warning).
TEMPORAL_WORKFLOW_MAX_RESULT_ROWS = int_env("TEMPORAL_WORKFLOW_MAX_RESULT_ROWS", 200)
# Which registered Temporal workflows the temporal scheduled-query action may
# start. Enabling the temporal module (SCHEDULED_QUERY_MODULES) otherwise makes
# every registered workflow dispatchable; this narrows that to an allowlist.
# Empty or unset → all registered workflows. Comma-separated names (e.g.
# "cve_repo_report") → only those. Unknown names are ignored. The schema's
# workflow picker only offers enabled workflows, and dispatch refuses disabled
# ones. Set this on both the web service (for the picker) and the scheduled
# query worker (for enforcement).
TEMPORAL_ENABLED_WORKFLOWS = list_env("TEMPORAL_ENABLED_WORKFLOWS", [])
# Per-activity timeout in seconds for AI chat sessions run by workflows.
TEMPORAL_CHAT_ACTIVITY_TIMEOUT_SECONDS = int_env("TEMPORAL_CHAT_ACTIVITY_TIMEOUT_SECONDS", 600)

# ---------------------------------------------------------------------------
# CVE dependency remediation (cve_dependency_remediation workflow)
# ---------------------------------------------------------------------------

# The cve_dependency_remediation Temporal workflow runs a headless coding-agent
# CLI (Claude Code by default) in an ephemeral sandbox: it clones the affected
# repo, upgrades the vulnerable dependency (with any code changes needed for
# compatibility), runs the tests, and opens a PR. Credentials are
# phase-isolated: the coding agent never runs with the GitHub token in its
# environment. There is no dedicated enable flag and no per-user permission:
# the workflow runs only when configured (REMEDIATION_GITHUB_TOKEN + an agent
# API key) and is reachable only through the temporal scheduled-query action
# module (SCHEDULED_QUERY_MODULES) via admin-managed scheduled queries —
# disable the scheduled query or remove this configuration to turn it off.

# --- Sandbox coding-agent (shared by any sandbox-agent workflow/tool) --------
# The provider/credential settings for a headless coding-agent CLI run inside a
# sandbox (reporting/services/sandbox_agent.py). Generic on purpose: the
# remediation workflow is one consumer, but the machinery is reusable.

# Which coding-agent CLI to run: "claude" (Claude Code), "codex", or "opencode".
# opencode is multi-provider — set SANDBOX_AGENT_MODEL to a "provider/model" id
# (e.g. "deepseek/deepseek-chat") and the matching provider key is used, falling
# back to the same global *_API_KEY the chat assistant uses (e.g. DEEPSEEK_API_KEY).
SANDBOX_AGENT_PROVIDER = str_env("SANDBOX_AGENT_PROVIDER", "claude")

# E2B sandbox template for the agent run. Empty → the provider's official
# prebuilt template (E2B ships "claude"/"codex" images with the CLI installed),
# which avoids a per-run npm install and its postinstall scripts. A template
# name → that template. The literal "none" → the plain base image (the run
# installs the CLI itself). Ignored on self-hosted backends (SANDBOX_DOMAIN set)
# since templates are an E2B-cloud feature; the install step covers those.
# The template provides tools only, not credentials — phase isolation is intact.
SANDBOX_AGENT_TEMPLATE = str_env("SANDBOX_AGENT_TEMPLATE", "")

# API key for the coding-agent CLI (exported only to the agent phase, e.g. as
# ANTHROPIC_API_KEY). Empty → falls back to the model provider's global
# *_API_KEY (ANTHROPIC_API_KEY for claude/codex; for opencode, the one matching
# the model prefix, e.g. DEEPSEEK_API_KEY). Prefer SANDBOX_AGENT_API_KEY_COMMAND
# for short-lived per-run keys. NOTE for opencode: this key must belong to the
# provider named in SANDBOX_AGENT_MODEL — an Anthropic key with a
# "deepseek/…" model is exported as DEEPSEEK_API_KEY and will fail auth.
SANDBOX_AGENT_API_KEY = str_env("SANDBOX_AGENT_API_KEY", "")

# Optional command run in the worker before each agent run; its stdout
# (stripped) becomes the agent API key for that run. Use this to mint
# short-lived credentials from a broker (e.g. Vault, an LLM-gateway virtual
# key issuer) instead of handing the sandbox the long-lived key attached to
# the Seizu process. Takes precedence over SANDBOX_AGENT_API_KEY.
SANDBOX_AGENT_API_KEY_COMMAND = str_env("SANDBOX_AGENT_API_KEY_COMMAND", "")

# Optional base URL exported to the agent phase as the provider's base-url env
# var (ANTHROPIC_BASE_URL / OPENAI_BASE_URL), so the coding agent talks to an
# LLM gateway/proxy — typically paired with SANDBOX_AGENT_API_KEY_COMMAND
# so the sandbox only ever holds a short-lived gateway key.
SANDBOX_AGENT_BASE_URL = str_env("SANDBOX_AGENT_BASE_URL", "")

# Model for the coding-agent CLI. For claude/codex a bare model override
# (e.g. "claude-sonnet-4-6" for Claude Code's ANTHROPIC_MODEL); empty → the
# CLI's default. For opencode this is required and takes the form
# "provider/model" (e.g. "deepseek/deepseek-chat"), passed as --model.
SANDBOX_AGENT_MODEL = str_env("SANDBOX_AGENT_MODEL", "")

# Ephemeral credential-proxy sandbox. When true (and the provider uses a base
# URL — claude/codex, not opencode), a *second, separate* sandbox runs a
# short-lived LiteLLM proxy holding the real provider key, and the agent sandbox
# gets only a budget-capped virtual key pointed at that proxy. The real key never
# enters the untrusted agent VM, and the virtual key dies when the proxy sandbox
# is torn down (its lifetime == the run), so a leak is worthless after the run.
# Off by default. Mutually exclusive with SANDBOX_AGENT_BASE_URL. Requires a
# real key (SANDBOX_AGENT_API_KEY or the global provider key) to seed the
# proxy — the key command is not used in this mode.
SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED = bool_env("SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED", False)
# Max spend (USD) allowed on the per-run virtual key — bounds real-time abuse of
# a key stolen while the proxy is up.
SANDBOX_AGENT_CREDENTIAL_PROXY_MAX_BUDGET = str_env("SANDBOX_AGENT_CREDENTIAL_PROXY_MAX_BUDGET", "5")

# Hard timeout for one remediation run (all sandbox phases). A full clone →
# upgrade → test → PR cycle on a large repo can take tens of minutes.
REMEDIATION_TIMEOUT_SECONDS = int_env("REMEDIATION_TIMEOUT_SECONDS", 1800)

# Optional expected SHA-256 of the pinned gh linux_amd64 release tarball. When
# set, the install verifies gh against this out-of-band digest (an independent
# pin) instead of the release's own checksums file. Since the installed gh later
# handles the GitHub token, set this (or bake gh into a pinned sandbox image) for
# a supply-chain guarantee. Empty → verify against the release checksums only.
REMEDIATION_GH_SHA256 = str_env("REMEDIATION_GH_SHA256", "")

# GitHub host the target repositories live on. "github.com" or a GitHub
# Enterprise Server hostname (e.g. "github.example.com").
REMEDIATION_GITHUB_HOST = str_env("REMEDIATION_GITHUB_HOST", "github.com")

# GitHub token used to clone the repo (setup phase) and push/open the PR (push
# phase) — never present while the coding agent runs. Use a fine-grained PAT
# restricted to the target org/repos with contents:write + pull_requests:write,
# and keep branch protection on: PR review is the gate.
REMEDIATION_GITHUB_TOKEN = str_env("REMEDIATION_GITHUB_TOKEN", "")

# git author identity for the remediation commits.
REMEDIATION_GIT_USER = str_env("REMEDIATION_GIT_USER", "seizu-remediation-bot")
REMEDIATION_GIT_EMAIL = str_env("REMEDIATION_GIT_EMAIL", "seizu-remediation@localhost")

# Timeout in seconds for the overall FastAPI request handling. Requests that
# exceed this limit receive a 504 response.
API_REQUEST_TIMEOUT = int_env("API_REQUEST_TIMEOUT", 60)

# Timeout in seconds for JWKS endpoint HTTP requests used to fetch signing keys.
JWKS_FETCH_TIMEOUT = int_env("JWKS_FETCH_TIMEOUT", 10)

# Connection and read timeouts (in seconds) for AWS boto3 clients (DynamoDB, SQS).
AWS_CONNECT_TIMEOUT = int_env("AWS_CONNECT_TIMEOUT", 5)
AWS_READ_TIMEOUT = int_env("AWS_READ_TIMEOUT", 30)

# Timeout in seconds for SQL statement execution (asyncpg/PostgreSQL only).
SQL_STATEMENT_TIMEOUT = int_env("SQL_STATEMENT_TIMEOUT", 30)

# Timeout in seconds for Slack API calls.
SLACK_TIMEOUT = int_env("SLACK_TIMEOUT", 30)

# Whether to enable HSTS (HTTP Strict Transport Security) headers.
# Set to True in production to enforce HTTPS. Disable in development or when
# running behind an SSL-terminating load balancer.
TALISMAN_FORCE_HTTPS = bool_env("TALISMAN_FORCE_HTTPS", True)

# DynamoDB settings for report config storage
# Name of the DynamoDB table used to store report configs and version history
DYNAMODB_TABLE_NAME = str_env("DYNAMODB_TABLE_NAME", "seizu-reports")
# AWS region for DynamoDB. Falls back to boto3 default chain if unset.
DYNAMODB_REGION = str_env("DYNAMODB_REGION", "us-east-1")
# Override the DynamoDB endpoint URL, e.g. http://dynamodb:8000 for local dev.
# Leave empty to use the default AWS endpoint.
DYNAMODB_ENDPOINT_URL = str_env("DYNAMODB_ENDPOINT_URL", "")
# When true, the table is created automatically on startup if it does not exist.
# Enable this in local development against DynamoDB Local.
DYNAMODB_CREATE_TABLE = bool_env("DYNAMODB_CREATE_TABLE", False)
# Snowflake ID generator machine ID (0–1023). Set a unique value per instance
# when running multiple replicas to avoid ID collisions.
SNOWFLAKE_MACHINE_ID = int_env("SNOWFLAKE_MACHINE_ID", 1)
# Report storage backend. Supported values: "dynamodb" (default), "sqlmodel".
REPORT_STORE_BACKEND = str_env("REPORT_STORE_BACKEND", "dynamodb")
# SQLAlchemy database URL used when REPORT_STORE_BACKEND=sqlmodel. Keep
# credentials out of this value and provide them through SQL_DATABASE_USER and
# SQL_DATABASE_PASSWORD so secret managers can manage only the password.
# Credential-bearing URLs remain supported for backward compatibility.
# Any SQLAlchemy-compatible URL works (PostgreSQL, SQLite, MySQL, etc.).
# Example: postgresql://postgres:5432/seizu
# Example: sqlite:///./seizu.db
SQL_DATABASE_URL = str_env("SQL_DATABASE_URL", "")
# Optional credentials overlaid on SQL_DATABASE_URL.
SQL_DATABASE_USER = str_env("SQL_DATABASE_USER", "")
SQL_DATABASE_PASSWORD = str_env("SQL_DATABASE_PASSWORD", "")

# Master switch for the chat assistant. When false the chat routes are not
# registered, checkpoint storage is not initialized, and the frontend hides the
# Chat UI (surfaced via GET /api/v1/config -> features.chat).
CHAT_ENABLED = bool_env("CHAT_ENABLED", True)

# LLM provider sentinel for the chat assistant. "mock" keeps local/dev chat
# deterministic and keyless; any other value routes through LiteLLM, so the
# supported provider/model surface is whatever LiteLLM supports rather than a
# fixed allowlist. Legacy values ("openai", "anthropic", "gemini", "deepseek")
# still work and namespace a bare CHAT_LLM_MODEL; new deployments can leave this
# at "litellm" and set a fully-qualified CHAT_LLM_MODEL instead.
CHAT_LLM_PROVIDER = str_env("CHAT_LLM_PROVIDER", "mock")
# LiteLLM model identifier for the chat assistant. Required whenever
# CHAT_LLM_PROVIDER is not "mock"; Seizu fails fast at startup if a real provider
# is selected without one. Prefer a provider-namespaced string
# (e.g. "openai/gpt-4o", "anthropic/claude-3-5-sonnet-latest",
# "gemini/gemini-2.0-flash", "deepseek/deepseek-reasoner"). A bare model name is
# namespaced using the legacy CHAT_LLM_PROVIDER value.
CHAT_LLM_MODEL = str_env("CHAT_LLM_MODEL", "")
# Optional API key override passed to LiteLLM. If empty, the legacy
# provider-specific env vars below are used, then LiteLLM's own per-provider
# environment lookup applies (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.).
CHAT_LLM_API_KEY = str_env("CHAT_LLM_API_KEY", "")
# Optional OpenAI-compatible base URL (LiteLLM api_base). Set this to point chat
# at a self-hosted LiteLLM proxy or other gateway/private endpoint; it now
# applies regardless of which model/provider is selected.
CHAT_LLM_BASE_URL = str_env("CHAT_LLM_BASE_URL", "")
# Generation controls for real chat providers.
CHAT_LLM_TEMPERATURE = float_env("CHAT_LLM_TEMPERATURE", 0.2)
# Per-call output token cap. Kept generous so most answers finish in one shot;
# replies still truncated by it are auto-continued server-side (see below).
CHAT_LLM_MAX_TOKENS = int_env("CHAT_LLM_MAX_TOKENS", 4096)
CHAT_LLM_TIMEOUT_SECONDS = int_env("CHAT_LLM_TIMEOUT_SECONDS", 60)
CHAT_LLM_MAX_RETRIES = int_env("CHAT_LLM_MAX_RETRIES", 2)
# When a final answer is cut off by the output-token limit (finish_reason
# "length"), Seizu transparently asks the model to continue and stitches the
# pieces into one seamless response. These bound that loop. Set MAX_CONTINUATIONS
# to 0 to disable auto-continuation (falling back to the manual "Continue
# response" button). MAX_RESPONSE_CHARS is a hard ceiling on the stitched length
# (0 disables it); the loop also stops as soon as a continuation adds no new text.
CHAT_LLM_MAX_CONTINUATIONS = int_env("CHAT_LLM_MAX_CONTINUATIONS", 2)
CHAT_LLM_MAX_RESPONSE_CHARS = int_env("CHAT_LLM_MAX_RESPONSE_CHARS", 60_000)
# Maximum prior messages/characters sent to the LLM. Checkpoints may retain
# more messages for UI history; this separate cap controls model cost, latency,
# and provider context pressure.
CHAT_LLM_CONTEXT_MAX_MESSAGES = int_env("CHAT_LLM_CONTEXT_MAX_MESSAGES", 80)
CHAT_LLM_CONTEXT_MAX_CHARS = int_env("CHAT_LLM_CONTEXT_MAX_CHARS", 120_000)
# Optional full prompt override. Leave empty to use Seizu's provider-aware
# security-dashboard prompt.
CHAT_LLM_SYSTEM_PROMPT = str_env("CHAT_LLM_SYSTEM_PROMPT", "")
# When true, the model sees available skills first and lets rendered skills
# disclose which tools to use. When false, the model sees both chat-safe tools
# and skills up front, matching the normal MCP list-tools/list-prompts shape.
CHAT_LLM_PROGRESSIVE_DISCLOSURE = bool_env("CHAT_LLM_PROGRESSIVE_DISCLOSURE", True)
# Maximum model-requested structured skill/tool calls the chat agent will execute
# during one assistant turn. This bounds progressive skill rendering plus
# follow-on tool calls so a model cannot loop indefinitely.
CHAT_LLM_MAX_AUTO_ACTIONS = int_env("CHAT_LLM_MAX_AUTO_ACTIONS", 12)
# Maximum model-requested tool calls to run concurrently during one auto-action
# batch. Tool handlers are async, so this uses asyncio concurrency rather than
# a threadpool for the normal Neo4j/store I/O path.
CHAT_LLM_MAX_PARALLEL_TOOL_CALLS = int_env("CHAT_LLM_MAX_PARALLEL_TOOL_CALLS", 4)

# Plan -> dispatch -> verify orchestration for complex chat requests. When off,
# every turn takes the existing single-agent (gather -> act) path; the router
# node short-circuits to "simple" with no extra LLM call, so behavior is
# unchanged. When on, a cheap router classifies each turn and routes multi-step
# requests through a planner, a dispatcher that runs scoped sub-agent workers
# (parallel when steps are independent), and a verify gate with bounded retry.
CHAT_ORCHESTRATOR_ENABLED = bool_env("CHAT_ORCHESTRATOR_ENABLED", True)
# Maximum number of steps the planner may emit for one orchestrated turn.
CHAT_ORCHESTRATOR_MAX_STEPS = int_env("CHAT_ORCHESTRATOR_MAX_STEPS", 8)
# Planner generation budget. Thinking models need more room than the compact
# router/verifier schemas so their final JSON is not crowded out by reasoning.
CHAT_ORCHESTRATOR_PLANNER_MAX_TOKENS = int_env("CHAT_ORCHESTRATOR_PLANNER_MAX_TOKENS", 4096)
# Maximum verify-driven retry cycles before the orchestrator synthesizes an
# answer from whatever steps passed. Bounds self-correction so a persistently
# failing step cannot loop forever.
CHAT_ORCHESTRATOR_MAX_ITERATIONS = int_env("CHAT_ORCHESTRATOR_MAX_ITERATIONS", 3)
# Maximum independent steps the dispatcher runs concurrently in one batch.
CHAT_ORCHESTRATOR_MAX_PARALLEL = int_env("CHAT_ORCHESTRATOR_MAX_PARALLEL", 3)
# Compatibility guard for runs with all shared budget dimensions disabled.
# Normal interactive and headless plans use the shared run-level
# token/cost/call ledger instead of stopping at a per-step action count.
CHAT_ORCHESTRATOR_WORKER_MAX_ACTIONS = int_env("CHAT_ORCHESTRATOR_WORKER_MAX_ACTIONS", 24)
# Per-turn chat orchestrator budget shared by interactive and automated runs.
# The reserve is unavailable to normal planning/worker calls and is released
# only for final summaries/synthesis.
# A zero token or cost limit disables that dimension; the LLM-call ceiling
# remains an emergency loop guard.
CHAT_RUN_TOKEN_BUDGET = int_env("CHAT_RUN_TOKEN_BUDGET", 120_000)
CHAT_RUN_COST_BUDGET_USD = float_env("CHAT_RUN_COST_BUDGET_USD", 0.0)
CHAT_RUN_RESERVE_PERCENT = int_env("CHAT_RUN_RESERVE_PERCENT", 20)
CHAT_RUN_SOFT_LIMIT_PERCENT = int_env("CHAT_RUN_SOFT_LIMIT_PERCENT", 75)
CHAT_RUN_MAX_LLM_CALLS = int_env("CHAT_RUN_MAX_LLM_CALLS", 64)
# Optional role-specific models. Empty values inherit CHAT_LLM_MODEL. The
# economy model is selected for read-only worker/synthesis calls after the run
# crosses its soft budget limit.
CHAT_LLM_PLANNER_MODEL = str_env("CHAT_LLM_PLANNER_MODEL", "")
CHAT_LLM_WORKER_MODEL = str_env("CHAT_LLM_WORKER_MODEL", "")
CHAT_LLM_VERIFIER_MODEL = str_env("CHAT_LLM_VERIFIER_MODEL", "")
CHAT_LLM_SYNTHESIZER_MODEL = str_env("CHAT_LLM_SYNTHESIZER_MODEL", "")
CHAT_LLM_ECONOMY_MODEL = str_env("CHAT_LLM_ECONOMY_MODEL", "")

# Standard provider API key env vars. These are intentionally not exposed via
# GET /api/v1/config.
OPENAI_API_KEY = str_env("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = str_env("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = str_env("GEMINI_API_KEY", "")
GOOGLE_API_KEY = str_env("GOOGLE_API_KEY", "")
DEEPSEEK_API_KEY = str_env("DEEPSEEK_API_KEY", "")

# LangGraph checkpoint backend. Supported values: "dynamodb" (default),
# "postgres". The PostgreSQL checkpointer is independent of the report store,
# though the development Makefile switchers change both together.
CHAT_CHECKPOINT_BACKEND = str_env("CHAT_CHECKPOINT_BACKEND", "dynamodb")
# PostgreSQL connection URL used when CHAT_CHECKPOINT_BACKEND=postgres. Defaults
# to SQL_DATABASE_URL so SQL-backed deployments can share one database. Keep
# credentials separate using the settings below; credential-bearing URLs remain
# supported for backward compatibility.
# The LangGraph PostgreSQL checkpointer requires PostgreSQL; SQLite/MySQL URLs
# supported by the report store are not valid here.
CHAT_CHECKPOINT_DATABASE_URL = str_env("CHAT_CHECKPOINT_DATABASE_URL", "") or SQL_DATABASE_URL
# Optional checkpoint-specific credentials. Empty values inherit the main SQL
# credentials, allowing one secret to be shared or independently overridden.
CHAT_CHECKPOINT_DATABASE_USER = str_env("CHAT_CHECKPOINT_DATABASE_USER", "") or SQL_DATABASE_USER
CHAT_CHECKPOINT_DATABASE_PASSWORD = str_env("CHAT_CHECKPOINT_DATABASE_PASSWORD", "") or SQL_DATABASE_PASSWORD
# Per-process async PostgreSQL connection pool bounds for chat checkpoints.
CHAT_CHECKPOINT_DATABASE_POOL_MIN_SIZE = int_env("CHAT_CHECKPOINT_DATABASE_POOL_MIN_SIZE", 1)
CHAT_CHECKPOINT_DATABASE_POOL_MAX_SIZE = int_env("CHAT_CHECKPOINT_DATABASE_POOL_MAX_SIZE", 10)
# Dedicated DynamoDB table used by LangGraph to persist chat checkpoints when
# CHAT_CHECKPOINT_BACKEND=dynamodb.
CHAT_CHECKPOINT_TABLE_NAME = str_env("CHAT_CHECKPOINT_TABLE_NAME", "seizu-chat-checkpoints")
# When true, create/migrate the configured LangGraph checkpoint storage at startup.
CHAT_CHECKPOINT_CREATE_TABLE = bool_env("CHAT_CHECKPOINT_CREATE_TABLE", False)
# DynamoDB-only optional checkpoint TTL in seconds. Empty/0 disables automatic expiry.
CHAT_CHECKPOINT_TTL_SECONDS = int_env("CHAT_CHECKPOINT_TTL_SECONDS", 0)
# DynamoDB-only compression for serialized checkpoint payloads.
CHAT_CHECKPOINT_ENABLE_COMPRESSION = bool_env("CHAT_CHECKPOINT_ENABLE_COMPRESSION", True)
# S3 bucket used by langgraph-checkpoint-aws for payloads larger than 350KB.
CHAT_CHECKPOINT_S3_BUCKET = str_env("CHAT_CHECKPOINT_S3_BUCKET", "")
# Optional S3 endpoint override, e.g. http://minio:9000 for local development.
CHAT_CHECKPOINT_S3_ENDPOINT_URL = str_env("CHAT_CHECKPOINT_S3_ENDPOINT_URL", "")
# Optional S3 object prefix for checkpoint offload isolation.
CHAT_CHECKPOINT_S3_KEY_PREFIX = str_env("CHAT_CHECKPOINT_S3_KEY_PREFIX", "seizu/langgraph")
# Maximum persisted LangGraph messages per chat thread. Older turns are removed
# from checkpoint state after each non-ephemeral turn.
CHAT_MAX_PERSISTED_MESSAGES = int_env("CHAT_MAX_PERSISTED_MESSAGES", 200)
# Default number of messages returned by GET /api/v1/chat/history.
CHAT_HISTORY_LIMIT = int_env("CHAT_HISTORY_LIMIT", 100)
# Maximum rows returned to chat from a single MCP tool call. Normal MCP calls are
# unaffected; this caps model/UI context growth on the chat path.
CHAT_TOOL_RESULT_MAX_ROWS = int_env("CHAT_TOOL_RESULT_MAX_ROWS", 100)
# Maximum serialized bytes returned to chat from a single MCP tool call.
CHAT_TOOL_RESULT_MAX_BYTES = int_env("CHAT_TOOL_RESULT_MAX_BYTES", 200_000)

# Maximum lifetime for an approved or denied mutating-action confirmation.
ACTION_CONFIRMATION_TTL_SECONDS = int_env("ACTION_CONFIRMATION_TTL_SECONDS", 1800)

# Optional public browser origin used when MCP clients need to show a user an
# approval URL. When unset, Seizu derives the origin from MCP_RESOURCE_URL.
SEIZU_PUBLIC_URL = str_env("SEIZU_PUBLIC_URL", "")

# The JWT claim that contains the user's Seizu role name.
# Configure your OIDC provider to embed the role (e.g. "seizu-admin") directly
# as a claim in the token. Common claim names: "seizu_role", "role".
RBAC_ROLE_CLAIM = str_env("RBAC_ROLE_CLAIM", "seizu_role")

# Default role assigned when a user's JWT has no RBAC_ROLE_CLAIM.
# Set to "" to deny access to users without an explicit role claim.
# Valid values: "seizu-viewer", "seizu-editor", "seizu-admin", or any user-defined role name.
RBAC_DEFAULT_ROLE = str_env("RBAC_DEFAULT_ROLE", "seizu-viewer")

# Whether to enable the MCP server at /api/v1/mcp.
MCP_ENABLED = bool_env("MCP_ENABLED", True)

# Which built-in MCP tool groups are exposed.
# Unset or empty → all groups enabled (default).
# "none"         → all built-in groups disabled (user-defined toolsets unaffected).
# Comma-separated list (e.g. "graph,reports") → only those groups.
# Known groups: graph, reports, roles, sandbox, scheduled_queries, skillsets, toolsets.
# Note: the sandbox group is chat-only (never exposed via the MCP server endpoint
# regardless of this setting) and also requires SANDBOX_ENABLED=true.
MCP_ENABLED_BUILTINS = list_env("MCP_ENABLED_BUILTINS", [])

# ---------------------------------------------------------------------------
# Sandbox delegation (sandbox__delegate chat tool)
# ---------------------------------------------------------------------------

# Set to true to enable the sandbox__delegate tool in the chat agent.
# Requires SANDBOX_API_KEY when using E2B (https://e2b.dev).
# For self-hosted sandboxes (e.g. OpenKruise Agents), set SANDBOX_DOMAIN to
# the sandbox service hostname — the E2B-compatible API is used in both cases.
SANDBOX_ENABLED = bool_env("SANDBOX_ENABLED", False)

# Hostname of the sandbox API (no scheme, no trailing slash).
# Empty → E2B's default cloud endpoint.
# For OpenKruise Agents: set to your cluster's sandbox ingress hostname.
SANDBOX_DOMAIN = str_env("SANDBOX_DOMAIN", "")

# API key for the sandbox service.  Required when using E2B.
# Leave empty for self-hosted deployments that use internal auth.
SANDBOX_API_KEY = str_env("SANDBOX_API_KEY", "")

# Allow sandboxes to make outbound internet connections.
# Defaults to false: sandboxes are network-isolated from the public internet.
# Set to true only when the task explicitly requires external network access
# (e.g. fetching a URL, cloning a public repo).
SANDBOX_ALLOW_INTERNET = bool_env("SANDBOX_ALLOW_INTERNET", False)

# Hard timeout for a single sandbox__delegate invocation (seconds).
SANDBOX_TIMEOUT_SECONDS = int_env("SANDBOX_TIMEOUT_SECONDS", 120)

# Maximum bytes of sandbox agent output returned to the outer chat agent.
SANDBOX_MAX_OUTPUT_BYTES = int_env("SANDBOX_MAX_OUTPUT_BYTES", 50_000)

# LiteLLM model id for the sandbox subagent.  Empty → inherits CHAT_LLM_MODEL.
# Example: "anthropic/claude-haiku-4-5-20251001" for a cheaper inner agent.
SANDBOX_LLM_MODEL = str_env("SANDBOX_LLM_MODEL", "")

# OAuth 2.0 Authorization Server Metadata (RFC 8414) for MCP clients.
# When set, Seizu exposes /.well-known/oauth-authorization-server so MCP clients
# (e.g. Claude Desktop) can discover the OAuth flow and authenticate users
# without requiring a pre-issued token.
# Set these to the authorization and token endpoints of your OIDC provider.
# Example (Authentik): https://authentik.example.com/application/o/seizu/authorize/
# Leave empty to disable the metadata endpoint.
MCP_OAUTH_ISSUER = str_env("MCP_OAUTH_ISSUER", "")
MCP_OAUTH_AUTHORIZATION_ENDPOINT = str_env("MCP_OAUTH_AUTHORIZATION_ENDPOINT", "")
MCP_OAUTH_TOKEN_ENDPOINT = str_env("MCP_OAUTH_TOKEN_ENDPOINT", "")
# Public base URL of the MCP endpoint (e.g. https://seizu.example.com/api/v1/mcp).
# Required for OAuth discovery: used in the WWW-Authenticate resource_metadata
# header and the RFC 9728 protected resource metadata document.
# Leave empty to disable protected-resource metadata.
MCP_RESOURCE_URL = str_env("MCP_RESOURCE_URL", "")
# Override the RFC 7591 dynamic client registration endpoint advertised in the
# OAuth metadata. When unset and both MCP_RESOURCE_URL and OIDC_CLIENT_ID are
# configured, Seizu serves its own lightweight DCR endpoint that returns the
# pre-configured OIDC_CLIENT_ID so MCP clients don't need a DCR-capable IdP.
MCP_OAUTH_REGISTRATION_ENDPOINT = str_env("MCP_OAUTH_REGISTRATION_ENDPOINT", "")
