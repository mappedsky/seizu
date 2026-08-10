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
