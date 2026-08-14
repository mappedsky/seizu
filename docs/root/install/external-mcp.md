# External MCP proxies

Seizu's chat agent can discover and call tools on external MCP servers through
an identity-aware proxy. The proxy remains responsible for authenticating to
the external service, storing or rotating its credentials, and enforcing its
own authorization policy. Seizu supplies a scoped user identity and, where
configured, a proxy bearer or machine credential.

External servers are deliberately available to the chat runtime only. Seizu
does not re-export them from `/api/v1/mcp`. Tool names are rewritten as
`ext__<proxy-name>__<remote-tool-name>`, preventing collisions with built-ins,
stored toolsets, or another proxy.

## Proxy configuration

Set `MCP_EXTERNAL_PROXIES` to a JSON array. The web service and
`seizu-temporal-worker` must receive identical configuration and token
environment variables because interactive and headless turns execute in the
Temporal worker.

```text
MCP_EXTERNAL_PROXIES=[{"name":"drive","url":"https://mcp-proxy.example/mcp/drive","transport":"streamable_http","auth_mode":"m2m_jwt","token_env":"MCP_EXTERNAL_PROXY_TOKEN","header_mappings":{"user_id":"X-Target-User-ID","email":"X-Target-Email"}}]
MCP_EXTERNAL_CONFIRMATION_REQUIRED_TOOLS=ext__drive__delete_file,ext__drive__share_file
MCP_EXTERNAL_PROXY_TOKEN=<short-lived-service-jwt>
```

Each object accepts:

| Field | Meaning |
| --- | --- |
| `name` | Lowercase namespace component. It must be unique and cannot contain `__`. |
| `url` | Absolute `http` or `https` MCP proxy endpoint. Embedded credentials are rejected. |
| `transport` | `sse` (the issue-compatible default) or `streamable_http` (recommended for new servers). |
| `auth_mode` | `bearer`, `header_delegation`, or `m2m_jwt`. |
| `header_mappings` | Map a supported identity source to the HTTP header the proxy expects. |
| `token_env` | Name of the environment variable holding the bearer/M2M credential. The secret is never placed in the JSON. |
| `require_confirmation` | Fallback when a tool's annotations do not give clear confirmation guidance; default `true`. |
| `enabled` | Disable one entry without deleting it; default `true`. |
| `connect_timeout_seconds` / `read_timeout_seconds` | Per-operation HTTP bounds; defaults 10/300 seconds. |

Supported header sources are `user_id`, `subject`, `issuer`, `email`,
`display_name`, `preferred_username`, and `access_token`. `access_token` is only
present when the caller supplied an ephemeral token in the current process. A
Temporal turn reconstructs identity from the Seizu user record and deliberately
does not persist the browser's bearer token, so detached interactive turns and
headless runs should use `m2m_jwt` plus `user_id` (or trusted identity-header
delegation) instead.

`m2m_jwt` adds `Authorization: Bearer <token_env>` and defaults the target
identity to `X-Target-User-ID` when no mapping uses that header. `bearer` sends
the configured token in `Authorization`. `header_delegation` sends only the
mapped identity values.

Every discovery and tool call creates a fresh client transport and fresh header
dictionary for that user. A connection carrying one user's headers is never
pooled or reused for another user. Do not place a user-controlled JWT claim name
or a literal secret in `header_mappings`; its keys are a closed set and
`token_env` is the secret indirection.

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

For an unattended M2M call, a 401 becomes a distinct expired-token condition
before the chat boundary normalizes it. Rotate the credential in `token_env` and
restart the worker. Seizu never falls back from a rejected M2M token to another
user or a broader process identity.

## Lightweight local development with Obot

The optional Compose profile runs
[`obot-platform/mcp-oauth-proxy`](https://github.com/obot-platform/mcp-oauth-proxy)
on `http://localhost:8081`. Obot is intentionally opt-in: it needs an OAuth
application and an upstream Streamable HTTP MCP server. Register
`http://localhost:8081/callback` with the OAuth provider, then add this to
`.env`:

```text
DEV_MCP_PROXY_OAUTH_CLIENT_ID=<provider-client-id>
DEV_MCP_PROXY_OAUTH_CLIENT_SECRET=<provider-client-secret>
DEV_MCP_PROXY_OAUTH_AUTHORIZE_URL=https://accounts.google.com
DEV_MCP_PROXY_SCOPES=openid,email,profile
DEV_MCP_PROXY_UPSTREAM_URL=http://host.docker.internal:9000/mcp/example
DEV_MCP_PROXY_ENCRYPTION_KEY=<base64-encoded-32-byte-key>

MCP_EXTERNAL_PROXIES=[{"name":"local","url":"http://external-mcp-proxy:8080/mcp/example","transport":"streamable_http","auth_mode":"bearer","token_env":"MCP_EXTERNAL_PROXY_TOKEN"}]
MCP_EXTERNAL_CONFIRMATION_REQUIRED_TOOLS=
MCP_EXTERNAL_PROXY_TOKEN=<access-token-issued-by-the-local-proxy>
```

Generate the encryption key with `openssl rand -base64 32`. Start the stack:

```bash
docker compose --profile external-mcp up
```

With `MCP_EXTERNAL_PROXY_TOKEN` empty, ask chat to use the external server to
exercise the 401/RFC 9728 path. Complete the proxy's OAuth flow with an MCP OAuth
client, put the issued development access token in the variable, recreate the
Seizu and worker containers, and retry to exercise discovery and invocation.
This copied bearer is suitable only for a single-user local test. Multi-user
deployments should use a gateway/M2M exchange or a proxy integration that maps a
Seizu identity to its own per-user token vault.

## Enterprise gateway example

With Envoy, Kong, Traefik Hub, or another OAuth resource-server gateway, let the
gateway validate a short-lived Seizu service JWT and authorize the target-user
header before proxying to MCP. The Seizu side is vendor-neutral:

```text
MCP_EXTERNAL_PROXIES=[{"name":"corp","url":"https://gateway.example/mcp","transport":"streamable_http","auth_mode":"m2m_jwt","token_env":"MCP_EXTERNAL_PROXY_TOKEN","header_mappings":{"user_id":"X-Target-User-ID","issuer":"X-Target-Issuer"},"require_confirmation":true}]
MCP_EXTERNAL_PROXY_TOKEN=<gateway-audience-service-jwt>
```

Configure the gateway to:

1. Validate the bearer token's signature, issuer, audience, expiry, and required scope.
2. Trust `X-Target-User-ID` only after that validation and remove any client-supplied copy before policy evaluation.
3. Bind authorization and downstream credentials to the pair of service identity and target user.
4. Return an RFC 9728 `WWW-Authenticate` challenge on missing, expired, or insufficient credentials.
5. Preserve streaming responses and apply timeouts longer than Seizu's configured MCP read timeout.

The gateway must not treat `X-Target-User-ID` alone as authentication. That
would allow any caller able to reach it to select another tenant and recreate
the confused-deputy problem this integration is designed to avoid.
