# Managing dependencies

## Python dependencies

Seizu's Python dependencies are managed by uv. After updating `pyproject.toml`, update the lockfile through the `seizu` container:

```bash
$> docker compose run --rm seizu uv lock
```

The root `pyproject.toml` defines the server package (`seizu`). The separately releasable CLI package is defined in `packages/seizu-cli/pyproject.toml` and reuses the top-level `seizu_cli` and `seizu_schema` source packages.

Build the server wheel after generating the frontend bundle:

```bash
$> make build_server
```

Build the CLI-only wheel:

```bash
$> make build_cli
```

Release tags drive package versions in GitHub Actions:

| Tag | Published artifacts |
|-----|---------------------|
| `vX.Y.Z` | `seizu`, `seizu-cli`, and the Docker image |
| `server-vX.Y.Z` | `seizu` and the Docker image |
| `cli-vX.Y.Z` | `seizu-cli` only |
| `dev-vX.Y.Z.devN` | Development release of `seizu`, `seizu-cli`, and the Docker image |
| `server-dev-vX.Y.Z.devN` | Development release of `seizu` and the Docker image |
| `cli-dev-vX.Y.Z.devN` | Development release of `seizu-cli` only |

Development release tags publish normal PyPI prerelease artifacts using the
PEP 440 version after the prefix, such as `3.1.0.dev1`. The Docker image is
published to GHCR with the same version tag, such as
`ghcr.io/mappedsky/seizu:3.1.0.dev1`; development tags do not update `latest`.

## Node dependencies

Seizu's node dependencies are managed by bun. If your system is setup to use bun directly, you can do so. Otherwise, you can use docker to manage the node resources:

```bash
$> make bun <bun-commands>
```
