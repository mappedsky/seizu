# Kubernetes (Helm)

The [`mappedsky-helm-charts`](https://github.com/mappedsky/mappedsky-helm-charts)
repository packages Seizu for Kubernetes. It deploys the web/API process and,
optionally, the Temporal workflow worker and the Cartography sync worker. The
chart's `README.md` and `values.yaml` are the authoritative reference for every
setting; this page covers what the chart expects you to provide.

## Prerequisites

The chart does **not** deploy Seizu's data stores. Provide these yourself and
point the chart at them:

- **Neo4j** — `seizu.neo4j.uri` (plus credentials as secrets).
- **PostgreSQL** — application records via `seizu.reportStore.sqlDatabaseUrl`,
  with `secrets.data.sqlDatabaseUser` / `secrets.data.sqlDatabasePassword`. A
  second database (or instance) for LangGraph chat checkpoints is recommended;
  see [Chat checkpoint storage](backend.html).
- **Temporal server** — required for workflows, scheduled chats, and interactive
  chat turns. Set `seizu.temporal.address` and `temporalWorker.enabled=true`.
- **Authentication** — a `JWKS_URL` or full OIDC configuration plus
  `SESSION_TOKEN_ENCRYPTION_KEY` and `REPORT_QUERY_SIGNING_SECRET`. Auth is on by
  default; disabling it serves every request as a single development user. See
  [Auth configuration](backend.html) and the [security guidance](security.html).

## Installing

Charts are published as OCI artifacts. Install the release directly from the
registry:

```bash
helm install seizu oci://ghcr.io/mappedsky/charts/seizu \
  --set seizu.neo4j.uri=bolt://neo4j.default.svc.cluster.local:7687 \
  --set seizu.reportStore.sqlDatabaseUrl=postgresql://postgres.default.svc.cluster.local:5432/seizu \
  --set secrets.data.sqlDatabaseUser=seizu \
  --set secrets.data.sqlDatabasePassword=<password>
```

Prefer a checked-in `values.yaml` over long `--set` chains, and keep secrets in a
secret manager or an externally-managed `Secret` rather than in plaintext values.

## Enabling optional components

The workers and feature areas are toggled through the chart's values. The
underlying settings are documented in [Backend Installation &
Configuration](backend.html); the chart maps them to structured keys:

| Component | Values |
|-----------|--------|
| Temporal worker | `temporalWorker.enabled=true`, `seizu.temporal.address` |
| Chat assistant | `seizu.chat.enabled=true`, `seizu.chat.llm.model`, provider API key via `secrets` |
| Chat checkpoints | `seizu.chat.checkpoint.databaseUrl` |
| Sandbox delegation | `seizu.sandbox.enabled=true` (+ sandbox provider config) |
| Cartography sync worker | `cartographyWorker.enabled=true` (own image and task queue) |
| External MCP proxies | `secrets.extraData` for tokens, `MCP_EXTERNAL_*` via values |
| OTLP tracing | `seizu.telemetry.otlpEndpoint` |

Chat and scheduled chats require Temporal, so enable `temporalWorker` alongside
`seizu.chat.enabled`.

## Upgrading

Follow the [upgrade guide](upgrading.html) for every release between the deployed
and target versions — the chart runs the same startup migrations as the Docker
image and does not remove the need to read breaking-change procedures first. The
chart's `README.md` calls out chart-specific notes (for example, the 5.0.0
storage cutover is not reversible).
