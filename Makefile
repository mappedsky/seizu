# bash needed for pipefail
SHELL := /bin/bash

args = `arg="$(filter-out $@,$(MAKECMDGOALS))" && echo $${arg:-${1}}`

# Automatically include --profile auth if auth is enabled in .env
AUTH_PROFILE := $(shell grep -q 'DEVELOPMENT_ONLY_REQUIRE_AUTH=true' .env 2>/dev/null && echo '--profile auth' || echo '')
# Automatically include --profile sqlmodel if either PostgreSQL-backed store is selected in .env
SQL_PROFILE := $(shell grep -Eq '^(REPORT_STORE_BACKEND=sqlmodel|CHAT_CHECKPOINT_BACKEND=postgres)$$' .env 2>/dev/null && echo '--profile sqlmodel' || echo '')
COMPOSE_PROFILES := $(AUTH_PROFILE) $(SQL_PROFILE)

.PHONY: uv_sync
uv_sync:
	docker compose run --rm --user root seizu uv sync --frozen --all-groups --all-packages --no-install-workspace

junit:
	mkdir -p junit

.PHONY: test
test: test_unit test_frontend

.PHONY: test_unit
test_unit: junit uv_sync
	docker compose run --rm seizu uv run --frozen --no-sync pytest --strict --junitxml=coverage/unit.xml --cov=reporting --cov=seizu_schema --cov-report=html:coverage/cov_html --cov-report=xml:coverage/cov.xml --cov-report=term --no-cov-on-fail tests/unit

.PHONY: test_integration
test_integration:
	docker compose run --rm seizu uv run --frozen --no-sync pytest tests/integration -v

.PHONY: test_query_validator_live
test_query_validator_live: config_setup
	docker compose run --rm seizu uv run --frozen --no-sync pytest tests/integration/reporting/services/query_validator_test.py -v

# Verifies every cartography_sync registry flag exists in the pinned image's
# CLI — run after bumping the Dockerfile.cartography pin.
.PHONY: cartography_contract_test
cartography_contract_test: build_cartography_worker
	docker run --rm --network none --entrypoint python ghcr.io/mappedsky/seizu-cartography -m cartography_sync.contract_check

.PHONY: remediation_smoke
# Manual, real-network smoke test of the CVE remediation sandbox path. Runs in
# the temporal worker service so it inherits the SANDBOX_*/REMEDIATION_* config.
# Default: the two-sandbox git auth+handoff path (install gh, clone, extract a
#   patch, push it from a fresh sandbox, delete the branch).
#   Requires SANDBOX_API_KEY + REMEDIATION_GITHUB_TOKEN. Usage:
#     make remediation_smoke SMOKE_REPO=org/repo
# SMOKE_FORK=1 (or REMEDIATION_USE_FORK=true in .env): exercise the fork path —
#   ensure the bot fork, push the branch to the fork, delete it there. SMOKE_REPO
#   then only needs read/fork access. Usage:
#     make remediation_smoke SMOKE_REPO=org/repo SMOKE_FORK=1
# SMOKE_PROXY=1: instead probe the credential proxy (boot a private LiteLLM proxy,
#   mint a key, reach it from a second sandbox). Requires SANDBOX_AGENT_* with a
#   real provider key (+ SANDBOX_AGENT_MODEL for opencode). Usage:
#     make remediation_smoke SMOKE_PROXY=1
remediation_smoke:
	docker compose run --rm --no-deps -e SMOKE_REPO=$(SMOKE_REPO) -e SMOKE_FORK=$(SMOKE_FORK) \
		-e SMOKE_PROXY=$(SMOKE_PROXY) \
		seizu-temporal-worker uv run --frozen --no-sync python -m scripts.remediation_smoke

.PHONY: test_frontend
test_frontend:
	@docker compose run --rm --no-deps seizu-node bun run type-check

.PHONY: lock
lock:
	docker compose run --rm seizu uv lock

.PHONY: lock_update
lock_update:
	docker compose run --rm seizu uv lock --upgrade

.PHONY: rebuild
rebuild:
	docker compose build seizu
	docker compose run --rm --no-deps seizu-node bun run build

# Build the cartography sync-worker image (Dockerfile.cartography: pinned
# upstream cartography + the cartography_sync activity worker).
.PHONY: build_cartography_worker
build_cartography_worker:
	docker compose build seizu-cartography-worker

.PHONY: drop_db
drop_db: down
	@if grep -q 'REPORT_STORE_BACKEND=sqlmodel' .env 2>/dev/null; then \
		echo "Removing postgres_data volume..."; \
		docker volume rm -f seizu_postgres_data; \
	else \
		echo "Removing dynamodb_data volume..."; \
		docker volume rm -f seizu_dynamodb_data; \
	fi
	@echo "Done. Run 'make up' to recreate and 'make seed_dashboard' to reseed."

.PHONY: drop_auth_db
drop_auth_db:
	docker compose --profile auth stop authentik-server authentik-worker authentik-postgresql
	docker compose --profile auth rm -f authentik-server authentik-worker authentik-postgresql
	@echo "Removing authentik_postgres_data volume..."
	docker volume rm -f seizu_authentik_postgres_data
	@echo "Done. Run 'make up' to recreate Authentik."

.PHONY: seed_dashboard
seed_dashboard:
	docker compose $(COMPOSE_PROFILES) run --rm seizu uv run --frozen --no-sync python -m seizu_cli --api-url http://seizu:8080 seed --config .config/dev/seizu/reporting-dashboard.yaml $(ARGS)

.PHONY: schema
schema: generate_openapi
	docker compose run --rm seizu uv run --frozen --no-sync python -m reporting.schema.cli export > schema/reporting-schema.json

# Export the OpenAPI spec from the FastAPI app (no backend connections required).
.PHONY: generate_openapi
generate_openapi:
	docker compose run --rm -e DYNAMODB_CREATE_TABLE=false seizu uv run --frozen --no-sync python -c "from reporting.app import create_app; import json; app = create_app(); print(json.dumps(app.openapi()))" > schema/openapi.json

# Generate a client library from schema/openapi.json using openapi-generator-cli.
# Usage: make generate_client LANG=go
#        make generate_client LANG=typescript-fetch
#        make generate_client LANG=java
# See https://openapi-generator.tech/docs/generators for all supported languages.
LANG ?= python
.PHONY: generate_client
generate_client: generate_openapi
	docker run --rm \
		-v $(PWD):/local \
		openapitools/openapi-generator-cli generate \
		-i /local/schema/openapi.json \
		-g $(LANG) \
		-o /local/generated/$(LANG)-client \
		--package-name seizu_client

# Build the standalone Seizu server package (wheel + sdist). Output lands in dist/.
.PHONY: build_server
build_server:
	docker compose run --rm --no-deps seizu-node bun run build
	docker compose run --rm seizu uv build --package seizu --wheel

# Build the separately releasable seizu-cli package (wheel + sdist).
.PHONY: build_cli
build_cli:
	docker compose run --rm seizu uv build --package seizu-cli --wheel

.PHONY: docs
# Builds the Sphinx site via docs/build.sh, which uses its own isolated
# virtualenv under docs/.venv. No schema generation is needed — the docs
# do not consume the JSON schema anymore.
docs:
	@bash docs/build.sh

.PHONY: bun
bun:
	@docker compose run seizu-node bun $(call args)

.PHONY: setup
config_setup:
	@./.config/setup.sh

.PHONY: up
up: config_setup
	docker compose $(COMPOSE_PROFILES) up $(call args)

.PHONY: down
down:
	docker compose $(COMPOSE_PROFILES) down

.PHONY: neo4j_current
neo4j_current: config_setup
	@grep -q '^COMPOSE_FILE=' .env 2>/dev/null \
		&& perl -pi -e 's|^COMPOSE_FILE=.*|COMPOSE_FILE=docker-compose.yml|' .env \
		|| echo 'COMPOSE_FILE=docker-compose.yml' >> .env
	@echo "Neo4j current dev database selected (neo4j:5.26, volume neo4j_data). Run 'make down && make up' to apply."

.PHONY: neo4j_latest
neo4j_latest: config_setup
	@mkdir -p ./.compose/neo4j-latest/logs ./.compose/neo4j-latest/plugins
	@grep -q '^COMPOSE_FILE=' .env 2>/dev/null \
		&& perl -pi -e 's|^COMPOSE_FILE=.*|COMPOSE_FILE=docker-compose.yml:docker-compose.neo4j-latest.yml|' .env \
		|| echo 'COMPOSE_FILE=docker-compose.yml:docker-compose.neo4j-latest.yml' >> .env
	@grep -q '^NEO4J_LATEST_IMAGE_TAG=' .env 2>/dev/null \
		&& perl -pi -e 's|^NEO4J_LATEST_IMAGE_TAG=.*|NEO4J_LATEST_IMAGE_TAG=2026.04.0|' .env \
		|| echo 'NEO4J_LATEST_IMAGE_TAG=2026.04.0' >> .env
	@echo "Neo4j latest database selected (neo4j:2026.04.0, volume neo4j_latest_data). Run 'make down && make up' to apply."

.PHONY: auth_enable
auth_enable:
	@perl -pi -e 's/DEVELOPMENT_ONLY_REQUIRE_AUTH=false/DEVELOPMENT_ONLY_REQUIRE_AUTH=true/' .env
	@echo "Auth enabled in .env. Run 'make down && make up' to apply."

.PHONY: auth_enable_bootstrap
auth_enable_bootstrap:
	@grep -q '^SESSION_TOKEN_ENCRYPTION_KEY=.\+' .env 2>/dev/null \
		|| echo "SESSION_TOKEN_ENCRYPTION_KEY=$$(openssl rand -base64 32)" >> .env
	@grep -q '^REPORT_QUERY_SIGNING_SECRET=.\+' .env 2>/dev/null \
		|| echo "REPORT_QUERY_SIGNING_SECRET=$$(openssl rand -hex 64)" >> .env
	@$(MAKE) auth_enable
	@echo "Secrets written to .env. Run 'make down && make up' to apply."

.PHONY: auth_disable
auth_disable:
	@perl -pi -e 's/DEVELOPMENT_ONLY_REQUIRE_AUTH=true/DEVELOPMENT_ONLY_REQUIRE_AUTH=false/' .env
	@echo "Auth disabled in .env. Run 'make down && make up' to apply."

.PHONY: apoc_enable
apoc_enable:
	@grep -q 'NEO4J_PLUGINS=' .env 2>/dev/null \
		&& perl -pi -e 's/NEO4J_PLUGINS=.*/NEO4J_PLUGINS=["apoc"]/' .env \
		|| echo 'NEO4J_PLUGINS=["apoc"]' >> .env
	@echo "APOC enabled in .env. Run 'make down && make up' to apply (downloads on first start)."

.PHONY: apoc_disable
apoc_disable:
	@grep -q 'NEO4J_PLUGINS=' .env 2>/dev/null \
		&& perl -pi -e 's/NEO4J_PLUGINS=.*/NEO4J_PLUGINS=/' .env \
		|| true
	@rm -f .compose/neo4j/plugins/apoc-*.jar
	@echo "APOC disabled. Run 'make down && make up' to apply."

.PHONY: sqlmodel_enable
sqlmodel_enable:
	@grep -q 'REPORT_STORE_BACKEND=' .env 2>/dev/null \
		&& perl -pi -e 's/REPORT_STORE_BACKEND=.*/REPORT_STORE_BACKEND=sqlmodel/' .env \
		|| echo 'REPORT_STORE_BACKEND=sqlmodel' >> .env
	@grep -q 'CHAT_CHECKPOINT_BACKEND=' .env 2>/dev/null \
		&& perl -pi -e 's/CHAT_CHECKPOINT_BACKEND=.*/CHAT_CHECKPOINT_BACKEND=postgres/' .env \
		|| echo 'CHAT_CHECKPOINT_BACKEND=postgres' >> .env
	@echo "SQLModel report store and PostgreSQL chat checkpoints enabled in .env. Run 'make down && make up' to apply."

.PHONY: sqlmodel_disable
sqlmodel_disable:
	@grep -q 'REPORT_STORE_BACKEND=' .env 2>/dev/null \
		&& perl -pi -e 's/REPORT_STORE_BACKEND=.*/REPORT_STORE_BACKEND=dynamodb/' .env \
		|| echo 'REPORT_STORE_BACKEND=dynamodb' >> .env
	@grep -q 'CHAT_CHECKPOINT_BACKEND=' .env 2>/dev/null \
		&& perl -pi -e 's/CHAT_CHECKPOINT_BACKEND=.*/CHAT_CHECKPOINT_BACKEND=dynamodb/' .env \
		|| echo 'CHAT_CHECKPOINT_BACKEND=dynamodb' >> .env
	@echo "DynamoDB report store and chat checkpoints restored in .env. Run 'make down && make up' to apply."

.PHONY: restart
restart:
	docker compose restart $(call args)

.PHONY: logs
logs:
	docker compose logs -f $(call args)

.PHONY: sync_aws
sync_aws:
	docker compose run cartography --neo4j-uri=bolt://neo4j:7687 --selected-modules=create-indexes,aws,analysis --aws-sync-all-profiles --permission-relationships-file=/etc/cartography/permission_relationships.yaml

.PHONY: sync_k8s
sync_k8s:
	docker compose run cartography --neo4j-uri=bolt://neo4j:7687 --selected-modules=create-indexes,kubernetes,analysis --k8s-kubeconfig=/etc/cartography/kube.config

.PHONY: sync_crowdstrike
sync_crowdstrike:
	docker compose run cartography --neo4j-uri=bolt://neo4j:7687 --selected-modules=create-indexes,crowdstrike,analysis --crowdstrike-client-id-env-var=CARTOGRAPHY_CROWDSTRIKE_CLIENT_ID --crowdstrike-client-secret-env-var=CARTOGRAPHY_CROWDSTRIKE_CLIENT_SECRET

.PHONY: sync_github
sync_github:
	docker compose run cartography --neo4j-uri=bolt://neo4j:7687 --selected-modules=create-indexes,github,analysis --github-config-env-var=CARTOGRAPHY_GITHUB_CONFIG

.PHONY: sync_cve
sync_cve:
	docker compose run cartography --neo4j-uri=bolt://neo4j:7687 --selected-modules=create-indexes,cve,analysis --cve-enabled

.PHONY: sync_cve_metadata
sync_cve_metadata:
	docker compose run cartography --neo4j-uri=bolt://neo4j:7687 --selected-modules=create-indexes,cve_metadata,analysis --cve-metadata-nist-api-key-env-var=CARTOGRAPHY_NIST_NVD_TOKEN
