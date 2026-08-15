import os
from importlib import resources

from cartography_sync.registry import parse_enabled_modules
from reporting.schema.external_mcp import parse_external_mcp_proxies
from reporting.utils.settings import bool_env, float_env, int_env, list_env, str_env

_DEFAULT_SANDBOX_CORE_TOOLS = ["graph__query", "graph__schema", "graph__validate_query", "graph__explain"]


def _core_tools_from_env() -> list[str]:
    """``SANDBOX_CORE_TOOLS``, where *set but empty* means an empty list.

    Not ``list_env``: that treats an empty value as absent and hands back the
    default, so ``SANDBOX_CORE_TOOLS=`` — the documented way to bind nothing —
    silently kept all four graph tools. Caught by the harness's read-back check
    rather than by review.

    Deliberately not fixed inside ``list_env``: ``ALLOWED_JWT_ALGORITHMS`` also
    carries a non-empty default, and letting a stray empty assignment empty
    *that* is a security change, not a convenience.
    """
    raw = os.environ.get("SANDBOX_CORE_TOOLS")
    if raw is None:
        return list(_DEFAULT_SANDBOX_CORE_TOOLS)
    return [name.strip() for name in raw.split(",") if name.strip()]


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
#
# Both hostnames must present the SAME "issuer" value. Durable Seizu identity is
# (iss, sub), so an IDP that derives its issuer from the request host hands the
# same person two user records -- one per authentication path -- and everything
# owner-scoped (private reports, query history, chat threads, scheduled chats,
# action confirmations) silently diverges between them. Seizu compares the two
# discovery documents at startup and logs the mismatch; see AUTH-001 in
# docs/root/dev/decisions/authentication.md.
OIDC_INTERNAL_AUTHORITY = str_env("OIDC_INTERNAL_AUTHORITY", "")
# Make that startup issuer comparison fatal instead of advisory: refuse to start
# when the internal and external authorities advertise different issuers. Off by
# default because the mismatch is only reachable with a split-hostname
# deployment, and a loud log lets an existing one keep running while it is fixed.
OIDC_REQUIRE_CONSISTENT_ISSUER = bool_env("OIDC_REQUIRE_CONSISTENT_ISSUER", False)
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

# How long (seconds) to cache the introspected graph schema process-wide. The
# schema is graph-wide rather than per-user, so one cache serves the schema
# route, the graph__schema tool, the MCP server and the sandbox subagent. Agents
# re-introspect constantly — each sandbox delegation starts a fresh subagent
# with no memory — so this turns a per-call cost into a per-TTL one. Lower it if
# a sync adds labels that must appear immediately; 0 disables caching.
GRAPH_SCHEMA_CACHE_TTL_SECONDS = int_env("GRAPH_SCHEMA_CACHE_TTL_SECONDS", 300)

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

# Legacy scheduler settings are retained as aliases for one compatibility
# release. Configurable workflows are executed and scheduled by Temporal.
ENABLE_SCHEDULED_QUERIES = bool_env("ENABLE_SCHEDULED_QUERIES", False)
SCHEDULED_QUERY_FREQUENCY = int_env("SCHEDULED_QUERY_FREQUENCY", 20)
_DEFAULT_WORKFLOW_ACTIVITY_MODULES = [
    "reporting.scheduled_query_modules.sqs",
    "reporting.scheduled_query_modules.slack",
    "reporting.scheduled_query_modules.statsd",
]
WORKFLOW_ACTIVITY_MODULES = list_env(
    "WORKFLOW_ACTIVITY_MODULES",
    list_env("SCHEDULED_QUERY_MODULES", _DEFAULT_WORKFLOW_ACTIVITY_MODULES),
)
SCHEDULED_QUERY_MODULES = WORKFLOW_ACTIVITY_MODULES
WORKFLOW_QUERY_MAX_ROWS = int_env("WORKFLOW_QUERY_MAX_ROWS", 200)
WORKFLOW_RESULT_MAX_BYTES = int_env("WORKFLOW_RESULT_MAX_BYTES", 1_000_000)
WORKFLOW_WATCH_POLL_SECONDS = int_env("WORKFLOW_WATCH_POLL_SECONDS", 20)
WORKFLOW_RECONCILE_SECONDS = int_env("WORKFLOW_RECONCILE_SECONDS", 30)
# NOTE: scheduled query module settings are defined within the modules themselves

# Whether scheduled chats (recurring headless agent runs managed from the chat
# UI) are enabled: gates the /api/v1/chat/schedules routes, the frontend UI,
# and the scheduled chats worker. Requires CHAT_ENABLED.
CHAT_SCHEDULES_ENABLED = bool_env("CHAT_SCHEDULES_ENABLED", True)
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
# Which registered code-defined Temporal workflows are available as top-level
# workflow activity types. Empty or unset → all registered workflows.
# Comma-separated names (e.g. "cve_repo_report") → only those. Unknown names
# are ignored. Disabled workflows don't appear as activity types in the
# workflow editor, and dispatch refuses them. Set this on both the web service
# (for the editor) and the temporal worker (for enforcement).
TEMPORAL_ENABLED_WORKFLOWS = list_env("TEMPORAL_ENABLED_WORKFLOWS", [])
# Per-activity timeout in seconds for AI chat sessions run by workflows.
TEMPORAL_CHAT_ACTIVITY_TIMEOUT_SECONDS = int_env("TEMPORAL_CHAT_ACTIVITY_TIMEOUT_SECONDS", 600)

# ---------------------------------------------------------------------------
# Cartography syncs (cartography_sync workflow)
# ---------------------------------------------------------------------------

# The cartography_sync Temporal workflow runs cartography intel-module syncs
# as a staged pipeline. The workflow itself runs in the main temporal worker;
# its per-module activities run on a separate task queue served by the
# dedicated cartography sync worker image (Dockerfile.cartography, service
# seizu-cartography-worker), which holds only cartography intel credentials.
# Task queue the sync worker polls; the workflow dispatches its module
# activities there.
CARTOGRAPHY_TASK_QUEUE = str_env("CARTOGRAPHY_TASK_QUEUE", "seizu-cartography")
# Which registry modules (cartography_sync/registry.py) scheduled syncs may
# run. Empty or unset → all registered modules; comma-separated names narrow
# the allowlist (e.g. "aws,github,cve"). Set on the web service (config
# validation + UI options) and the scheduled query worker (dispatch).
CARTOGRAPHY_ENABLED_MODULES = parse_enabled_modules(str_env("CARTOGRAPHY_ENABLED_MODULES"))
# Default per-module-run subprocess timeout (seconds); overridable per
# scheduled query via the workflow's timeout_minutes config field.
CARTOGRAPHY_MODULE_TIMEOUT_SECONDS = int_env("CARTOGRAPHY_MODULE_TIMEOUT_SECONDS", 3600)
# How long a module run may wait (seconds) for an overlapping run of the same
# module to finish. Overlap across pipelines, schedules, ticks, and worker
# replicas serializes on the cartography_module child workflow's fixed
# workflow ID (concurrent same-module syncs race on cartography's update tags
# and can delete each other's data).
CARTOGRAPHY_MODULE_WAIT_SECONDS = int_env("CARTOGRAPHY_MODULE_WAIT_SECONDS", 3600)
# Temporal retry attempts for one module-run activity (config errors never
# retry).
CARTOGRAPHY_SYNC_RETRY_ATTEMPTS = int_env("CARTOGRAPHY_SYNC_RETRY_ATTEMPTS", 2)

# ---------------------------------------------------------------------------
# CVE dependency remediation (cve_dependency_remediation workflow)
# ---------------------------------------------------------------------------

# The cve_dependency_remediation Temporal workflow runs a headless coding-agent
# CLI (Claude Code by default) in an ephemeral sandbox: it clones the affected
# repo, upgrades the vulnerable dependency (with any code changes needed for
# compatibility), and opens a PR. The agent does not run the test suite (the
# sandbox usually lacks its dependencies); CI runs the tests on the PR.
# Credentials are phase-isolated: the coding agent never runs with the GitHub token in its
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
# (e.g. "deepseek/deepseek-v4-pro") and the matching provider key is used, falling
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
# "provider/model" (e.g. "deepseek/deepseek-v4-pro"), passed as --model.
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
# Hash-locked requirement file the proxy sandbox installs when no template is
# configured. Empty → the checked-in lock
# (reporting/services/sandbox_proxy_requirements.txt), regenerated by
# `make lock_proxy_requirements`. Point this at your own compiled lock to run a
# different LiteLLM, or one resolved for a different sandbox runtime — a lock
# records the python/architecture it was resolved for and the install refuses to
# proceed on a sandbox that does not match. Every requirement in it is pinned
# with hashes: an unpinned install resolves to whatever PyPI served that day,
# transitively too, which is how the proxy broke in the field.
SANDBOX_AGENT_CREDENTIAL_PROXY_REQUIREMENTS_FILE = str_env("SANDBOX_AGENT_CREDENTIAL_PROXY_REQUIREMENTS_FILE", "")
# E2B template for the proxy sandbox — either one built by
# `make build_proxy_template` from the lock above, or any image of your own that
# provides a working `litellm` proxy. Set → the run uses that image as built,
# installs nothing, and checks nothing: what it contains is entirely yours to
# manage. Empty → the base image plus a run-time install from the lock.
SANDBOX_AGENT_CREDENTIAL_PROXY_TEMPLATE = str_env("SANDBOX_AGENT_CREDENTIAL_PROXY_TEMPLATE", "")

# Hard timeout for one remediation run (all sandbox phases). A full clone →
# upgrade → test → PR cycle on a large repo can take tens of minutes.
REMEDIATION_TIMEOUT_SECONDS = int_env("REMEDIATION_TIMEOUT_SECONDS", 1800)

# --- Post-push CI watch --------------------------------------------------
# After pushing a PR the workflow watches its check suite so a bump that
# breaks CI is not silently left failing: relevant failures get a coding-agent
# fix run (pushed to the same branch), irrelevant ones get an explanatory PR
# comment. All watching is durable Temporal timers plus a short read-only
# GitHub API activity in the worker — no sandbox is opened until a fix run is
# actually needed.

# Total time to wait for one PR's checks to settle (including re-runs after a
# fix push). 0 disables the CI watch entirely.
REMEDIATION_CI_MAX_WAIT_SECONDS = int_env("REMEDIATION_CI_MAX_WAIT_SECONDS", 3600)

# How often to poll the PR's check status while waiting.
REMEDIATION_CI_POLL_SECONDS = int_env("REMEDIATION_CI_POLL_SECONDS", 120)

# A check still queued (never started) after this long is ignored by the watch
# — CI that never schedules a runner (offline self-hosted runner, disabled
# app) would otherwise stall every poll until the max wait.
REMEDIATION_CI_QUEUED_STUCK_SECONDS = int_env("REMEDIATION_CI_QUEUED_STUCK_SECONDS", 1800)

# Max coding-agent CI-fix runs per PR (each is a full sandbox agent session —
# this bounds spend). 0 → watch and record the CI outcome but never fix.
REMEDIATION_CI_FIX_MAX_ATTEMPTS = int_env("REMEDIATION_CI_FIX_MAX_ATTEMPTS", 1)

# Optional expected SHA-256 of the pinned gh linux_amd64 release tarball. When
# set, the install verifies gh against this out-of-band digest (an independent
# pin) instead of the release's own checksums file. Since the installed gh later
# handles the GitHub token, set this (or bake gh into a pinned sandbox image) for
# a supply-chain guarantee. Empty → verify against the release checksums only.
REMEDIATION_GH_SHA256 = str_env("REMEDIATION_GH_SHA256", "")

# GitHub CLI (gh) version the sandbox installs when it isn't already present
# (base image / self-hosted backends). Pinned rather than "latest" to avoid
# version drift; bump deliberately. Pair with REMEDIATION_GH_SHA256 for an
# independent supply-chain pin of that version's tarball.
REMEDIATION_GH_VERSION = str_env("REMEDIATION_GH_VERSION", "2.62.0")

# GitHub host the target repositories live on. "github.com" or a GitHub
# Enterprise Server hostname (e.g. "github.example.com").
REMEDIATION_GITHUB_HOST = str_env("REMEDIATION_GITHUB_HOST", "github.com")

# GitHub token used to clone the repo (setup phase), push/open the PR (push
# phase) — never present while the coding agent runs — and by the worker-side
# CI watch (read checks, post triage comments). Direct mode: a fine-grained
# PAT scoped to the target repos (contents + pull requests write; issues
# write and checks/commit-statuses read for the CI watch). Fork mode
# (REMEDIATION_USE_FORK): a fine-grained PAT cannot span the bot's forks and
# another owner's targets (and fork creation needs Administration:write) —
# use a classic PAT on a machine account (public_repo, or repo for private
# targets). Full walkthrough: docs/root/install/temporal-workflows.md,
# "GitHub token setup". Keep branch protection on: PR review is the gate.
REMEDIATION_GITHUB_TOKEN = str_env("REMEDIATION_GITHUB_TOKEN", "")

# Fork-based flow: when true, the push phase pushes the work branch to a
# bot-owned fork of the target repository (created on demand via the GitHub
# API) and opens a cross-repo PR (fork-owner:branch → target base branch)
# instead of writing a branch into the target repo. Use when the token cannot
# — or should not — push to the target repositories. CI-fix runs clone and
# push the PR branch on the fork; credential phase isolation is unchanged.
REMEDIATION_USE_FORK = bool_env("REMEDIATION_USE_FORK", False)

# Organization that owns the bot forks when REMEDIATION_USE_FORK is on; empty
# → forks are created under the token user's account.
REMEDIATION_FORK_ORG = str_env("REMEDIATION_FORK_ORG", "")

# git author identity for the remediation commits.
REMEDIATION_GIT_USER = str_env("REMEDIATION_GIT_USER", "seizu-remediation-bot")
REMEDIATION_GIT_EMAIL = str_env("REMEDIATION_GIT_EMAIL", "seizu-remediation@localhost")

# Timeout in seconds for overall FastAPI request handling and the Gunicorn
# worker watchdog. Ordinary requests that exceed this limit receive a 504.
API_REQUEST_TIMEOUT = int_env("API_REQUEST_TIMEOUT", 60)

# Timeout in seconds for JWKS endpoint HTTP requests used to fetch signing keys.
JWKS_FETCH_TIMEOUT = int_env("JWKS_FETCH_TIMEOUT", 10)

# Connection and read timeouts (in seconds) for AWS boto3 clients such as SQS.
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

# Snowflake ID generator machine ID (0–1023). Set a unique value per instance
# when running multiple replicas to avoid ID collisions.
SNOWFLAKE_MACHINE_ID = int_env("SNOWFLAKE_MACHINE_ID", 1)
# PostgreSQL database URL for Seizu-managed application records. Keep
# credentials out of this value and provide them through SQL_DATABASE_USER and
# SQL_DATABASE_PASSWORD so secret managers can manage only the password.
# Credential-bearing URLs remain supported for backward compatibility.
# Example: postgresql://postgres:5432/seizu
SQL_DATABASE_URL = str_env("SQL_DATABASE_URL", "")
# Optional credentials overlaid on SQL_DATABASE_URL.
SQL_DATABASE_USER = str_env("SQL_DATABASE_USER", "")
SQL_DATABASE_PASSWORD = str_env("SQL_DATABASE_PASSWORD", "")

_LEGACY_PERSISTENCE_SETTINGS = frozenset(
    {
        "REPORT_STORE_BACKEND",
        "DYNAMODB_TABLE_NAME",
        "DYNAMODB_REGION",
        "DYNAMODB_ENDPOINT_URL",
        "DYNAMODB_CREATE_TABLE",
        "CHAT_CHECKPOINT_BACKEND",
        "CHAT_CHECKPOINT_TABLE_NAME",
        "CHAT_CHECKPOINT_ENABLE_COMPRESSION",
        "CHAT_CHECKPOINT_S3_BUCKET",
        "CHAT_CHECKPOINT_S3_ENDPOINT_URL",
        "CHAT_CHECKPOINT_S3_KEY_PREFIX",
        "CHAT_CHECKPOINT_TTL_SECONDS",
    }
)


def validate_persistence_settings() -> None:
    """Reject removed persistence selectors before any database is touched."""
    configured = sorted(name for name in _LEGACY_PERSISTENCE_SETTINGS if name in os.environ)
    if not configured:
        return
    names = ", ".join(configured)
    raise RuntimeError(
        "Legacy DynamoDB persistence configuration is no longer supported: "
        f"{names}. Migrate durable data before upgrading, remove these settings, "
        "and configure SQL_DATABASE_* plus CHAT_CHECKPOINT_DATABASE_* instead. "
        "See docs/root/install/upgrading.md#migrating-from-dynamodb-to-postgresql."
    )


# Master switch for the chat assistant. When false the chat routes are not
# registered, checkpoint storage is not initialized, and the frontend hides the
# Chat UI (surfaced via GET /api/v1/config -> features.chat).
CHAT_ENABLED = bool_env("CHAT_ENABLED", False)

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
# Maximum prior messages/tokens sent to the LLM. Checkpoints may retain more
# messages for UI history; this separate cap controls model cost, latency, and
# provider context pressure.
CHAT_LLM_CONTEXT_MAX_MESSAGES = int_env("CHAT_LLM_CONTEXT_MAX_MESSAGES", 80)
# Tokens, counted with the provider's own tokenizer. This replaced a
# 120,000-*character* cap: measured against real tool payloads the conversion is
# 3.0 chars/token rather than the 4 the code assumed, so the old cap admitted
# about a third more tokens than intended, and worst exactly when payloads were
# largest (structured data tokenizes worse than prose). 40,000 is that cap's
# measured token equivalent. 0 means "whatever the model's window allows".
CHAT_LLM_CONTEXT_MAX_TOKENS = int_env("CHAT_LLM_CONTEXT_MAX_TOKENS", 40_000)
# Share of the model's input window that history may occupy. The remainder is
# for the system prompt, tool schemas, the session digest, this turn's tool
# results and the reply. The effective history budget is the smaller of this and
# CHAT_LLM_CONTEXT_MAX_TOKENS -- a ceiling, not a target, so pointing Seizu at a
# million-token model does not silently multiply the cost of every call.
CHAT_LLM_CONTEXT_WINDOW_SHARE = float_env("CHAT_LLM_CONTEXT_WINDOW_SHARE", 0.5)
# Override the model's input window instead of taking it from litellm's model
# database. 0 derives it, which is almost always right.
CHAT_LLM_CONTEXT_WINDOW_TOKENS = int_env("CHAT_LLM_CONTEXT_WINDOW_TOKENS", 0)
# Window assumed for a model litellm does not know -- typically self-hosted or
# custom. Small on purpose: guessing low wastes part of a window, guessing high
# fails the turn. Raise it (or set CHAT_LLM_CONTEXT_WINDOW_TOKENS) when running
# a large-context model litellm cannot identify.
CHAT_LLM_CONTEXT_WINDOW_FALLBACK_TOKENS = int_env("CHAT_LLM_CONTEXT_WINDOW_FALLBACK_TOKENS", 32_768)
# Fraction of the window held back when sizing a call. Our count is an
# under-estimate by construction -- providers frame each message with tokens we
# never see, and a tokenizer resolved by name can differ from the one the
# endpoint runs -- and under-counting is the direction that fails the call.
CHAT_LLM_CONTEXT_SAFETY_MARGIN = float_env("CHAT_LLM_CONTEXT_SAFETY_MARGIN", 0.05)
# Emit explicit prompt-cache breakpoints for providers that need them (Anthropic
# caches nothing without them; measured at 0 cached tokens over a five-call turn,
# against 6,513 read back on the next call once a breakpoint was placed).
# Providers with automatic prefix caching are unaffected either way.
CHAT_LLM_PROMPT_CACHE_ENABLED = bool_env("CHAT_LLM_PROMPT_CACHE_ENABLED", True)
# Anthropic will not cache a prefix shorter than this, so a smaller system
# prompt is left unmarked rather than reshaped into blocks for nothing.
CHAT_LLM_PROMPT_CACHE_MIN_TOKENS = int_env("CHAT_LLM_PROMPT_CACHE_MIN_TOKENS", 1_024)
# Disclose the tools declared by the skills a plan step names, from the start of
# the step, instead of only once the skill renders. The declaration is the skill
# author naming exactly what the workflow uses, so there is nothing to learn by
# waiting -- and waiting churns the tool list mid-turn, which is the head of the
# provider's cached prefix and invalidates everything behind it. Scoped to the
# skills the step names: unioning every enabled skill's declaration describes
# the catalogue rather than the need, and took a measured turn from 1 bound tool
# to 43.
# The cost is a larger tool list up front, cached after the first call.
# Set false to disclose only on render.
CHAT_LLM_DISCLOSE_SKILL_TOOLS = bool_env("CHAT_LLM_DISCLOSE_SKILL_TOOLS", True)
# Log which part of a request (model, system prompt, tools, messages) changed
# since the previous call of the same kind, which is what a prompt-cache miss
# never tells you on its own. Off by default: it is a debugging aid, and it
# token-counts every component of every call. Anthropic ships an equivalent as a
# beta, but its beta header is one our LiteLLM version builds from feature
# detection and will not take from a caller, so the request is rejected outright
# -- and this works on providers with automatic prefix caching too, where no
# such feature exists.
CHAT_LLM_CACHE_DIAGNOSTICS = bool_env("CHAT_LLM_CACHE_DIAGNOSTICS", False)
# Condense the oldest turns of a long conversation instead of dropping them.
# Truncation lost what the conversation had said, and moved the boundary on
# every turn -- the worst shape for a prompt cache, since the prefix changed each
# time. Compaction cuts back past the budget in chunks so the condensed block
# stays byte-identical for the many turns it takes to refill.
CHAT_LLM_HISTORY_COMPACTION = bool_env("CHAT_LLM_HISTORY_COMPACTION", True)
# How far back a compaction cuts, as a fraction of the history budget. Lower
# compacts less often but keeps less history; at 1.0 it would compact on nearly
# every turn, which is the behaviour this replaced.
CHAT_LLM_HISTORY_COMPACTION_TARGET = float_env("CHAT_LLM_HISTORY_COMPACTION_TARGET", 0.5)
# Ceiling on the condensed block itself. It grows with the conversation, and
# something has to stop it; past this the oldest lines are shed, which is the one
# deeper prefix rewrite this design accepts.
CHAT_LLM_HISTORY_SUMMARY_MAX_TOKENS = int_env("CHAT_LLM_HISTORY_SUMMARY_MAX_TOKENS", 1_500)
# ...but only while that list stays small enough to be worth it: skills are
# user-authored, and disclosing a large declaration up front is just binding
# every tool on every call. Above this many tokens of tool schema, fall back to
# disclosing on render. Scale and rationale: CTX-006 in
# docs/root/dev/decisions/chat-context.md.
CHAT_LLM_DISCLOSE_SKILL_TOOLS_MAX_TOKENS = int_env("CHAT_LLM_DISCLOSE_SKILL_TOOLS_MAX_TOKENS", 6_000)
# Optional full prompt override. Leave empty to use Seizu's provider-aware
# security-dashboard prompt.
CHAT_LLM_SYSTEM_PROMPT = str_env("CHAT_LLM_SYSTEM_PROMPT", "")
# When true, the model sees available skills first and lets rendered skills
# disclose which tools to use. When false, the model sees both chat-safe tools
# and skills up front, matching the normal MCP list-tools/list-prompts shape.
# Also decides how a sandbox sub-agent reaches tools outside its bound set
# (SBX-004). Not an authorization boundary -- AGT-002.
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
# Characters of prior-step output a worker may be given, split across the step's
# dependencies. A dependency is the reason a step can do its job: at 2,000 each,
# a 19-finding CVE list reached the reachability step truncated, and the result
# was failed for the incomplete coverage that caused.
CHAT_ORCHESTRATOR_DEPENDENCY_CONTEXT_MAX_CHARS = int_env("CHAT_ORCHESTRATOR_DEPENDENCY_CONTEXT_MAX_CHARS", 16_000)
# Maximum verify-driven retry cycles before the orchestrator synthesizes an
# answer from whatever steps passed. Bounds self-correction so a persistently
# failing step cannot loop forever.
CHAT_ORCHESTRATOR_MAX_ITERATIONS = int_env("CHAT_ORCHESTRATOR_MAX_ITERATIONS", 3)
# Maximum independent steps the dispatcher runs concurrently in one batch.
CHAT_ORCHESTRATOR_MAX_PARALLEL = int_env("CHAT_ORCHESTRATOR_MAX_PARALLEL", 3)
# Compatibility guard for runs with all shared budget dimensions disabled.
# Normal interactive and headless plans use the shared run-level
# token/cost/call ledger instead of stopping at a per-step action count.
# A budgeted run is bounded by CHAT_ORCHESTRATOR_STEP_BUDGET_OVERRUN instead,
# which is proportional to the work the planner expected rather than a flat
# count, so a large headless plan is not truncated at an arbitrary action.
CHAT_ORCHESTRATOR_WORKER_MAX_ACTIONS = int_env("CHAT_ORCHESTRATOR_WORKER_MAX_ACTIONS", 24)
# Multiple of the planner's per-step token estimate at which a step is stopped
# and asked to summarize what it has. The estimate itself only downgrades the
# step to the economy model; stopping there would kill work the planner merely
# under-estimated. Without a ceiling one step can consume the whole run budget
# and starve every step after it.
# Raised from 3.0 once the ceiling began counting delegated sandbox spend. The
# planner's per-step estimates (4k/8k/16k by complexity) were set when a step's
# total meant its own loop only, so against a total that now includes a sandbox
# sub-agent they are an order of magnitude low: a medium step was cut at 24,000
# four times in a row, burning 121,643 tokens to fail four times where one
# uninterrupted attempt had answered completely. 12x puts a medium step near the
# ~80k per step the successful configuration actually used.
CHAT_ORCHESTRATOR_STEP_BUDGET_OVERRUN = float_env("CHAT_ORCHESTRATOR_STEP_BUDGET_OVERRUN", 12.0)
# How far past its fair share of the remaining run budget a step may go before
# it is stopped. 1.0 makes the share a hard cut; a large value lets one step use
# everything outside the finalization reserve. Below it the step is only
# degraded and asked to converge, so this is the point at which a step is ended
# rather than pressured.
#
# 1.0 because a three-arm, three-sample sweep (1.0 / 2.0 / effectively
# unbounded) found no discernible difference: every usable sample consumed the
# whole spendable budget, ended in finalization, and failed a verification, with
# within-arm spread (449-1,114 characters) as large as anything between arms.
# Chosen for the strongest sibling protection at no measured cost, not because
# it scored best. The ceiling is not the binding constraint -- the run budget
# is, and the work does not fit inside it.
# How far past its fair share of the run budget a step may go before it is
# stopped outright. The share exists so no step starves its siblings, which is a
# scheduling concern; at 1.0 it was also the execution cut, and so became the
# thing that ended every long investigation measured here -- four consecutive
# runs stopped on it while the run budget sat at ~80% unspent and the cost
# budget at ~16%. Above 1.0 the share still fires as a *signal* (the step
# degrades and is told to converge); what changes is that a step with no sibling
# contending can use what the run can actually spend. Useless work is now caught
# by loop detection rather than by rationing (AGT-017).
CHAT_ORCHESTRATOR_STEP_SHARE_HARD_MULTIPLE = float_env("CHAT_ORCHESTRATOR_STEP_SHARE_HARD_MULTIPLE", 3.0)
# Consecutive tool calls with nothing new in them before a step is stopped as
# stuck. A full window is required, so occasional legitimate repetition (polling,
# re-reading a file just written) does not trip it.
CHAT_ORCHESTRATOR_STUCK_CALL_WINDOW = int_env("CHAT_ORCHESTRATOR_STUCK_CALL_WINDOW", 8)
# Episodic recall between sub-agents within one step. Each sandbox__delegate
# call runs a fresh subagent that knows nothing of the previous one, so without
# this they re-derive the same ground -- one observed step made 136 delegations
# and 678 graph queries answering a question about eight CVEs. Entries are the
# prior sub-agents' own task/outcome pairs, replayed into the next one's prompt.
# Set the recall budget to 0 to disable.
CHAT_EPISODIC_RECALL_MAX_CHARS = int_env("CHAT_EPISODIC_RECALL_MAX_CHARS", 4_000)
# Entries retained before the oldest are shed. Bounds memory and keeps recall
# relevant: recent sub-agents cover ground the next one is likeliest to repeat.
CHAT_EPISODIC_MAX_ENTRIES = int_env("CHAT_EPISODIC_MAX_ENTRIES", 20)
# The same carry one scope out: what earlier *turns* of the conversation already
# established, held in the thread's checkpoint. Without it a follow-up turn
# re-ran the previous turn's queries on top of its own work, because nothing
# said they had been run. Entries are sub-agent task/outcome pairs; receipts are
# the files earlier turns left in the (now persistent) sandbox, which is the
# difference between reading data and fetching it again.
CHAT_SESSION_MEMORY_MAX_ENTRIES = int_env("CHAT_SESSION_MEMORY_MAX_ENTRIES", 30)
CHAT_SESSION_MEMORY_MAX_RECEIPTS = int_env("CHAT_SESSION_MEMORY_MAX_RECEIPTS", 40)
# Budget for the same material in the *top-level* agent's prompt (planner,
# worker, single-agent loop) rather than a sub-agent's. Smaller, because that
# model needs to know the data exists in order not to plan a re-fetch, not to
# work with it. Set 0 to disable the digest without disabling the carry.
CHAT_SESSION_MEMORY_DIGEST_MAX_CHARS = int_env("CHAT_SESSION_MEMORY_DIGEST_MAX_CHARS", 2_000)
# Corrective retries when a worker ends a turn without calling the sentinel that
# submits its step result. A step ends on that explicit call, never on the model
# simply going quiet, so a plain-text turn is a protocol violation the worker
# points out and re-asks. After this many retries it falls back to reading the
# text as the result, so a model that will not use the protocol still finishes.
CHAT_ORCHESTRATOR_WORKER_FINALIZE_RETRIES = int_env("CHAT_ORCHESTRATOR_WORKER_FINALIZE_RETRIES", 2)
# Characters of raw step evidence (the tool/skill output each worker gathered)
# carried into the synthesizer's context, shared across all steps of a plan.
# A worker's prose summary is a lossy channel: whatever it omits is gone, since
# nothing else crosses the step boundary. Passing the underlying evidence too
# means the synthesizer can still answer when a summary comes back thin. Set 0
# to send summaries only (the pre-existing behavior).
CHAT_ORCHESTRATOR_SYNTHESIS_EVIDENCE_MAX_CHARS = int_env("CHAT_ORCHESTRATOR_SYNTHESIS_EVIDENCE_MAX_CHARS", 12_000)
# Characters of earlier conversation given to the planner, so a follow-up whose
# subject is a back-reference ("cross-check that", "which of those findings")
# can be resolved into self-contained step goals. The orchestrated path is
# otherwise built only from the latest user message, which leaves such a request
# with no referent anywhere in the run. The planner makes one call per turn, so
# this is charged once.
CHAT_ORCHESTRATOR_PLANNER_CONTEXT_MAX_CHARS = int_env("CHAT_ORCHESTRATOR_PLANNER_CONTEXT_MAX_CHARS", 6_000)
# The same conversation given to each sub-agent worker, for resolving references
# in its step goal. Deliberately much tighter than the planner's: workers run in
# parallel and each runs its own multi-call loop, so this is charged per step
# per call, and a worker handed the whole transcript drifts toward answering the
# user's overall question instead of its own step. Set 0 to restore strict
# worker isolation (the pre-existing behavior).
CHAT_ORCHESTRATOR_WORKER_CONTEXT_MAX_CHARS = int_env("CHAT_ORCHESTRATOR_WORKER_CONTEXT_MAX_CHARS", 2_000)
# Per-turn chat orchestrator budget shared by interactive and automated runs.
# The reserve is unavailable to normal planning/worker calls and is released
# only for final summaries/synthesis.
# A zero token or cost limit disables that dimension; the LLM-call ceiling
# remains an emergency loop guard.
# Sized to cover sandbox sub-agent spend, which this budget now includes. Before
# that spend was metered, a delegating turn billed only its outer loop -- two
# measured turns put the sandbox at 69% and 84% of real usage -- so the previous
# 120k default was, for such turns, closer to 400k in practice. Lower it if
# delegation is disabled or rare; a turn that never delegates spends the same as
# it always did.
CHAT_RUN_TOKEN_BUDGET = int_env("CHAT_RUN_TOKEN_BUDGET", 400_000)
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

# PostgreSQL connection URL used for LangGraph chat checkpoints. Defaults
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
# When true, create/migrate the LangGraph checkpoint schema at startup.
CHAT_CHECKPOINT_CREATE_TABLE = bool_env("CHAT_CHECKPOINT_CREATE_TABLE", False)
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
# Bounds for a normal (non-chat) MCP tool call. Separate from the chat caps
# above, which exist to protect a model's context and are far tighter: an
# external MCP client is not a model context and has never been limited by one.
# These exist so the server has a finite bound at all -- a broad query is
# otherwise materialized in full before anything can trim it -- and are set well
# above what any chat turn permits. A result that hits them is returned
# truncated with a marker rather than failing.
MCP_TOOL_RESULT_MAX_ROWS = int_env("MCP_TOOL_RESULT_MAX_ROWS", 50_000)
MCP_TOOL_RESULT_MAX_BYTES = int_env("MCP_TOOL_RESULT_MAX_BYTES", 25_000_000)

# Maximum lifetime for an approved or denied mutating-action confirmation.
ACTION_CONFIRMATION_TTL_SECONDS = int_env("ACTION_CONFIRMATION_TTL_SECONDS", 1800)

# How long a finished chat turn's event log stays replayable. This is the window
# a client has to reconnect and replay a turn; it is not conversation history
# (that lives in the checkpoint), so it can be short.
CHAT_TURN_RETENTION_SECONDS = int_env("CHAT_TURN_RETENTION_SECONDS", 600)
# Target added latency for flushing and reading stream batches. The idle poll
# ceiling is derived from it; the two sides cannot be tuned into disagreement.
CHAT_TURN_STREAM_LATENCY_MS = int_env("CHAT_TURN_STREAM_LATENCY_MS", 200)
# How long one interactive turn may run before its workflow gives up. The
# activity gets this plus a margin; a turn that hits it is recorded as failed
# rather than left running. It is also what bounds a *running* turn's lease --
# see ``chat_turn_lease_expiry``.
CHAT_TURN_TIMEOUT_SECONDS = int_env("CHAT_TURN_TIMEOUT_SECONDS", 900)

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
# Known groups: graph, reports, roles, sandbox, scheduled_queries, skillsets, spaces, toolsets.
# Note: the sandbox group is chat-only (never exposed via the MCP server endpoint
# regardless of this setting) and also requires SANDBOX_ENABLED=true.
MCP_ENABLED_BUILTINS = list_env("MCP_ENABLED_BUILTINS", [])

# External MCP servers reached through an identity-aware proxy. JSON array; see
# docs/root/install/external-mcp.md. Parsing at startup makes malformed security
# mappings a configuration error rather than silently dropping a proxy.
MCP_EXTERNAL_ENABLED = bool_env("MCP_EXTERNAL_ENABLED", False)
_MCP_EXTERNAL_CONFIGURED_PROXIES = parse_external_mcp_proxies(str_env("MCP_EXTERNAL_PROXIES", ""))
MCP_EXTERNAL_PROXIES = _MCP_EXTERNAL_CONFIGURED_PROXIES if MCP_EXTERNAL_ENABLED else []

# Fully namespaced external tools that always require confirmation, regardless
# of remote MCP annotations or a proxy's fallback policy.
MCP_EXTERNAL_CONFIRMATION_REQUIRED_TOOLS = list_env("MCP_EXTERNAL_CONFIRMATION_REQUIRED_TOOLS", [])

# ---------------------------------------------------------------------------
# Sandbox delegation (sandbox__delegate chat tool)
# ---------------------------------------------------------------------------

# Identifies this installation in the metadata of every sandbox it creates, and
# is the only ownership claim the reaper acts on. Set it whenever the sandbox
# credentials are shared with another Seizu installation (production and
# staging on one E2B account, say) -- deployments that leave it unset share one
# bucket and can therefore collect each other's sandboxes.
SEIZU_DEPLOYMENT_ID = str_env("SEIZU_DEPLOYMENT_ID", "")

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

# Tools bound to every sandbox delegation regardless of progressive disclosure,
# because "fetch some data" is what a sub-agent is for and the default set is
# what that means when no specific tool has been named.
#
# These bypass *disclosure*, not RBAC: each is intersected with the caller's
# permitted tools, so a role without query:execute gets none of them. Set empty
# to bind nothing up front and route even graph access through a skill (or
# through the delegating model naming `tools`) -- at the cost of a discovery
# round trip on the most ordinary thing a delegation does. See SBX-003 in
# docs/root/dev/decisions/sandbox.md.
SANDBOX_CORE_TOOLS = _core_tools_from_env()

# Maximum bytes of sandbox agent output returned to the outer chat agent.
SANDBOX_MAX_OUTPUT_BYTES = int_env("SANDBOX_MAX_OUTPUT_BYTES", 50_000)
# Caps for a sub-agent tool result written to a sandbox file rather than
# returned. Far larger than the in-context caps because the context window they
# protect is not involved: the agent computes over the file with run_python and
# never reads the rows itself. Still finite -- write_file takes a string, so the
# whole result materializes in the Seizu process before reaching the sandbox,
# and an unbounded query would be a memory event here rather than there.
#
# Bytes of a file the sub-agent may pull into context. Above 0 the agent gets
# preview_file (files at or under this size whole; larger ones shape plus the
# beginning). At 0 it gets read_file, capped at SANDBOX_MAX_OUTPUT_BYTES.
# On by default as a design choice with no measured effect either way -- see
# SBX-002 in docs/root/dev/decisions/sandbox.md.
SANDBOX_PREVIEW_MAX_BYTES = int_env("SANDBOX_PREVIEW_MAX_BYTES", 2_000)

# Lifetime of the sandbox shared by a turn's delegations. Longer than
# SANDBOX_TIMEOUT_SECONDS (which bounds one delegation) because the sandbox has
# to outlive a whole turn; the provider would otherwise reap it mid-turn.
SANDBOX_SESSION_TIMEOUT_SECONDS = int_env("SANDBOX_SESSION_TIMEOUT_SECONDS", 1_800)
# Suspend the sandbox between turns instead of destroying it, and resume it on
# the next turn of the same thread, so a follow-up turn reads files earlier
# turns wrote instead of re-running their queries. Pausing keeps only the
# full VM state including memory (keep_memory=True), so untrusted processes DO
# survive into the next turn of that thread -- accepted deliberately, because
# filesystem-only suspension leaves the code interpreter dead. See SBX-005.
#
# A thread the user abandons rather than deletes leaves its sandbox suspended;
# SANDBOX_REAP_* below is what reclaims those. Set false to opt out of
# persistence entirely.
SANDBOX_SESSION_PERSIST = bool_env("SANDBOX_SESSION_PERSIST", True)

# Retire chat sessions nobody has come back to, destroying the suspended
# sandbox each one holds along with it. THIS DELETES CHAT HISTORY: a session
# untouched for CHAT_SESSION_REAP_IDLE_SECONDS is removed, transcript included.
# A sandbox belongs to its thread for as long as the thread exists, so the
# session is the unit -- reaping the sandbox alone would leave a conversation
# whose accumulated files silently vanished.
#
# OFF by default, and deliberately so: retention is a policy an operator
# chooses, not something an upgrade should decide for them. Turning it on is
# what starts deleting; check CHAT_SESSION_REAP_IDLE_SECONDS first, because the
# first sweep after it goes on collects everything already past the threshold.
#
# Runs as a Temporal Schedule (fixed id, SKIP overlap), so a deployment without
# a Temporal worker does not reap. See SBX-011 in
# docs/root/dev/decisions/sandbox.md.
CHAT_SESSION_REAP_ENABLED = bool_env("CHAT_SESSION_REAP_ENABLED", False)
# How long a session may sit untouched before it is retired, measured from its
# last update (not its creation), so an active conversation is never at risk.
# 0 (or less) disables reaping entirely. Default 30 days.
CHAT_SESSION_REAP_IDLE_SECONDS = int_env("CHAT_SESSION_REAP_IDLE_SECONDS", 2_592_000)
# Interval between sweeps.
CHAT_SESSION_REAP_INTERVAL_SECONDS = int_env("CHAT_SESSION_REAP_INTERVAL_SECONDS", 3_600)
# Also collect suspended sandboxes tagged for another deployment, or not tagged
# at all. Off by default: the provider listing is account-wide, so those may
# belong to a sibling installation, another tool, or a person. Turn it on only
# when these credentials are this deployment's alone -- it is also how sandboxes
# created before tagging existed get cleaned up.
SANDBOX_REAP_UNTAGGED = bool_env("SANDBOX_REAP_UNTAGGED", False)
# How much a single delegation may return to the sub-agent *inline* before the
# rest is written to files instead. Cumulative, and in tokens, because the
# per-call bounds above cannot catch the shape that actually exhausts a step: a
# reachability review made 90 GitHub calls of a few KB each -- every one under
# the per-call trigger, 1.1M tokens in total, one receipt written. Set 0 to
# disable and keep only the per-call triggers.
SANDBOX_INLINE_RESULT_BUDGET_TOKENS = int_env("SANDBOX_INLINE_RESULT_BUDGET_TOKENS", 60_000)
# Consecutive already-answered calls before a delegation is told, in terms it
# can act on, that there is nothing further to get and it should report what it
# has. The per-call note says one call was pointless; a run of them says the
# task is (AGT-017).
SANDBOX_STUCK_REPEAT_LIMIT = int_env("SANDBOX_STUCK_REPEAT_LIMIT", 3)
SANDBOX_FILE_RESULT_MAX_ROWS = int_env("SANDBOX_FILE_RESULT_MAX_ROWS", 50_000)
SANDBOX_FILE_RESULT_MAX_BYTES = int_env("SANDBOX_FILE_RESULT_MAX_BYTES", 10_000_000)

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
