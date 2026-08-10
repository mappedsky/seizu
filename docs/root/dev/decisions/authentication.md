# Authentication decisions (`AUTH`)

Decisions behind identity resolution and the OIDC configuration guards. For
configuration, see the [security guidance](../../install/security.md) and the
[backend settings reference](../../install/backend.md).

Primary code: `reporting/authnz/__init__.py`, `reporting/services/oauth_client.py`,
`reporting/routes/auth.py`.

## AUTH-001 — A split-hostname issuer mismatch is reported, never reconciled

**Applies to:** `oauth_client.verify_issuer_consistency`, `OIDC_INTERNAL_AUTHORITY`

Durable identity is `(iss, sub)` exactly as the token carries it. When
`OIDC_INTERNAL_AUTHORITY` differs from `OIDC_AUTHORITY` and the two authorities
advertise different issuers, Seizu logs the mismatch at startup (fatal only
under `OIDC_REQUIRE_CONSISTENT_ISSUER`) and otherwise leaves the two identities
alone. It does **not** rewrite the internal issuer to the external one before
`get_or_create_user`.

**Why the guard exists:** an IDP that derives its issuer from the request host —
Authentik does — forks one human into two user records, one per authentication
path, and every owner-scoped surface diverges silently: private reports, query
history, chat threads (namespaced by `user_id`), scheduled chats, and MCP action
confirmations. The observed symptom is an MCP-initiated confirmation that 404s
as "Confirmation not found" when opened in the browser, with no other signal
anywhere. The startup comparison turns a class of mystery duplicate accounts
into one line in the deploy log.

**Why not canonicalize the issuer:** aliasing the internal issuer onto the
external one would silently make user identity a function of mutable deployment
config — editing `OIDC_AUTHORITY`, or dropping the internal one, would re-key
every existing record. Nothing in the token proves the two URLs are the same
IDP; only the IDP's own configuration does. Pinning `JWT_ISSUER` is not a fix
either: it pins exactly one value, so the *other* path 401s outright. With two
issuers in play there is no correct single setting, so the fix belongs at the
IDP, and Seizu's job is to say so loudly.

**Why best-effort, and advisory by default:** the backend frequently cannot
reach the external hostname — that unreachability is the entire reason
`OIDC_INTERNAL_AUTHORITY` exists — so an unverifiable check downgrades to a
warning rather than taking the app down, and an already-running deployment with
this shape keeps serving while it is fixed.

**Don't:** make the check fatal by default, block startup on a discovery fetch
failure, or normalize `iss` anywhere between token validation and
`get_or_create_user`.

## AUTH-002 — The dev stack reaches Authentik only as `localhost:9000`

**Applies to:** `scripts/dev_oidc_loopback.py`, the `seizu` service's `command`

The backend container runs a small stdlib forwarder on its own loopback `:9000`,
pointed at `authentik-server:9000` by `DEV_OIDC_LOOPBACK_TARGET`, so the
backend's discovery and token exchange use the same URL the browser, MCP clients
and the CLI use. `OIDC_INTERNAL_AUTHORITY` is therefore unset in dev, and
`JWKS_URL` points at `localhost:9000` like everything else.

**Why:** Authentik has no fixed-issuer setting — `OAuth2Provider.get_issuer()`
calls `request.build_absolute_uri()`, so `iss` and every advertised endpoint
follow the request `Host`, and `issuer_mode` only selects the path. Reaching it
under a second hostname is therefore enough to fork one login into two users
(AUTH-001), which is what made an MCP-initiated confirmation unopenable from the
dev browser. This is split-horizon DNS in miniature: one name and port, resolved
differently inside the network than outside, which is how the problem is solved
in production.

**Rejected — spoofing the `Host` header on the backend's OIDC calls:** httpx
will do it, but over HTTPS it forces a choice between a certificate valid for
the internal name and an SNI override for the external one, and any `Host`-
routing proxy in front of the IDP may answer 421. A dev-only convenience is not
worth that in the shared auth path.

**Rejected — a DNS name resolving to `127.0.0.1` (`idp.localtest.me`) with a
docker network alias:** it needs no hosts-file edit and was verified working,
but the issuer includes the *port*, so any quickstart that forwards the IDP on
a port other than 9000 (a VM port-forward, say) re-forks it under a new name.
It also makes first-run depend on public DNS, which fails offline and under
resolvers with DNS-rebinding protection.

**Rejected — a sidecar container sharing the backend's network namespace:** the
original implementation, a `socat` service with `network_mode: "service:seizu"`.
It works until anything recreates the backend — which `scripts/chat_harness.py`
does routinely — and then fails in the worst available way: the sidecar keeps
reporting `running` while attached to the dead namespace, so nothing restarts
it, and the new backend has nothing on `localhost:9000`. Measured directly:
after `up -d --force-recreate --no-deps seizu`, discovery from inside the new
container returned `ConnectError [Errno 111] Connection refused` with the
sidecar still listed as up. Running in-process also drops a root container with
default capabilities out of the backend's network namespace, where the loopback
OIDC exchange is plaintext HTTP.

**Don't:** add a second hostname for Authentik to the dev config — including a
"just for the backend" one. Binding both `127.0.0.1` and `::1` is load-bearing:
`localhost` resolves to `::1` first inside the container, and an IPv4-only
listener gets connection-refused for half the lookups.
