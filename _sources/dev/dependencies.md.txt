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

## Node dependencies

Seizu's node dependencies are managed by bun. If your system is setup to use bun directly, you can do so. Otherwise, you can use docker to manage the node resources:

```bash
$> make bun <bun-commands>
```
