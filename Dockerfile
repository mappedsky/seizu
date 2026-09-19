FROM python:3.14-slim-bookworm@sha256:a3974109d36f164ca70024bc0d0828ac706e4ccda849f8638d879e91f79e90ec AS base

COPY --from=ghcr.io/astral-sh/uv:0.11.1@sha256:fc93e9ecd7218e9ec8fba117af89348eef8fd2463c50c13347478769aaedd0ce /uv /uvx /usr/local/bin/

RUN groupadd seizu && \
    useradd -s /bin/bash -d /home/seizu -m -g seizu seizu

RUN mkdir /run/seizu && chown seizu:seizu /run/seizu

WORKDIR /home/seizu/seizu

ENV UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY pyproject.toml uv.lock ./
COPY packages/seizu-cli/pyproject.toml packages/seizu-cli/pyproject.toml

# Dev image: prod + dev deps (pytest, pre-commit, aiosqlite, etc.).
# Used by docker-compose services for local development, testing, seeding,
# and anything else that may need the dev toolchain.
FROM base AS dev

RUN uv sync --frozen --all-groups --all-packages --no-install-workspace

USER seizu

COPY . .

EXPOSE 8080

FROM oven/bun:latest@sha256:87416c977a612a204eb54ab9f3927023c2a3c971f4f345a01da08ea6262ae30e AS nodebuilder

WORKDIR /home/node/seizu

COPY package.json bun.lock eslint.config.mjs .prettierrc .prettierignore tsconfig.json vite.config.ts .

RUN bun install

COPY . .

RUN bun run build

# Production image: prod-only deps + the built frontend.
# No pytest, pre-commit, or other dev tooling — keeps the image small and
# avoids shipping code paths that are not part of the runtime.
FROM base AS production

RUN uv sync --frozen --no-dev --package seizu --no-install-workspace

USER seizu

COPY --chown=seizu:seizu . .

COPY --chown=seizu:seizu --from=nodebuilder /home/node/seizu/build /build

CMD ["gunicorn", "--config", "/home/seizu/seizu/gunicorn.conf", "reporting.asgi:application", "--workers=2", "-k", "uvicorn_worker.UvicornWorker", "--access-logfile=-", "--error-logfile=-"]
