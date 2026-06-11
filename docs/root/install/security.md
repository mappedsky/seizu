# Security Guidance

Seizu exposes operational views over a Neo4j security graph. Treat access to Seizu as access to the graph data itself, and treat access to the Query Console, MCP `graph__query`, and Cypher-backed toolsets as high-trust capabilities.

This page covers the recommended production posture for Seizu and the Neo4j hardening settings that should sit underneath it.

## Production checklist

- Require authentication for every Seizu request.
- Validate JWT issuer and audience, and use a strong signing algorithm allowlist.
- Assign the least-privileged Seizu role that supports each user's workflow.
- Keep ad-hoc Cypher execution limited to trusted roles that explicitly need `query:execute`.
- Prefer report views for read-only consumers; report queries are signed by the backend and are the safer default for shared dashboards.
- Limit MCP built-ins to the groups users actually need.
- Keep Neo4j reachable only from Seizu, workers, and trusted sync jobs.
- Disable Neo4j network and local-file import paths unless you explicitly need them.
- Restrict Neo4j extension loading and APOC access to a minimal allowlist.
- Run Seizu and Neo4j behind TLS at the network edge.
- Keep Seizu, Neo4j, APOC, and base container images patched.
- Protect graph data, report-store data, logs, backups, and config files with the same care as production credentials.

## Seizu authentication

Do not run production with authentication disabled.

Set:

```bash
DEVELOPMENT_ONLY_REQUIRE_AUTH=true
JWKS_URL=https://idp.example.com/application/o/seizu/jwks/
JWT_ISSUER=https://idp.example.com/application/o/seizu/
JWT_AUDIENCE=seizu
ALLOWED_JWT_ALGORITHMS=RS256,ES256,ES512
OIDC_AUTHORITY=https://idp.example.com/application/o/seizu/
OIDC_CLIENT_ID=seizu
OIDC_SCOPE="openid email"
```

Use `JWT_ISSUER` and `JWT_AUDIENCE` whenever your identity provider can issue stable values. Leaving them empty makes token validation less specific than it should be in production.

If Seizu is exposed through HTTPS directly, keep `TALISMAN_FORCE_HTTPS=true`. If TLS terminates at a load balancer or ingress, enforce HTTPS and HSTS there and set Seizu's proxy headers correctly for your deployment.

## RBAC

Seizu resolves a role from the configured JWT claim and maps it to permissions.

Use built-in roles conservatively:

| Role | Use |
|------|-----|
| `seizu-viewer` | Read reports and dashboard. No ad-hoc query console or query history access. |
| `seizu-editor` | Viewer plus report authoring, chat tool/skill use (`chat:tools:call`, `chat:skills:call`), chat confirmation bypass (`chat:bypass_permissions`), and scheduled chats (`chat:schedule`). |
| `seizu-admin` | Editor plus toolsets, tools, skillsets, skills, scheduled queries, roles, and administrative objects. |

Recommended settings:

```bash
RBAC_ROLE_CLAIM=seizu_role
RBAC_DEFAULT_ROLE=
```

Setting `RBAC_DEFAULT_ROLE=` denies access when the token does not contain an explicit Seizu role. This is safer than silently assigning a default role to every authenticated user.

Use user-defined roles for narrower access. For example, separate report authors from users who can manage scheduled queries or toolsets.
If a user truly needs ad-hoc Cypher, grant `query:execute` only to a tightly scoped role and keep that role out of general viewer assignments.

## Report Query Signing Secret

`REPORT_QUERY_SIGNING_SECRET` signs the backend-issued capability tokens used by report panels.
Treat it like any other application signing key:

- Use a cryptographically random secret.
- Use at least 32 bytes of entropy; 64 bytes is a better default.
- Encode it as hex or base64, then store the encoded string in your secret manager or deployment env vars.
- If you use hex, 32 bytes becomes 64 characters and 64 bytes becomes 128 characters.
- If you use base64, 32 bytes is typically 44 characters with padding; longer secrets will be proportionally longer.
- Generate a fresh value per deployment environment. Do not reuse the same secret across dev, staging, and production unless you have a deliberate reason to do so.
- Keep it stable across restarts so existing report tokens remain valid until they expire.
- Rotate it if the secret is exposed; rotation invalidates outstanding report tokens immediately.

Example generation commands:

```bash
openssl rand -hex 32
openssl rand -base64 48
python - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
```

Prefer a longer secret if your secret storage format allows it. The exact encoding does not matter as long as the full random value is preserved without truncation or normalization.

## Query Execution

Seizu validates Cypher before execution:

- Neo4j `EXPLAIN` must classify the query as read-only.
- `LOAD CSV`, APOC network procedures, APOC dynamic Cypher execution helpers, and selected Neo4j administration commands are blocked.
- The validator scans original, comment-stripped, and Cypher Unicode-decoded forms to catch common obfuscation.

This validation is a guardrail, not the only control. Query-capable users can still read data they are allowed to query. Do not give Query Console or MCP graph-query permissions to users who should not have broad graph read access.
With the current split between execution paths:

- `POST /api/v1/query/adhoc` is for trusted ad-hoc exploration and always records query history.
- `POST /api/v1/query/report` is for report panels and only accepts backend-signed tokens bound to the current user and report version.
- Report tokens are short-lived capabilities. Treat them as authorization artifacts, not as values to expose or store outside the request flow.
- Report API responses do not include `query_capabilities` unless the caller explicitly opts in with `include_query_capabilities=true`.

When writing reports, scheduled queries, and tools:

- Prefer parameters for user-controlled values.
- Do not concatenate untrusted values into Cypher.
- Avoid dynamic labels, relationship types, and property names unless the value comes from a strict allowlist.
- Add explicit `LIMIT` values to exploratory queries.
- Keep returned columns narrow; avoid returning entire nodes if a few fields are enough.
- Treat error messages, query history, and signed report-query tokens as sensitive.
- Keep report panels on signed report execution; do not route them through the ad-hoc console path.

## MCP

The MCP endpoint can expose Seizu management tools and user-defined Cypher-backed tools to LLM clients.

Recommended settings:

```bash
MCP_ENABLED=true
MCP_ENABLED_BUILTINS=graph,reports
```

Only enable the built-in groups that users actually need. Use `MCP_ENABLED_BUILTINS=none` if you only want user-defined toolsets exposed.

For MCP clients that support OAuth discovery, configure:

```bash
MCP_OAUTH_AUTHORIZATION_ENDPOINT=https://idp.example.com/application/o/seizu/authorize/
MCP_OAUTH_TOKEN_ENDPOINT=https://idp.example.com/application/o/token/
MCP_OAUTH_ISSUER=https://idp.example.com/application/o/seizu/
MCP_RESOURCE_URL=https://seizu.example.com/api/v1/mcp
```

Toolsets should be treated like code:

- Review Cypher before enabling a tool.
- Keep parameters typed and required where possible.
- Return the minimum useful data.
- Disable or delete tools that are no longer used.
- Use version history to audit changes.

## Scheduled Queries

Scheduled queries run without an interactive user at execution time. Keep them narrowly scoped.

Recommendations:

- Require `scheduled_queries:write` only for trusted administrators.
- Review destination action configs, especially Slack channels and SQS queue names.
- Avoid placing secrets in scheduled query config.
- Keep result sizes bounded.
- Disable scheduled queries that are no longer required.
- Run workers with only the cloud permissions they need for their configured actions.

## Report Store And Secrets

Reports, scheduled queries, roles, toolsets, tools, skillsets, and skills are stored in the report store. Protect that store as configuration state, not as disposable cache.

Recommendations:

- Use IAM roles or workload identity instead of static AWS keys when possible.
- Limit DynamoDB or SQL credentials to the minimum required tables/database.
- Encrypt storage volumes and managed database storage.
- Back up report-store data and test restore paths.
- Keep Slack tokens, OIDC client secrets, database passwords, and cloud credentials in a secret manager.
- Do not commit `.env`, credentials files, or generated local state.

## Neo4j Network Exposure

Neo4j should not be directly reachable from end users.

Recommended production topology:

- Browser/users -> Seizu frontend/API only.
- Seizu API/workers -> Neo4j Bolt.
- Cartography/sync jobs -> Neo4j Bolt.
- No public listener for Neo4j HTTP, HTTPS, or Bolt.

The default development compose stack does not publish Neo4j ports to the host. Keep that pattern in production unless you have a specific operational need.

If direct Neo4j administration is required, use a private network, VPN, bastion host, or short-lived port forward.

## Neo4j Authentication

The development compose stack uses `NEO4J_AUTH=none` for convenience. Do not use that in production.

Production should enable Neo4j authentication and use a dedicated Seizu database user:

```bash
NEO4J_AUTH=neo4j/<strong-password>
NEO4J_USER=seizu
NEO4J_PASSWORD=<strong-password>
```

With Neo4j Enterprise, use Neo4j RBAC to grant only the graph privileges Seizu needs. At minimum, separate operational administrator accounts from application accounts.

## Neo4j LOAD CSV And SSRF Hardening

Seizu does not need arbitrary Cypher-originated HTTP, FTP, or local-file imports for normal operation. Harden Neo4j so a future validator bypass still cannot make outbound requests or read local files.

Recommended Neo4j 5.x settings:

```properties
dbms.security.allow_csv_import_from_file_urls=false
internal.dbms.cypher_ip_blocklist=0.0.0.0/0,::/0
```

In Docker environment-variable form:

```bash
NEO4J_dbms_security_allow__csv__import__from__file__urls=false
NEO4J_internal_dbms_cypher__ip__blocklist=0.0.0.0/0,::/0
```

`dbms.security.allow_csv_import_from_file_urls=false` disables `file:///` reads through `LOAD CSV`.

`internal.dbms.cypher_ip_blocklist=0.0.0.0/0,::/0` blocks all IPv4 and IPv6 HTTP/FTP requests made by Cypher features such as `LOAD CSV`.

If your Neo4j deployment has a legitimate import workflow, prefer offline `neo4j-admin database import` or a tightly controlled import directory. If you must allow network loading, block metadata and internal ranges at both Neo4j and network egress layers.

## Neo4j APOC And Extension Hardening

APOC and other Neo4j extensions increase the callable surface from Cypher. Load only what you need.

Seizu's development compose stack uses a narrow APOC utility allowlist:

```properties
dbms.security.procedures.allowlist=apoc.convert.*,apoc.text.*
dbms.security.procedures.unrestricted=apoc.convert.*,apoc.text.*
```

In Docker environment-variable form:

```bash
NEO4J_dbms_security_procedures_allowlist=apoc.convert.*,apoc.text.*
NEO4J_dbms_security_procedures_unrestricted=apoc.convert.*,apoc.text.*
```

Avoid broad settings such as:

```properties
dbms.security.procedures.allowlist=*
dbms.security.procedures.unrestricted=apoc.*
```

Do not install APOC Extended, GDS, custom procedures, or other plugins unless a specific Seizu workflow requires them. If you add a plugin, review:

- Network-capable procedures.
- File import/export procedures.
- Procedures that execute dynamic Cypher.
- Procedures that run writes, background jobs, triggers, or schema operations.

Then update the allowlist and Seizu query validator tests accordingly.

## Neo4j HTTP, Browser, And CORS

If users do not need Neo4j Browser in production, disable Neo4j HTTP/HTTPS listeners or keep them on a private administrative network.

Relevant settings include:

```properties
server.http.enabled=false
server.https.enabled=false
dbms.security.http_access_control_allow_origin=https://seizu.example.com
```

Only keep HTTP enabled when you need health checks, Browser, or administrative access. If it is enabled, do not expose it publicly.

## TLS

Use TLS at the public edge for Seizu. For production Neo4j, also consider TLS for Bolt traffic, especially when Seizu and Neo4j are not on the same trusted private network.

Neo4j Bolt TLS is controlled by:

```properties
server.bolt.tls_level=REQUIRED
```

This requires certificate configuration and corresponding Seizu driver URI/settings. Do not enable it without testing the Seizu backend, workers, CLI, and sync jobs against the TLS-enabled endpoint.

## Logging And Audit

Recommendations:

- Keep Seizu API logs and Neo4j logs available for investigation.
- Avoid DEBUG logging in production unless actively investigating an issue.
- Treat query text as sensitive because it can include labels, properties, internal identifiers, and user-entered values.
- Review query history retention expectations with your users.
- Monitor for blocked validation attempts, repeated query errors, and unusually large result sets.

## Production References

These upstream Neo4j pages are useful when hardening an environment:

- [Neo4j security checklist](https://neo4j.com/docs/operations-manual/current/security/checklist/)
- [Securing Neo4j extensions](https://neo4j.com/docs/operations-manual/current/security/securing-extensions/)
- [APOC security guidelines](https://neo4j.com/docs/apoc/current/security-guidelines/)
- [Protecting Neo4j against SSRF](https://neo4j.com/developer/kb/protecting-against-ssrf/)
- [Protecting against Cypher injection](https://neo4j.com/developer/kb/protecting-against-cypher-injection/)
