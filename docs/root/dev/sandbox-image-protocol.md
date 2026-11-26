# Sandbox image protocol (`seizu-sandbox/1`)

Some sandbox providers run a container image that Seizu does not build. This
page is the contract such an image implements, so it can live in its own
repository and be built and released independently of Seizu. Today the one
provider that uses it is [Amazon Bedrock AgentCore Runtime](../install/sandbox.md#amazon-bedrock-agentcore-runtime),
and the reference implementation is
[mappedsky/seizu-bedrock-sandbox-image](https://github.com/mappedsky/seizu-bedrock-sandbox-image).

Three artifacts in the Seizu repository define the contract:

| Artifact | What it is |
|----------|------------|
| This page | The normative description: environment, operations, errors, versioning |
| `schema/sandbox-protocol-v1.json` | JSON Schema for every request, response, argument and result, generated from `reporting/services/sandbox_protocol.py` by `make schema` |
| `scripts/sandbox_image_conformance.py` | A standard-library-only checker to run against a built image, in the image repository's CI |

## Environment

An image must:

- **Run on `linux/arm64`** (an AgentCore Runtime requirement), as a **numeric,
  non-root `USER`**.
- **Serve HTTP on `0.0.0.0:8080`**: `POST /invocations` and `GET /ping`.
- **Set `HOME=/home/user`, and make `/home/user` resolve into the workspace
  mount at `/mnt/workspace`.** A symlink created at build time
  (`ln -s /mnt/workspace /home/user`) is enough. The mount is only attached at
  invocation time, never while the image starts, so the symlink must resolve
  lazily. Everything Seizu writes — saved tool results, plugin skills, session
  markers — goes under `/home/user`, and this is what makes it persist between
  conversation turns. `/tmp` and every other path do not persist.
- **Ship `python3`** with the baseline data libraries a sandbox sub-agent
  expects: at least `pandas`, `numpy`, `matplotlib`, `requests` and
  `pyyaml`, plus `bash` and coreutils.
- **Generate nothing secret or unique at start-up.** AgentCore may snapshot a
  started container and restore that snapshot into every session, so random
  seeds, tokens and timestamps taken before the first request are shared.
- **Hold no credentials of its own.** Code inside the image is untrusted; the
  runtime's execution role is readable from inside the VM and must be minimal
  (see the install docs).

## `GET /ping`

Return HTTP 200 with `{"status": "Healthy"}`, or `{"status": "HealthyBusy"}`
while **any** `/invocations` request is in flight. `HealthyBusy` holds off the
runtime's idle timeout, so a long Python run is not stopped half-way. If you
include `time_of_last_update`, change it only when the status changes.

## `POST /invocations`

One operation per request. Request:

```json
{
  "protocol": "seizu-sandbox/1",
  "op": "run_python",
  "args": {"code": "1 + 1"},
  "timeout_seconds": 120
}
```

Response — always HTTP 200 with a JSON envelope, success or failure:

```json
{"ok": true, "result": {"stdout": "", "stderr": "", "text": "2", "error": null, "restarted": false}}
```

```json
{"ok": false, "error": {"code": "not_found", "message": "/home/user/missing.txt"}}
```

`timeout_seconds` bounds the operation inside the image. Requests must be
served **concurrently** — the steps of one conversation share a sandbox and
call it in parallel — except where an operation says otherwise.

### Operations

| `op` | `args` | `result` | Notes |
|------|--------|----------|-------|
| `hello` | `create_marker?` | `protocol_versions`, `capabilities`, `home`, `storage_semantics`, `marker` | See [the marker](#the-marker). `storage_semantics` is `"object"` when the workspace is an object-store mount (no append, in-place edit, rename or symlink) and `"posix"` otherwise. `capabilities` is empty in v1. |
| `run_python` | `code` | `stdout`, `stderr`, `text`, `error`, `restarted` | Runs in **one persistent interpreter** per session: variables and imports survive between calls, as in a notebook. Calls are serialized. If the code ends in an expression, its `repr()` is `text`. An exception is a *successful* operation with `error: {name, value, traceback}`. If the code exceeds `timeout_seconds`, kill and restart the interpreter and return `restarted: true` (or the `timeout` error); its state is gone. |
| `run_bash` | `command`, `cwd?` | `stdout`, `stderr`, `exit_code`, `timed_out` | `bash -c` with `cwd` defaulting to `$HOME`. A non-zero exit is a successful operation. A command past `timeout_seconds` is killed and reported with `timed_out: true` (or the `timeout` error). |
| `read_file` | `path` | `content_b64`, `size` | Raw bytes, base64-encoded. `not_found` if the path is not a file. |
| `write_file` | `path`, `content_b64` | `size` | Creates parent directories. Overwrites. |
| `list_files` | `path` | `entries: [{name, type: "file" \| "dir"}]` | One level, no recursion. `not_found` if the path is not a directory. |
| `wipe` | — | `removed` | Delete everything in `/mnt/workspace`, the marker included. Seizu uses this to delete a conversation's sandbox. |

### The marker

`$HOME/.seizu-sandbox` holds the JSON of the `create_marker` object Seizu
passed (`deployment`, `purpose`, `thread`, `created_at`). `hello` with
`create_marker` writes it **only if no marker exists** — an existing marker is
never overwritten — and every `hello` returns the marker as it now stands, or
`null`.

Seizu writes the marker when it creates a sandbox and reads it back when it
resumes one. A missing marker on resume means the workspace was lost (it
expired, or the runtime was updated), so Seizu starts a fresh sandbox rather
than pointing the model at files that are not there. With S3 storage Seizu also
reads markers straight from the bucket to find abandoned workspaces.

### Errors

`error.code` is one of:

| Code | When |
|------|------|
| `unsupported_protocol` | `protocol` is not one the image speaks |
| `unsupported_op` | Unknown `op` |
| `invalid_args` | Missing or malformed `args` |
| `not_found` | A path that does not exist |
| `timeout` | The operation exceeded `timeout_seconds` and produced nothing to report |
| `internal` | Anything else |

These codes are for the *operation* failing. Code that raises, and commands that
exit non-zero, are reported in the result, not as errors.

## Versioning

- **Additive changes stay in v1.** Seizu ignores response fields it does not
  know, and an image must ignore request fields it does not know.
- **Anything else is a new version.** An image lists every version it speaks in
  `hello`'s `protocol_versions`; Seizu refuses an image that does not list the
  version it needs, naming both.

## Checking an image

```bash
mkdir ws
docker run --rm -d -p 8080:8080 -v "$PWD/ws:/mnt/workspace" my-sandbox-image
python scripts/sandbox_image_conformance.py --url http://localhost:8080
```

On an amd64 host, run the arm64 image under QEMU emulation, or build a native
variant of the same Dockerfile for the check. The checker wipes the workspace,
so never point it at a sandbox holding data. `--skip-timeouts` drops the two
slower timeout checks.
