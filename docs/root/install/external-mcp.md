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
| `auth_mode` | `bearer`, `header_delegation`, or `m2m_jwt`. |
| `header_mappings` | Map a supported identity source to the HTTP header the proxy expects. |
| `token_env` | Name of the environment variable holding the bearer/M2M credential. The secret is never placed in the JSON. |
| `client_credentials` | Automatic service-token acquisition for `m2m_jwt`; mutually exclusive with `token_env`. See below. |
| `user_authorization` | Experimental: opt into per-user gateway access and durable status; contains the gateway's browser-facing `reauthorize_url`. |
| `require_confirmation` | Fallback when a tool's annotations do not give clear confirmation guidance; default `true`. |
| `enabled` | Disable one entry without deleting it; default `true`. |
| `connect_timeout_seconds` / `read_timeout_seconds` | Per-operation HTTP bounds; defaults 10/300 seconds. |

Supported header sources are `user_id`, `subject`, `issuer`, `email`,
`display_name`, `preferred_username`, and `access_token`. `access_token` is only
present when the caller supplied an ephemeral token in the current process. A
Temporal turn reconstructs identity from the Seizu user record and deliberately
does not persist the browser's bearer token, so detached interactive turns and
headless runs should use `m2m_jwt` plus the durable OIDC `(issuer, subject)`
identity pair (or trusted identity-header delegation) instead.

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

Open **Chat Connections** in the navigation to see the latest observation and
last-check time for each per-user gateway. The entry appears only when at least
one enabled proxy sets `user_authorization`; a deployment with no such gateway
has no per-user status to show, so the page stays hidden. URL elicitation is
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

## Lightweight local development

The optional Compose profile runs two upstream MCP servers, so a development
stack has real tools to discover rather than a stub:

- [`github/github-mcp-server`](https://github.com/github/github-mcp-server) in
  Streamable HTTP mode, behind a network-internal Caddy adapter. It gives the
  profile a real, annotation-bearing MCP implementation without requiring
  developers to launch a separate upstream.
- [`mappedsky/depsdevmcp`](https://github.com/mappedsky/depsdevmcp), a deps.dev
  package-metadata server. It answers what a package version *declares* it
  requires and what its resolved graph pulls in — neither of which the security
  graph records, and neither of which the sandbox can fetch, since it has no
  egress. Read-only and unauthenticated; only a package name and version leave
  the network.
- [`mappedsky/elicitationtestermcp`](https://github.com/mappedsky/elicitationtestermcp),
  a catalogue of elicitation shapes for testing this client against. Most of them
  are shapes a conforming client should **refuse**, so a refusal is the pass
  condition rather than a failure; each scenario states which it is. It is a
  manual-testing fixture and is not in the example `MCP_EXTERNAL_PROXIES` above.

  Wiring it takes two proxies, because one endpoint cannot serve both protocols:
  `/mcp` carries input requests in the tool result, and `/mcp/legacy` negotiates
  the older revision where the server sends elicitation requests during the call.
  Both entries need `elicitation.form`/`elicitation.url` alongside
  `MCP_EXTERNAL_ELICITATION_ENABLED`, and their `user_authorization.reauthorize_url`
  has to match the tester's `-allowed-url`: every URL scenario is derived from
  that one value, so a mismatch refuses all of them including the ones meant to
  pass. `.env.example` carries a ready entry.

  The bundled `elicitation-testing` Agent Plugin declares both proxies' tools so
  the scenarios are reachable with progressive disclosure on. It ships
  **disabled**, since its skills are useless, and their dependencies unresolvable,
  wherever the tester is not running.

The profile also runs
[`obot-platform/mcp-oauth-proxy`](https://github.com/obot-platform/mcp-oauth-proxy)
on `http://localhost:8081`, used only when
[exercising the OAuth challenge path](#exercising-the-oauth-challenge-path).

**By default Seizu talks to the Caddy adapter directly**, and every GitHub call
is the development PAT's identity, read-only via `GITHUB_READ_ONLY=1`. There is
no per-user token to obtain and nothing that expires. Be aware about what
that is: a service account shared by every Seizu user, scoped by whatever the
PAT can reach. Grant it only the repositories and permissions needed for
testing.

```text
DEV_GITHUB_MCP_TOKEN=<fine-grained-development-pat>
DEV_GITHUB_MCP_READ_ONLY=1
DEV_GITHUB_MCP_TOOLSETS=default

MCP_EXTERNAL_ENABLED=true
MCP_EXTERNAL_PROXIES=[{"name":"github","url":"http://external-mcp-github-auth:8080/mcp","upstream_urls":["https://mcp.github.test/mcp"],"transport":"streamable_http","auth_mode":"header_delegation"},{"name":"deps","url":"http://external-mcp-deps:8080/mcp","upstream_urls":["https://mcp.deps.test/mcp"],"transport":"streamable_http","auth_mode":"header_delegation","require_confirmation":false}]
MCP_EXTERNAL_CONFIRMATION_REQUIRED_TOOLS=
```

Configure **both** proxies. Three of the bundled development skills declare the
deps.dev tools as dependencies — `github_security_investigations/repo_cve_reachability`,
`cve_response/dependency_provenance`, and
`portable_security_review/review_source_dependencies`. A skill whose declared
tools are missing from a user's inventory drops out of that user's listing
entirely rather than failing at call time, so omitting `deps` makes those three
quietly disappear rather than error. The `upstream_urls` aliases match the
logical endpoints the bundled `portable-security-review` plugin declares.

```bash
make external_mcp_enable
make up
```

`DEV_GITHUB_MCP_TOOLSETS` accepts the GitHub server's comma-separated toolset
names; set `DEV_GITHUB_MCP_READ_ONLY=0` to expose mutating tools and exercise
Seizu's annotation-based confirmation flow.

### Exercising the OAuth challenge path

The obot proxy sits in the separate `external-mcp-oauth` profile, for when the
thing under test is Seizu's OAuth handling — RFC 9728 discovery, the 401
challenge, and recovery — rather than the tools behind it. It authenticates the
*caller to the proxy* through the same local Authentik instance and users as
Seizu; it confers no GitHub authority, because the Caddy adapter strips obot's
forwarded identity headers and substitutes the PAT. Requiring it for ordinary
local work therefore bought a short-lived credential and no per-user
authorization, which is why it is not the default.

The Authentik blueprint creates a dedicated confidential client and registers
`http://localhost:8081/callback`, separate from Seizu's public PKCE/device
client because obot requires a client secret. Add to `.env`:

```text
DEV_MCP_PROXY_OAUTH_CLIENT_ID=seizu-external-mcp-proxy
DEV_MCP_PROXY_OAUTH_CLIENT_SECRET=seizu-external-mcp-proxy-dev-secret
DEV_MCP_PROXY_OAUTH_AUTHORIZE_URL=http://localhost:9000/application/o/seizu-external-mcp-proxy
DEV_MCP_PROXY_SCOPES=openid,email,profile
DEV_MCP_PROXY_ENCRYPTION_KEY=<base64-encoded-32-byte-key>

MCP_EXTERNAL_PROXIES=[{"name":"github","url":"http://external-mcp-proxy:8080/mcp/github","transport":"streamable_http","auth_mode":"bearer","token_env":"MCP_EXTERNAL_PROXY_TOKEN"}]
```

Generate the encryption key with `openssl rand -base64 32`, then:

```bash
docker compose --profile auth --profile external-mcp --profile external-mcp-oauth up -d
make external_mcp_login
```

`make external_mcp_login` dynamically registers a public PKCE client with the
proxy, opens its Authentik authorization flow, and writes the issued credential
to `.env` without displaying it. Authentik issues an access token valid for one
hour and a refresh token valid for thirty days, so the script stores **both**
(plus the registered client id) and renews silently on later runs — a browser
round trip is needed roughly monthly, not hourly. Pass `--force` to authorize
from scratch.

This local profile tests bearer authentication, not the per-user gateway
contract. A Temporal deployment needing per-user GitHub authority needs a
gateway that explicitly supports service authentication plus target-user grant
selection, as in the enterprise gateway example below. Changing an OAuth
provider alone does not enable that contract.

The make target enables local Authentik and persists
`MCP_EXTERNAL_ENABLED=true` in `.env`; `make up` then selects both Compose
profiles automatically. After the services are healthy,
`make external_mcp_login` dynamically registers a public PKCE client with the
local proxy, opens its Authentik authorization flow, writes the issued proxy
bearer to `MCP_EXTERNAL_PROXY_TOKEN` without displaying it, and recreates Seizu
and its Temporal worker. The proxy container uses a supervised loopback
forwarder so its Authentik discovery and token exchange retain the browser's
`localhost:9000` issuer (AUTH-002). To turn off the agent capability and its
local proxy services, run `make external_mcp_disable`, then restart the stack
with `make down && make up`. This does not disable Authentik independently.

With `MCP_EXTERNAL_PROXY_TOKEN` empty, the catalog and chat exercise the
401/RFC 9728 path and expose an `ext__github__seizu_authenticate` placeholder.
Run `make external_mcp_login` to complete that OAuth flow and replace the
placeholder with the GitHub server's repository and code tools. The resulting
proxy bearer and shared development PAT are suitable only for a single-user
local test. Multi-user deployments should use a
gateway/M2M exchange or a proxy integration that maps a Seizu identity to its
own per-user token vault.

Configured proxies also appear as read-only **External MCP** rows under
`/app/toolsets`. Opening one performs the same per-user discovery used by chat.
Before the proxy bearer is configured, the tools view contains
`ext__<proxy>__seizu_authenticate`; after authentication it contains the
remote server's namespaced tools and input parameters. This catalog does not
re-export external tools from Seizu's own MCP endpoint.

To proxy a different Streamable HTTP MCP server, set
`DEV_MCP_PROXY_UPSTREAM_URL` to its internal or host-reachable URL. Such an
upstream is responsible for understanding Obot's forwarded identity headers;
the GitHub-specific PAT adapter is used only by the default URL.

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
