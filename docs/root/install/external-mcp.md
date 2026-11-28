# External MCP proxies

Per-user gateway delegation and recovery (`user_authorization`) are
**experimental**. Use only in controlled deployments after validating the
gateway's identity mapping and per-user authorization. See the
[validation checklist](https://github.com/mappedsky/seizu/issues/312).
This designation does not apply to ordinary shared-token external MCP access.

Seizu's chat agent can discover and call tools on external MCP servers through
an identity-aware proxy. The proxy remains responsible for authenticating to
the external service, storing or rotating its credentials, and enforcing its
own authorization policy. Seizu supplies a scoped user identity and, where
configured, a proxy bearer or machine credential.

External servers are deliberately available to the chat runtime only. Seizu
does not re-export them from `/api/v1/mcp`. Tool names are rewritten as
`ext__<proxy-name>__<remote-tool-name>`, preventing collisions with built-ins,
stored toolsets, or another proxy.

## In-chat input requests

Set `MCP_EXTERNAL_ELICITATION_ENABLED=true` on the web and Temporal worker
services, and opt each proxy into `"elicitation": {"form": true, "url": true}`.
Both kinds default to false. URL requests also require `user_authorization` with
a `reauthorize_url` on the same origin as the requested browser URL.

Interactive modern MCP calls display input cards in chat. Answer every card in
a group; submitting the last answer resumes the conversation. Once the resumed
call has taken the answers, its card clears and the paused entry in the turn's
details shows what the call returned. After a reload, an answered card's
**Continue chat** button resumes without resubmitting values.
The call uses its saved arguments and continuation state and checks permissions
and action approvals again. Expired or rejected upstream continuations require
a new request. Decline and Cancel are final decisions for that request.

`CHAT_ELICITATION_TTL_SECONDS` controls pending and answered request lifetime
(default 3600 seconds, bounded to 1–86400). `CHAT_ELICITATION_MAX_FIELDS` controls
the maximum field count (default and hard ceiling 32). Forms support flat
strings, numbers, integers, booleans and bounded enums, with at most 4096
characters per string. Unsupported schemas are rejected. A call may request
eight inputs; live limits are sixteen per turn and sixty-four per thread.

Submitted values go to the external server through protocol input responses.
They may appear in tool results, chat history, or AI model context; tool results
are not redacted. Do not enter passwords, API keys, access tokens, or verification
codes in forms. Use URL elicitation for credential collection directly on the
external service’s site. Answers are stored until consumption or expiry collection.
Database backups may retain earlier copies.
Deleting a chat removes its input records. Expired rows are collected while
interactive elicitation is enabled.

Legacy callbacks, discovery, scheduled runs and other detached work retain the
Chat Connections recovery flow. Sandbox subagents do not advertise forms. If
one nevertheless receives an input request, it returns with its existing work;
a later delegation can continue the matching call after the owner answers.

### Local elicitation fixture

Run the unauthenticated fixture only on a development machine:

```bash
docker compose run --rm --no-deps --name seizu-elicitation-demo -p 127.0.0.1:8099:8099 seizu uv run --frozen --no-sync python scripts/elicitation_mcp_server.py
```

Append this entry to `MCP_EXTERNAL_PROXIES`, enable
`MCP_EXTERNAL_ELICITATION_ENABLED`, and recreate the web and Temporal worker:

```json
{
  "name": "elicitation-demo",
  "url": "http://seizu-elicitation-demo:8099/mcp/",
  "transport": "streamable_http",
  "require_confirmation": false,
  "elicitation": {"form": true, "url": true},
  "user_authorization": {"reauthorize_url": "http://localhost:8099/complete"}
}
```

Ask chat to call `ext__elicitation-demo__ask_form` or
`ext__elicitation-demo__ask_url`. Submit, decline, cancel, and reload with a
pending card. The fixture returns the action without echoing field values.
Use the same proxy in a scheduled chat to check its recovery-only behavior.

## Proxy configuration

Set `MCP_EXTERNAL_ENABLED=true` and `MCP_EXTERNAL_PROXIES` to a JSON array. The web service and
`seizu-temporal-worker` must receive identical configuration and token
environment variables because interactive and headless turns execute in the
Temporal worker.

```text
MCP_EXTERNAL_ENABLED=true
MCP_EXTERNAL_PROXIES=[{"name":"drive","url":"https://mcp-proxy.example/mcp/drive","transport":"streamable_http","auth_mode":"m2m_jwt","token_env":"MCP_EXTERNAL_PROXY_TOKEN","header_mappings":{"subject":"X-Target-User-ID","issuer":"X-Target-User-Issuer","email":"X-Target-Email"}}]
MCP_EXTERNAL_PLUGIN_URL_MATCH_MODE=none
# Reuse a proxy's discovered tool listing across turns, per user (0 = off).
MCP_EXTERNAL_DISCOVERY_TTL_SECONDS=0
MCP_EXTERNAL_CONFIRMATION_REQUIRED_TOOLS=ext__drive__delete_file,ext__drive__share_file
MCP_EXTERNAL_PROXY_TOKEN=<short-lived-service-jwt>
```

Agent Plugin `mcp:<server>/<tool>` dependencies use the matching proxy name by
default; the package URL is ignored. Set `MCP_EXTERNAL_PLUGIN_URL_MATCH_MODE`
to `lax` to prefer configured or advertised upstream URL aliases before the
same-name fallback, or `strict` to require exactly one URL alias match. Every
mode also requires the current user's discovered inventory to contain the exact
remote tool.

Each object accepts:

| Field | Meaning |
| --- | --- |
| `name` | Lowercase namespace component. It must be unique and cannot contain `__`. |
| `url` | Absolute `http` or `https` MCP proxy endpoint. Embedded credentials are rejected. |
| `transport` | `sse` (the issue-compatible default) or `streamable_http` (recommended for new servers). |
| `protocol_mode` | `auto` (default): Streamable HTTP tries modern discovery, with legacy handshake fallback. `legacy`: skip discovery and initialize directly. SSE always uses the legacy handshake. |
| `auth_mode` | `bearer`, `header_delegation`, `m2m_jwt`, or `user_oauth` (each user's own token; see Per-user OAuth below). |
| `header_mappings` | Map a supported identity source to the HTTP header the proxy expects. |
| `token_env` | Name of the environment variable holding the bearer/M2M credential. The secret is never placed in the JSON. |
| `client_credentials` | Automatic service-token acquisition for `m2m_jwt`; mutually exclusive with `token_env`. See below. |
| `oauth` | `user_oauth` only: a pre-registered client (`client_id`, `client_secret_env`) and `scope`. Omit it for dynamic registration. |
| `user_authorization` | Experimental: opt into per-user gateway access and durable status; contains the gateway's browser-facing `reauthorize_url`. |
| `require_confirmation` | Fallback when a tool's annotations do not give clear confirmation guidance; default `true`. |
| `enabled` | Disable one entry without deleting it; default `true`. |
| `connect_timeout_seconds` / `read_timeout_seconds` | Per-operation HTTP bounds; defaults 10/300 seconds. |

Supported header sources are `user_id`, `subject`, `issuer`, `email`,
`display_name`, `preferred_username`, and `access_token`. `access_token` is only
present when the caller supplied an ephemeral token in the current process. A
Temporal turn reconstructs identity from the Seizu user record and deliberately
does not persist the browser's bearer token. For per-user access in detached
interactive turns and headless runs, use `user_oauth`, whose grant is stored for
exactly that, or `m2m_jwt` plus the durable OIDC `(issuer, subject)` identity
pair (or trusted identity-header delegation).

`m2m_jwt` adds `Authorization: Bearer <service-token>` and defaults the target
identity to `X-Target-User-ID: <subject>` and
`X-Target-User-Issuer: <issuer>` unless those header names are explicitly
mapped. A mapping may instead use an identity-provider claim such as `email`,
for example `{"email":"X-Target-User-ID"}`. Every configured target identity
header must have a nonempty value for the user or Seizu rejects the operation
before contacting the gateway. `bearer` sends the configured token in
`Authorization`.
`header_delegation` sends only the mapped identity values.

Every discovery and tool call creates a fresh client transport and fresh header
dictionary for that user. A connection carrying one user's headers is never
pooled or reused for another user. Do not place a user-controlled JWT claim name
or a literal secret in `header_mappings`; its keys are a closed set and
`token_env` is the secret indirection.

A skill's `tools_required` may name external tools with their namespaced
`ext__<proxy>__<tool>` form. Those references are checked against the configured
proxy list only — the remote tool inventory is discovered per user at call time,
so the server cannot validate the tool name at save time, and a reference to an
unconfigured proxy is dropped from the saved skill with a warning.

Confirmation is decided per tool with this precedence:

1. An exact, fully namespaced match in
   `MCP_EXTERNAL_CONFIRMATION_REQUIRED_TOOLS` always requires confirmation.
2. `readOnlyHint:true` does not require confirmation. A mutating tool also does
   not require it when it supplies the complete safe profile
   `destructiveHint:false`, `idempotentHint:true`, and `openWorldHint:false`.
3. Explicit risk guidance—`readOnlyHint:false`, `destructiveHint:true`,
   `idempotentHint:false`, or `openWorldHint:true`—requires confirmation unless
   the complete safe mutation profile above applies.
4. Missing or incomplete guidance uses the proxy's `require_confirmation`
   fallback, which defaults to `true`.

Annotations are MCP server hints, not independently verified authorization
facts. Configure only proxies whose tool metadata you trust, and use the local
force-confirm list for tools that are sensitive even if their server marks them
read-only. Set `require_confirmation:false` only when you want ambiguous tools
from that proxy to run without a prompt. Headless runs can use the normal
`chat:bypass_permissions` path; the permission is re-checked and every bypassed
external call is audit-logged.

## OAuth challenges

If the proxy returns `401 Unauthorized`, Seizu parses the RFC 9728
`resource_metadata` parameter from `WWW-Authenticate`, including errors wrapped
by the asynchronous transport. Discovery exposes a temporary
`ext__<proxy>__seizu_authenticate` tool so the agent can surface the requirement;
a call is returned as a structured `authentication_required` block instead of
crashing the turn. The chat response includes the protected-resource metadata
URL when the proxy supplied one. Complete consent with the proxy, then retry the
request.

For a legacy unattended M2M call, a 401 becomes a distinct expired-token condition
before the chat boundary normalizes it. Rotate the credential in `token_env` and
restart the worker, or use automatic client credentials. Proxies opting into
`user_authorization` use the gateway contract below. Seizu never falls back from a rejected M2M token to another
user or a broader process identity.

## Shared API tokens

Use `bearer` with `token_env` for MCP servers that accept a static API token or
PAT. Every caller acts as that token's upstream account, with access bounded by
the token's permissions and Seizu's tool controls. This is shared access; it
does not provide per-user upstream authority.

```text
MCP_EXTERNAL_PROXIES=[{"name":"monitoring","url":"https://mcp.example/mcp","transport":"streamable_http","auth_mode":"bearer","token_env":"MCP_EXTERNAL_PROXY_TOKEN"}]
```

Operators manage static-token replacement and revocation. Restart web and worker
processes after changing their environment. Existing M2M `token_env` configurations
remain supported; automatic client credentials are recommended for short-lived
service tokens.

## Automatically renewed M2M tokens

```text
MCP_EXTERNAL_PROXIES=[{"name":"corp","url":"https://gateway.example/mcp","transport":"streamable_http","auth_mode":"m2m_jwt","client_credentials":{"token_url":"https://idp.example/oauth/token","client_id":"seizu","client_secret_env":"MCP_EXTERNAL_CLIENT_SECRET","scope":"mcp","audience":"mcp-gateway"},"user_authorization":{"reauthorize_url":"https://gateway.example/accounts"}}]
MCP_EXTERNAL_CLIENT_SECRET=<service-client-secret>
```

The web process and every Temporal worker need the same proxy configuration and
named secret. Compose forwards `MCP_EXTERNAL_CLIENT_SECRET`; inject any custom
secret names into both services too.

`client_credentials` requires `token_url`, `client_id`, and `client_secret_env`.
`scope` and `audience` are optional; omit them if the issuer does not accept them.
`token_endpoint_auth_method` defaults to `client_secret_basic`; it also supports
`client_secret_post`. The issuer must return a nonempty Bearer access token and
positive `expires_in`. Use HTTPS unless a trusted mesh protects the connection.

Seizu uses the [OAuth client-credentials grant](https://www.rfc-editor.org/rfc/rfc6749#section-4.4),
caches service tokens only in process memory, and acquires a replacement before
expiry. Concurrent requests within a process share acquisition. Administrators
rotate the client secret according to issuer policy; bearer expiry needs no
manual action. Browser OIDC settings and upstream user grants are independent.

Token requests use `connect_timeout_seconds`, do not follow redirects, and do not
retry automatically. A rejected cached token is discarded for the next operation.
Failed tool calls are not replayed for credential renewal; retry the request
after resolving its connection failure.

## Per-user OAuth (`user_oauth`)

With `auth_mode: "user_oauth"`, each user connects the gateway once from **Chat
Connections**, and every request Seizu makes for that user carries the user's
own access token. This covers interactive and detached runs alike; nothing about
the user's browser session is needed after connecting. The gateway holds the
external MCP servers and each user's upstream grants. Seizu holds only the
user's grant to the gateway, in its token vault.

```text
MCP_EXTERNAL_ENABLED=true
MCP_EXTERNAL_PROXIES=[{"name":"deps","url":"https://deps.gateway.example/mcp","transport":"streamable_http","auth_mode":"user_oauth"}]
DELEGATED_TOKEN_ENCRYPTION_KEYS=2026-11:<base64 32-byte key>
SESSION_TOKEN_ENCRYPTION_KEY=<base64 32-byte key>
```

`user_oauth` takes no `token_env`, `client_credentials` or `header_mappings`:
the user's token is the whole identity. `user_authorization` is optional and
only supplies the origin that URL elicitation recovery links must use.

**Connecting.** **Connect** on Chat Connections:

1. Seizu sends an unauthenticated request to the proxy URL and follows its 401
   to the gateway's protected-resource metadata (RFC 9728) and authorization
   server metadata (RFC 8414).
2. It finds or registers its client (below).
3. It sends the browser to the authorization endpoint with PKCE (S256) and the
   gateway as `resource`.
4. The gateway redirects back to
   `/api/v1/chat/connections/oauth/callback`. Seizu exchanges the code and
   stores the grant.

The callback is accepted only in the browser that started the connection. A
short-lived, encrypted `seizu_mcp_connect` cookie, scoped to the callback path,
carries the state, the PKCE verifier and the user. An authorization response
opened in any other browser is refused.

**Client registration.** Without an `oauth` block, Seizu registers itself
dynamically (RFC 7591) as a confidential client, once per authorization server
and redirect URI. The registration is stored encrypted in the vault and shared
by every user. For a client registered out of band:

```json
{"name":"corp","url":"https://gateway.example/mcp","transport":"streamable_http","auth_mode":"user_oauth",
 "oauth":{"client_id":"seizu","client_secret_env":"CORP_OAUTH_CLIENT_SECRET","scope":"mcp offline_access"}}
```

Omit `client_secret_env` for a public client, which relies on PKCE alone.
`scope` is sent with the authorization request and with dynamic registration;
an identity-provider-issued token usually needs `offline_access` here.

**What the gateway must support:**
- protected-resource metadata on the proxy's origin, naming its authorization
  server;
- authorization-server metadata whose `issuer` matches exactly;
- the authorization-code grant with PKCE S256;
- refresh tokens. A connection that is granted no refresh token is refused,
  because it would stop working as soon as the access token expires;
- dynamic client registration, or a pre-registered client.

A revocation endpoint is optional; Seizu calls it on **Disconnect**.

**Using the grant.** An access token within a minute of its expiry is refreshed
first. Rotated refresh tokens are written back only if the stored grant has not
changed since it was read, so two workers refreshing at once keep the newer
grant. A 401 from the gateway drops the stored access token and reports
**authorization required**; the user connects again, and the call is not
retried. A missing, revoked or unreadable grant fails the same way and never
falls back to another credential.

**Settings.**

| Setting | Meaning |
| --- | --- |
| `DELEGATED_TOKEN_ENCRYPTION_KEYS` | Vault keys, separate from the session key. Required by the web service and the Temporal worker. |
| `SESSION_TOKEN_ENCRYPTION_KEY` | Encrypts the connect cookie, as it does the session cookie. |
| `MCP_EXTERNAL_CA_FILE` | Extra CA trusted for proxies and their OAuth endpoints, on top of the system store. |
| `MCP_EXTERNAL_OAUTH_REDIRECT_URI` | The callback's browser-facing URL, when it cannot be derived from the connect request (a proxy that rewrites `Host`, or a pre-registered client allowing one URI). |

**Pomerium.** Each Pomerium MCP route is its own authorization server, so a user
connects each route separately. Pomerium also needs
`runtime_flags: {mcp_dynamic_client_registration: true}` for Seizu to register.

Every route a user connects in one Pomerium sign-in holds a copy of the identity
provider's refresh token, which Pomerium uses to re-create the session on
refresh. An identity provider that replaces its refresh token on every use
revokes that copy at the first route's refresh, and the user's other routes then
fail with authorization required. Configure the identity provider to reuse a
refresh token until it nears expiry. In Authentik that is the provider's
**Refresh token threshold**, which must not be `seconds=0`.

Pomerium also refuses an MCP request larger than its route's
`mcp.server.max_request_bytes` (default 4 KiB) with a 413, so raise it on every
route.

## Per-user gateway contract and recovery

This contract is experimental. A successful connection check or tool call does
not establish that the gateway selected the correct user's upstream grant.
Complete the [per-user validation checklist](https://github.com/mappedsky/seizu/issues/312)
before relying on delegated access. Configuration and recovery behavior may change.

Setting `user_authorization` opts into this contract for `m2m_jwt` or
`header_delegation`. The gateway must authenticate Seizu and authorize its ability
to delegate before trusting `X-Target-User-ID` (or the configured subject header).
The default target headers carry the run owner's identity-provider `(issuer,
subject)` pair, never Seizu's internal user ID. Configure both values as a pair
when the gateway uses different header names.

The gateway maps that owner to its account, selects and renews the user's upstream
grant, and enforces the grant's permissions. Missing users or grants must never
fall back to a shared or broader credential. The account-management page must
authenticate the browser user independently; a supplied user ID cannot authorize
account linking. Reject missing authority before dispatching any upstream action.

Seizu supports standard MCP [URL-mode elicitation](https://modelcontextprotocol.io/specification/2025-11-25/client/elicitation)
for out-of-band user interaction. The target-user header remains a deployment
contract, separate from MCP's service authentication.

| Gateway response | Status | Recovery |
| --- | --- | --- |
| URL `elicitation/create`, `-32042` (2025-11-25), or URL requests in `InputRequiredResult` (2026-07-28) | User interaction required | Owner reviews the gateway's request and opens an approved recovery URL. |
| `401` | Service authentication failed | Administrator checks the service credential or mesh policy. |
| `403`, including OAuth `insufficient_scope` | Access denied | Check service scopes, upstream permissions, and gateway policy. |
| Network/protocol failure | Unavailable | Check gateway availability and retry. |

Open **Connections**, at the foot of the chat sessions panel, to see the latest
observation and last-check time for each per-user gateway. The entry appears
only when at least one enabled proxy sets `user_authorization`; a deployment
with no such gateway has no per-user status to show, so the page stays hidden. URL elicitation is
advertised only for opted-in proxies, and form elicitation is not supported. Detached runs cancel
legacy elicitation requests without accepting them; newer input-required results
are recorded without continuing the operation. The custom `X-Seizu-Auth-Error`
header is not used.

Streamable HTTP tries `server/discover` for the 2026-07-28 stateless protocol
first. Modern requests carry their protocol version and client capabilities
without an initialization handshake. Legacy-only servers fall back to
`initialize`; authentication failures, rate limits, server failures, and timeouts
stop negotiation without retrying a tool. Set `protocol_mode: legacy` for a
server that requires initialization before any other request. SSE always uses
the legacy handshake. Legacy Streamable HTTP servers may be sessionful or
stateless; a session ID is not required.

The page displays the gateway's explanation and destination host. Opening a link
is optional; Seizu does not automatically open URLs, accept consent, or resume a
run on completion notifications. Elicitation may request actions other than OAuth:
review the request before proceeding. URLs must have the same scheme, host, and
effective port as `user_authorization.reauthorize_url`, with no embedded
credentials, fragment, whitespace, or backslashes. Up to eight links are stored;
URLs are limited to 4096 characters, IDs to 256, and explanations to 1000.
Links are hidden after one hour; retry discovery or the original request for a
fresh link. A gateway may expire a link sooner or invalidate it when the MCP
request is cancelled. The configured **Account page** remains available for
account management independently of an MCP session. Use HTTPS outside local development. Gateways must not include tokens
or secrets in URLs or explanations, and must expire recovery nonces themselves.

After completing the interaction, **Check connection** performs
fresh tool discovery; it does not call a tool or resume an earlier run. Retry the
interactive request or let the next scheduled run execute. Connection failures
also include a connection-page link in chat tool diagnostics.

Status is persisted per user and proxy configuration, including failures during
discovery that make a skill unavailable. It survives worker restarts. A successful
fresh request clears a failure; cached discovery does not. Changed configuration
starts with an unknown status. Records contain bounded recovery links and
explanations but no tokens or raw upstream responses. They do not prove access to
every tool; a tool-specific interaction may require retrying the original request.

The gateway owns upstream OAuth callbacks, encryption, refresh, revocation, and
account linking. Seizu stores no upstream grants and does not implement the
gateway's consent flow. Verify this contract against your gateway before enabling
it; a shared PAT adapter does not meet it.

## Kubernetes with service-mesh authentication

When the mesh already authenticates Seizu to the gateway, use:

```text
MCP_EXTERNAL_PROXIES=[{"name":"corp","url":"http://mcp-gateway.platform.svc.cluster.local/mcp","transport":"streamable_http","auth_mode":"header_delegation","user_authorization":{"reauthorize_url":"https://gateway.example/accounts"}}]
```

Seizu sends the target-user subject and issuer headers without `Authorization`. Configure strict
mTLS at the gateway and allow only the Seizu web and Temporal-worker workload
identities to assert target users. For Istio, use `PeerAuthentication` with
`STRICT` and an `AuthorizationPolicy` allowing the specific service-account
principals; see [Istio security](https://istio.io/latest/docs/concepts/security/).
Network reachability or encryption alone does not authorize delegation. Reject
direct unauthenticated traffic and prevent external clients from supplying
trusted target-user headers. The mesh manages certificate issuance and rotation;
Seizu needs no additional bearer token.

## Local development

Locally, every external MCP server sits behind one gateway, Pomerium Core, in the
`external-mcp` Compose profile. Seizu reaches each through `user_oauth`
(above), exactly as it would a production gateway. The gateway signs users in
through the local Authentik, so the profile brings the `auth` profile with it
even while Seizu's own authentication is off.

| Route | Upstream |
| --- | --- |
| `https://github.localhost.pomerium.io:8443/mcp` | [`github/github-mcp-server`](https://github.com/github/github-mcp-server), through a network-internal Caddy adapter that adds the development PAT |
| `https://deps.localhost.pomerium.io:8443/mcp` | [`mappedsky/depsdevmcp`](https://github.com/mappedsky/depsdevmcp), deps.dev package metadata: what a package version *declares* it requires and what its resolved graph pulls in. Read-only and unauthenticated; only a package name and version leave the network. |
| `https://whoami.localhost.pomerium.io:8443/` | `.config/dev/whoami-mcp`, which reports which per-user upstream grant authorized each call. Its upstream authorization server is Authentik, so connecting it also walks through the per-user upstream consent a third-party service would ask for. |
| `https://elicit.localhost.pomerium.io:8443/mcp` and `/mcp/legacy` | [`mappedsky/elicitationtestermcp`](https://github.com/mappedsky/elicitationtestermcp), elicitation scenarios for manual testing (below) |

`*.localhost.pomerium.io` resolves to `127.0.0.1` in public DNS, so the browser
reaches the gateway on port 8443. Inside the stack the same names are network
aliases on the gateway container, so Seizu sees the same issuer and resource
URLs the browser does. Gateway state lives in its own Postgres database, so
users stay connected across restarts.

The gateway serves HTTPS from a local CA it generates at
`.compose/external-mcp-gateway/ca.pem`; Seizu trusts it through
`MCP_EXTERNAL_CA_FILE`. The CA can only vouch for `*.localhost.pomerium.io`,
and its private key is discarded once the gateway's certificate is signed.

The browser reaches the gateway only while connecting an account. Expect a
certificate warning then, on the `authenticate.` host and on each route's host,
and accept it for that visit. Don't add the CA to a browser or system trust
store.

Because nothing can renew the certificate without the key, the gateway replaces
the CA and certificate together at startup when the certificate is within 30
days of expiry. Restart Seizu after a replacement.

```bash
make external_mcp_enable   # writes MCP_EXTERNAL_* and the vault key into .env
make down && make up
```

`make external_mcp_enable` (`scripts/dev_external_mcp.py`) sets
`MCP_EXTERNAL_ENABLED`, the development `MCP_EXTERNAL_PROXIES` and
`MCP_EXTERNAL_CA_FILE`. It generates `DELEGATED_TOKEN_ENCRYPTION_KEYS` and
`SESSION_TOKEN_ENCRYPTION_KEY` only if they are unset, and keeps the previous
file as `.env.bak`. `make external_mcp_disable` sets `MCP_EXTERNAL_ENABLED=false`
and keeps the rest.

Then open **Chat Connections** and **Connect** each gateway. Sign in as an
Authentik user, for example `seizu-editor` / `seizu`. The GitHub route also
needs a development PAT:

```text
DEV_GITHUB_MCP_TOKEN=<fine-grained-development-pat>
DEV_GITHUB_MCP_READ_ONLY=1
DEV_GITHUB_MCP_TOOLSETS=default
```

The PAT is a service credential shared by every user, scoped by whatever it can
reach, so grant it only what testing needs. `DEV_GITHUB_MCP_TOOLSETS` takes the
GitHub server's comma-separated toolset names. Set `DEV_GITHUB_MCP_READ_ONLY=0`
to expose mutating tools and exercise the annotation-based confirmation flow.

Connect **both** `github` and `deps`. Three of the bundled development skills
declare the deps.dev tools as dependencies:

- `github_security_investigations/repo_cve_reachability`
- `cve_response/dependency_provenance`
- `portable_security_review/review_source_dependencies`

A skill whose declared tools are missing from a user's inventory drops out of
that user's listing rather than failing at call time. The `upstream_urls`
aliases match the logical endpoints the bundled `portable-security-review`
plugin declares.

Configured proxies also appear as read-only **External MCP** rows under
`/app/toolsets`. Opening one performs the same per-user discovery used by chat.
Until a user connects, discovery reports authorization required for them.

### Elicitation tester

The tester is a catalogue of elicitation shapes for testing this client. Most
are shapes a conforming client should **refuse**, so a refusal is the pass
condition; each scenario states which it is. It is not in the default proxy
list. Wiring it takes two proxies, because one endpoint cannot serve both
protocols: `/mcp` carries input requests in the tool result, and `/mcp/legacy`
negotiates the older revision where the server sends elicitation requests during
the call.

Both entries need `elicitation.form`/`elicitation.url` alongside
`MCP_EXTERNAL_ELICITATION_ENABLED`. Their `user_authorization.reauthorize_url`
must match the tester's `-allowed-url`: every URL scenario is derived from that
one value, so a mismatch refuses all of them, including the ones meant to pass.
`.env.example` carries ready entries.

The bundled `elicitation-testing` Agent Plugin declares both proxies' tools. It
ships **disabled**, because its skills cannot run wherever the tester is not
configured.

## Enterprise gateway example

With Envoy, Kong, Traefik Hub, or another OAuth resource-server gateway, let the
gateway validate a short-lived Seizu service JWT and authorize the target-user
header before proxying to MCP. The Seizu side is vendor-neutral:

```text
MCP_EXTERNAL_ENABLED=true
MCP_EXTERNAL_PROXIES=[{"name":"corp","url":"https://gateway.example/mcp","transport":"streamable_http","auth_mode":"m2m_jwt","token_env":"MCP_EXTERNAL_PROXY_TOKEN","header_mappings":{"subject":"X-Target-User-ID","issuer":"X-Target-User-Issuer"},"require_confirmation":true}]
MCP_EXTERNAL_PROXY_TOKEN=<gateway-audience-service-jwt>
```

Configure the gateway to:

1. Validate the bearer token's signature, issuer, audience, expiry, and required scope.
2. Trust `X-Target-User-ID` and `X-Target-User-Issuer` only after that validation and remove any client-supplied copies before policy evaluation.
3. Bind authorization and downstream credentials to the service identity and target `(issuer, subject)` pair.
4. Return an RFC 9728 `WWW-Authenticate` challenge on missing, expired, or insufficient credentials.
5. Preserve streaming responses and apply timeouts longer than Seizu's configured MCP read timeout.

The gateway must not treat the target identity headers alone as authentication. That
would allow any caller able to reach it to select another tenant and recreate
the confused-deputy problem this integration is designed to avoid.

## Discovery cost

Listing a proxy's tools opens a transport, negotiates MCP and reads a
paginated `tools/list`. One chat turn needs that answer several times — the
capability listing in the system prompt, the planner's own, and each skill
render that resolves its declared dependencies — so Seizu discovers each proxy
**once per turn** and reuses it for the rest of that turn. Nothing to configure;
it is always on and cannot go stale within a turn.

`MCP_EXTERNAL_DISCOVERY_TTL_SECONDS` adds a second layer that survives *between*
turns, so a fresh turn need not discover at all. It is off by default because it
can be stale: a tool a user has just been granted stays invisible, and one they
have just lost stays listed, until the entry expires. Neither changes what a
call is allowed to do — every call is still checked by Seizu's RBAC and by the
upstream — but both are visible to the user. A cached listing is dropped for
that user as soon as an upstream refuses their identity.

Both layers are keyed per user, because a proxy's listing reflects the delegated
identity it was fetched with.
