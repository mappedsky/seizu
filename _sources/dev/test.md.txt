# Running Tests

Seizu's development test commands are split between Docker-backed Python/Bun commands and host-side tooling. Do not install Bun, Node, uv, or Python dependencies directly on the host for the app test suites; use the Docker Compose services shown below.

## Test Quickstart

For the common local test pass, run:

```bash
make test
```

This runs the backend unit suite and the frontend type-check target. Before opening a pull request, run the broader set of checks:

```bash
make test
docker compose run --rm --no-deps seizu-node bun test --watchAll=false
pre-commit run --all-files
make docs
```

If you changed generated schemas or API models, also run:

```bash
make schema
```

## Backend Unit Tests

Backend and CLI unit tests live under `tests/unit`. Run the full suite with coverage:

```bash
make test_unit
```

Run a narrower backend test path through the `seizu` container:

```bash
docker compose run --rm seizu uv run --frozen --no-sync pytest tests/unit/reporting/routes/query_test.py -v
docker compose run --rm seizu uv run --frozen --no-sync pytest tests/unit/seizu_cli -v
```

Always use `uv run --frozen --no-sync` in the container so tests use the already-synced Docker environment and the checked-in `uv.lock`.

## Backend Integration Tests

Integration tests live under `tests/integration` and expect the relevant services, such as Neo4j, to be available through Docker Compose.

```bash
make test_integration
```

Or run a specific integration test file:

```bash
docker compose run --rm seizu uv run --frozen --no-sync pytest tests/integration/reporting/services/query_validator_test.py -v
```

## Frontend Tests

Frontend tests live in `src/**/__tests__` and run with Bun.

Type-check only:

```bash
make test_frontend
```

Run all frontend tests:

```bash
docker compose run --rm --no-deps seizu-node bun test --watchAll=false
```

Run one frontend test file:

```bash
docker compose run --rm --no-deps seizu-node bun test src/pages/__tests__/ReportsList.test.tsx --watchAll=false
```

Run type-check and tests together:

```bash
docker compose run --rm --no-deps seizu-node bun run test:type-check
```

## Linting

Seizu uses pre-commit for linting and formatting checks. Run it on the host because the `seizu` container does not include git:

```bash
pre-commit run --all-files
```

To install the git hook locally:

```bash
pre-commit install
```

## Docs Build

Docs use a dedicated virtualenv under `docs/.venv` with dependencies pinned in `docs/requirements.txt`.

```bash
make docs
```

## Schema Generation Checks

If you change API routes, Pydantic schemas, report configuration models, or OpenAPI-visible request/response models, regenerate schemas:

```bash
make schema
```

This updates `schema/openapi.json` and `schema/reporting-schema.json`.

## Package Build Checks

CI verifies that both Python packages build. To check locally:

```bash
make build_server
make build_cli
```
