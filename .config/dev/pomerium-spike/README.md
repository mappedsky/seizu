# Pomerium delegated-OAuth spike

A throwaway evaluation of open-source Pomerium Core (v0.34.2) as the external
MCP gateway, under the delegated-OAuth design. In that design:

- each user connects once and Seizu keeps a refresh token for them;
- Pomerium holds and refreshes each user's upstream grants;
- Seizu calls tools as the user, including when no browser is present.

The spike exists to settle four questions before any Seizu code is written:

1. **Consent count.** Does one grant cover every route, or does each upstream
   route need its own authorization?
2. **Lifetime.** Do Pomerium's refresh tokens (documented as 365 days) keep
   working without a browser, within Authentik's own refresh-token policy?
3. **Rotation under concurrency.** What happens when two workers spend the same
   refresh token at once?
4. **Edition.** Does all of this work in open-source Pomerium Core, with no
   Enterprise console or Pomerium Zero?

It also checks isolation: two users must get their own upstream grant, never
each other's.

## What is running

| Piece | What it is |
| --- | --- |
| `pomerium-spike` | Pomerium Core on `:8443` with TLS from a local CA. Pomerium marks every cookie `Secure`, so HTTPS is required. Reaches Authentik as `localhost:9000` through an in-container forwarder (AUTH-002). Storage is in memory, so a restart drops every grant. |
| `pomerium-spike-whoami` | An MCP upstream (`whoami_mcp.py`) that rejects calls without a live Authentik token and otherwise reports whose grant authorized the call. It serves two ports, one per route. |
| Routes A and B | `https://whoami-{a,b}.localhost.pomerium.io:8443`. Both use Authentik as the upstream authorization server (client `pomerium-spike-upstream`). |
| Authentik apps | `pomerium-spike` (Pomerium's own sign-in, 5-minute access tokens) and `pomerium-spike-upstream` (2-minute access tokens, so upstream refresh happens within one sitting). Defined in `.config/dev/authentik/blueprints/pomerium-spike.yaml`. |
| `scripts/pomerium_spike.py` | Plays Seizu: one confidential client registered once, a refresh token per user, and calls made with no browser involved. Runs on the host and uses only the standard library. |

`*.localhost.pomerium.io` resolves to `127.0.0.1` in public DNS. Inside the
stack, the same names are network aliases on the Pomerium container.

## Running it

```bash
docker compose --profile auth --profile pomerium-spike up -d --build \
  authentik-postgresql authentik-server authentik-worker pomerium-spike pomerium-spike-whoami
```

The CA is generated on first start at `.compose/pomerium-spike/ca.pem`. Either
import it into the browser, or click through the certificate warning on each
of the `authenticate.`, `whoami-a.` and `whoami-b.` hosts.

```bash
S="python3 scripts/pomerium_spike.py"
$S register                     # one client for every user, as Seizu would hold

# User 1: sign in as seizu-editor / seizu. Consent twice: once to Pomerium,
# once to the upstream. connect repeats the authorization until whoami succeeds.
$S connect editor --route a
$S call editor --route a        # no browser from here on
$S call editor --route b        # Q1: does route A's token work on route B?
$S connect editor-b --route b   # Q1: a second authorization for route B
$S routes editor --route a      # per-route `connected` status

# Q2 and Q3
$S refresh editor               # rotation, then replay the old refresh token
$S race editor --workers 2      # spend one refresh token concurrently
$S call editor --route a        # still works after the race?

# Isolation: a second user, in a private window, as seizu-viewer / seizu
$S connect viewer --route a
$S call viewer --route a        # upstream_user and grant must differ from editor's
```

Waiting longer than 2 minutes between calls makes Pomerium refresh the upstream
token, and longer than 5 minutes covers its own session. The `grant.jti_sha256`
in a whoami result changes when the upstream token is replaced.

To revoke, delete the user's tokens in Authentik (**Directory → Tokens and App
passwords**, or the user's **Sessions** tab), then `call` again. It must fail
closed, never fall back to another grant.

Tear down with
`docker compose --profile pomerium-spike down` and `rm -rf .compose/pomerium-spike`.

## Findings

Recorded 2026-11-28 against Pomerium v0.34.2, Authentik 2026.2.3.

### Without a browser (verified)

- **The discovery chain works as MCP specifies.** An unauthenticated call gets
  `401` with `WWW-Authenticate: Bearer resource_metadata="…"`. The protected-
  resource metadata names Pomerium as the authorization server, and its metadata
  advertises `authorization_code` and `refresh_token`.
- **Confidential-client registration works in Core.** Dynamic client
  registration is off by default and needs the runtime flag
  `mcp_dynamic_client_registration`. With it on, `client_secret_basic` gets a
  secret. The default, client ID metadata documents, needs a client ID served
  over public HTTPS: Pomerium fetches it through an SSRF-safe client that
  refuses private addresses.
- **Every route is its own authorization server** (Q1, partly answered). Route A
  and route B each advertise their own issuer and token endpoint. Seizu should
  expect a separate grant per route. Whether a second route needs a second
  browser consent, or rides on Pomerium's sign-in session, is still to observe.
- **Pomerium reaches Authentik** through the loopback forwarder. An
  authorization request with no session goes to Pomerium's sign-in, then to
  Authentik's authorize endpoint as `client_id=pomerium-spike`.
- **The upstream fails closed.** Missing and invalid tokens get `401` on both
  ports, and Authentik's userinfo endpoint is consulted for each call.
- **Pomerium labels MCP support "experimental"** in `config/policy.go`.
- **The service-account path is not needed.** Nothing above uses an Enterprise
  or Zero feature (Q4, so far).

### With a browser (pending)

Q1 (second-route consent), Q2, Q3 and the two-user isolation check need the
browser steps above. Record their results here.
