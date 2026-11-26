# Sandbox

## Purpose

The sandbox gives the chat assistant somewhere to run code. When a task needs
data processing, scripting or file manipulation that a Cypher query cannot
express, the assistant hands it to an isolated VM that can run Python, run shell
commands, and read and write files, then reports back what happened.

It is also what makes [Agent Plugin](agent-plugins.html) skills that ship
`scripts/` runnable: without a sandbox those skills still load, but their
scripts cannot be executed.

The sandbox is **chat-only**. It never appears in the MCP server's tool listing
and cannot be called by external MCP clients.

## Getting started

The sandbox needs a provider. [E2B](https://e2b.dev)'s free tier is the quickest
way to try it; see [Providers](#providers) for the self-hosted option and for
Amazon Bedrock AgentCore Runtime.

1. Get an API key from [e2b.dev/dashboard](https://e2b.dev/dashboard).
2. Add to `.env`, alongside a working chat configuration — `CHAT_LLM_PROVIDER=mock`
   echoes input and cannot call tools, so a real provider is required:

   ```
   SANDBOX_ENABLED=true
   SANDBOX_API_KEY=e2b_...

   CHAT_ENABLED=true
   CHAT_LLM_MODEL=anthropic/claude-sonnet-4-6
   ANTHROPIC_API_KEY=sk-ant-...
   ```

3. Restart: `make down && make up`.
4. Open http://localhost:3000/app/chat and ask *"Can you run a Python script
   that prints the first 10 Fibonacci numbers?"*

Users need the `sandbox:delegate` permission, granted to `seizu-editor` and
above. In the default unauthenticated dev mode every request has it.

Sandboxes have **no outbound internet** unless you set
`SANDBOX_ALLOW_INTERNET=true`. Seizu itself needs outbound access to your
provider's API, but the sandbox itself should not.

## What the assistant can do with it

Inside the sandbox the assistant can run Python, run shell commands, and read,
write and list files. It can also call the Seizu tools its user is entitled to —
`graph__query` and your own toolset tools — so it fetches the data it needs
itself rather than being handed it.

**Large results become files rather than truncated text.** A query returning
more than the assistant can hold in context is written to a file under
`/home/user/seizu_results/` and replaced with a receipt naming the path, size,
row count, columns and two sample rows. The assistant then processes the file
with Python. This is decided by size, not by the model, so a big result is never
silently cut short.

Files written in one turn are still there in the next, so a follow-up question
can build on data an earlier turn gathered instead of re-querying it.

## Sessions and persistence

**One sandbox per conversation, not one per request.** It opens the first time a
conversation needs it and is shared by everything in that turn — including steps
running on different workers. At the end of the turn it is *suspended* rather
than destroyed, and the next turn in the same conversation resumes it.

Deleting a chat thread destroys its sandbox.

```{warning}
**On E2B, processes survive between turns.** Suspension keeps the whole VM, memory
included, so code the model started in one turn can still be running in the
next, for the life of the conversation. The sandbox stays isolated from Seizu's
data stores, holds no credentials, and is bounded to one user's thread — but
this is a wider blast radius than a per-turn sandbox, and it is a deliberate
trade.

**With `SANDBOX_ALLOW_INTERNET=true` this compounds.** A surviving process keeps
its outbound access, so code started in one turn can still be reaching the
internet while later turns feed the sandbox new data. Treat persistence and
outbound internet as a combination to enable deliberately, not independently.

`SANDBOX_SESSION_PERSIST=false` destroys the sandbox at the end of every turn
instead, at the cost of each turn re-fetching what the last one gathered.
```

### Cleaning up idle conversations

A conversation a user simply stops replying to is never deleted, so nothing
tells Seizu its sandbox is finished with. `SANDBOX_SESSION_TIMEOUT_SECONDS`
bounds a *running* sandbox, not a suspended one, so suspended ones would
otherwise accumulate until your provider reclaims them, if it does at all.

An optional scheduled sweep retires chat sessions that have been idle past
`CHAT_SESSION_REAP_IDLE_SECONDS` and destroys each one's sandbox with it. A
second pass collects orphaned sandboxes whose conversation no longer exists (on
Bedrock, only with `s3` storage: session storage cannot be listed and expires on
its own after 14 days).

```{warning}
**The sweep deletes chat history, so it is off by default.** A session idle past
the threshold is removed, transcript included. Set
`CHAT_SESSION_REAP_IDLE_SECONDS` to your retention window *before* enabling
`CHAT_SESSION_REAP_ENABLED`.
```

## Security model

Isolation is the safety mechanism. The sandbox cannot reach Neo4j, PostgreSQL or
any Seizu API, and holds no credentials — which is why running code in it needs
no confirmation prompt.

The sandbox can call read-only tools on the user's behalf, but never
confirmation-gated mutating ones: those stay with the chat assistant, where you
approve them interactively.

**Which tools it gets.** By default every delegation is given the read-only
graph tools (`SANDBOX_CORE_TOOLS`), plus whatever the conversation has already
disclosed, plus anything the assistant explicitly asked for.

```{important}
The core set means **raw Cypher is available to every delegation by default**.
Anything the graph tools can read, `graph__query` can read too.

To restrict graph access, use the controls that actually bound it:

- **RBAC** — a role without `query:execute` cannot reach the graph tools at all,
  here or anywhere else. `SANDBOX_CORE_TOOLS` is intersected with the caller's
  permitted tools, so it can never widen access.
- **`SANDBOX_CORE_TOOLS`** — narrow it (for example to `graph__schema`), or set
  it empty to bind nothing up front. Expect an extra discovery step per
  delegation if you do.
```

## Providers

### E2B (default)

[E2B](https://e2b.dev) runs each sandbox in an isolated Firecracker microVM. Set
`SANDBOX_ENABLED=true` and `SANDBOX_API_KEY=e2b_...`. Seizu needs outbound access
to `https://api.e2b.app`.

### OpenKruise Agents (self-hosted)

[OpenKruise Agents](https://openkruise.io/kruiseagents/) is an
E2B-API-compatible self-hosted alternative that runs sandboxes in Kubernetes
pods, suitable for air-gapped or cost-sensitive deployments.

1. Deploy it to your cluster and expose its API (e.g. `sandbox.example.internal`).
2. Set `SANDBOX_ENABLED=true`, `SANDBOX_DOMAIN=sandbox.example.internal`, and
   `SANDBOX_API_KEY=<internal-token>` — or leave the key empty if your
   deployment uses internal auth without a client key.

The E2B SDK skips client-side key format validation when `SANDBOX_DOMAIN` is
set, so non-E2B tokens are accepted without extra configuration.

### Amazon Bedrock AgentCore Runtime

`SANDBOX_PROVIDER=bedrock` runs each conversation's sandbox as a session of an
[AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html)
in your own AWS account. Each session is its own microVM, and the conversation's
files are kept between turns in AgentCore session storage or in an S3 bucket.

**What changes compared with E2B:**

- **Files persist between turns; running processes and Python variables do not.**
  At the end of a turn the session is stopped, and the next turn starts a fresh
  VM with the files restored. This also means code started in one turn cannot
  still be running in the next.
- **The image is yours.** The runtime runs a container image built to the
  [sandbox image protocol](../dev/sandbox-image-protocol.md), from a repository
  of your choosing. `SANDBOX_BEDROCK_IMAGE` names it.
- **Network access is a property of the runtime.** Run it in VPC mode in private
  subnets without a NAT gateway to keep sandboxes off the internet. Seizu refuses
  to start a PUBLIC-mode runtime unless `SANDBOX_ALLOW_INTERNET=true`.
- **CVE remediation is not supported.** It needs E2B's templates and
  inter-sandbox networking; keep it on E2B.

#### Sandbox storage

`SANDBOX_BEDROCK_STORAGE` chooses where a conversation's files live:

| | `session` (default) | `s3` |
|---|---|---|
| Backing | AgentCore managed session storage (public preview) | Your S3 bucket, one prefix per conversation |
| Size | 1 GB per conversation | Unbounded |
| Kept until | 14 days without use, **or any runtime update** — including changing `SANDBOX_BEDROCK_IMAGE` | Deleted by Seizu, or by your bucket lifecycle rules |
| File semantics | POSIX-like | Object store: files are written whole; no append, rename or symlinks. The assistant is told this. |
| Abandoned sandboxes | Expire on AWS's schedule | Found and deleted by the [idle sweep](#cleaning-up-idle-conversations) |

Session storage is available in the regions listed in the
[AgentCore announcement](https://aws.amazon.com/about-aws/whats-new/2026/03/bedrock-agentcore-runtime-session-storage).
S3 storage requires AgentCore Runtime platform version V2, which Seizu uses.

```{warning}
**With `session` storage, updating the runtime erases every conversation's
files.** Seizu updates the runtime whenever a managed setting changes — the
image, role, network, lifecycle or storage settings — and logs a warning when it
does. Conversations continue; their sandboxes simply start empty. Use `s3`
storage if files must survive image upgrades.
```

#### Setting it up

1. **Put an image in ECR** implementing the
   [sandbox image protocol](../dev/sandbox-image-protocol.md), as
   `linux/arm64`. The reference image,
   [`ghcr.io/mappedsky/seizu-bedrock-sandbox-image`](https://github.com/mappedsky/seizu-bedrock-sandbox-image),
   can be copied in as it is; its README shows how. An image of your own can be
   checked with `scripts/sandbox_image_conformance.py`.
2. **Create the runtime's execution role.** Code in the sandbox can read this
   role's credentials, so grant only what the runtime needs: pulling the image
   from ECR and writing CloudWatch Logs. Restrict its trust policy to
   `bedrock-agentcore.amazonaws.com` with `aws:SourceAccount` and
   `aws:SourceArn` conditions.

   With `s3` storage the role also needs object access to the bucket, **scoped to
   the session**, or one conversation's code could read another's files:

   ```json
   {
     "Effect": "Allow",
     "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
     "Resource": "arn:aws:s3:::BUCKET/PREFIX/${aws:AgentCoreRuntimeSourceSid}/*"
   },
   {
     "Effect": "Allow",
     "Action": "s3:ListBucket",
     "Resource": "arn:aws:s3:::BUCKET",
     "Condition": {"StringLike": {"s3:prefix": "PREFIX/${aws:AgentCoreRuntimeSourceSid}/*"}}
   }
   ```

   `PREFIX` is `SANDBOX_BEDROCK_S3_PREFIX` (default `seizu-sandbox/<SEIZU_DEPLOYMENT_ID>`).
   Before relying on it, confirm from inside a sandbox that a sibling
   conversation's prefix cannot be listed or read.
3. **Prepare the network.** Private subnets with no NAT gateway, a security group
   allowing HTTPS egress to VPC endpoints only, and endpoints for ECR (`ecr.api`,
   `ecr.dkr`), S3 (gateway endpoint, covering the image layers, the
   `acr-storage-*` session-storage buckets and, with `s3` storage, your bucket),
   and CloudWatch Logs.
4. **Give Seizu AWS credentials** through the usual AWS mechanisms (an
   instance or pod role, or environment variables):
   - web and Temporal worker: `bedrock-agentcore:InvokeAgentRuntime`,
     `bedrock-agentcore:StopRuntimeSession`, `bedrock-agentcore:ListAgentRuntimes`;
   - Temporal worker, which creates and updates the runtime:
     `bedrock-agentcore:CreateAgentRuntime`, `UpdateAgentRuntime`,
     `GetAgentRuntime`, `TagResource`, and `iam:PassRole` on the execution role;
   - with `s3` storage, both: `s3:ListBucket`, `s3:GetObject` and
     `s3:DeleteObject` on the prefix, to delete and sweep sandboxes.
5. **Configure Seizu:**

   ```
   SANDBOX_ENABLED=true
   SANDBOX_PROVIDER=bedrock
   SANDBOX_BEDROCK_IMAGE=123456789012.dkr.ecr.us-east-1.amazonaws.com/seizu-sandbox:1.0.0
   SANDBOX_BEDROCK_ROLE_ARN=arn:aws:iam::123456789012:role/seizu-sandbox-runtime
   SANDBOX_BEDROCK_SUBNETS=subnet-0abc,subnet-0def
   SANDBOX_BEDROCK_SECURITY_GROUPS=sg-0123
   SANDBOX_BEDROCK_REGION=us-east-1
   SEIZU_DEPLOYMENT_ID=prod
   ```

On start-up the Temporal worker creates the runtime (named
`seizu_sandbox_<SEIZU_DEPLOYMENT_ID>`), or updates it if a setting changed, and
waits for it to become ready; this takes a few minutes the first time. Until
then, sandbox requests fail with "sandbox runtime … does not exist yet" or "is
CREATING". To manage the runtime yourself instead, set
`SANDBOX_BEDROCK_MANAGE_RUNTIME=false` and create it under the name Seizu
expects (or set `SANDBOX_BEDROCK_RUNTIME_NAME`), with the HTTP protocol, a
session-storage or S3 mount at `/mnt/workspace`, and an idle timeout of at least
`SANDBOX_SESSION_TIMEOUT_SECONDS`.

The local Docker Compose stack sets placeholder AWS credentials for its SQS
emulator. To try this provider locally, override `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY` and `AWS_DEFAULT_REGION` for the `seizu` and
`seizu-temporal-worker` services.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SANDBOX_ENABLED` | `false` | Master switch. |
| `SANDBOX_API_KEY` | `""` | Provider API key. Required for E2B cloud; may be empty for self-hosted deployments using internal auth. |
| `SANDBOX_DOMAIN` | `""` | Provider hostname. Empty → E2B cloud. Set to your ingress hostname for self-hosted. |
| `SANDBOX_ALLOW_INTERNET` | `false` | Allow sandboxes outbound internet access. Enable only when a task needs it. |
| `SANDBOX_TIMEOUT_SECONDS` | `120` | Maximum wall-clock time for one task. On timeout the task errors; the sandbox itself survives to the end of the turn. |
| `SANDBOX_SESSION_PERSIST` | `true` | Suspend the sandbox between turns and resume it next turn. `false` destroys it every turn. |
| `SANDBOX_SESSION_TIMEOUT_SECONDS` | `1800` | Lifetime of a running sandbox. |
| `SANDBOX_CORE_TOOLS` | `graph__query,graph__schema,graph__validate_query,graph__explain` | Tools bound to every delegation. Intersected with the caller's permitted tools, so it never widens access. Empty binds nothing. |
| `SANDBOX_MAX_OUTPUT_BYTES` | `50000` | Cap on tool output fed back into the sandbox and on the result returned to the assistant. Larger output is truncated. |
| `SANDBOX_PREVIEW_MAX_BYTES` | `2000` | How much of a file a preview returns whole; larger files return shape plus the beginning. `0` returns whole files. |
| `SANDBOX_FILE_RESULT_MAX_ROWS` | `50000` | Row bound for detecting an oversized result and writing it to a file instead of truncating it. |
| `SANDBOX_FILE_RESULT_MAX_BYTES` | `10000000` | Byte bound for the same. |
| `SANDBOX_LLM_MODEL` | `""` | LiteLLM model id for work inside the sandbox. Empty inherits `CHAT_LLM_MODEL`; set a cheaper or faster model here if you want one. |
| `SANDBOX_PROVIDER` | `e2b` | `e2b` or `bedrock`. |

Amazon Bedrock AgentCore Runtime settings (`SANDBOX_PROVIDER=bedrock`):

| Variable | Default | Description |
|----------|---------|-------------|
| `SANDBOX_BEDROCK_IMAGE` | `""` | ECR URI of the sandbox image. Required unless `SANDBOX_BEDROCK_MANAGE_RUNTIME=false`. |
| `SANDBOX_BEDROCK_ROLE_ARN` | `""` | Execution role the runtime runs as. Required unless `SANDBOX_BEDROCK_MANAGE_RUNTIME=false`. |
| `SANDBOX_BEDROCK_SUBNETS` | `""` | Comma-separated subnet ids. With `SANDBOX_BEDROCK_SECURITY_GROUPS`, selects VPC mode; empty means PUBLIC mode, which requires `SANDBOX_ALLOW_INTERNET=true`. |
| `SANDBOX_BEDROCK_SECURITY_GROUPS` | `""` | Comma-separated security group ids. |
| `SANDBOX_BEDROCK_REGION` | `""` | AWS region. Empty uses the default AWS configuration. |
| `SANDBOX_BEDROCK_RUNTIME_NAME` | `""` | Runtime name. Empty means `seizu_sandbox_<SEIZU_DEPLOYMENT_ID>`. |
| `SANDBOX_BEDROCK_QUALIFIER` | `DEFAULT` | Runtime endpoint to invoke. |
| `SANDBOX_BEDROCK_MANAGE_RUNTIME` | `true` | Create and update the runtime from these settings. `false` only looks it up. |
| `SANDBOX_BEDROCK_STORAGE` | `session` | `session` or `s3`; see [Sandbox storage](#sandbox-storage). |
| `SANDBOX_BEDROCK_S3_BUCKET` | `""` | Bucket for `s3` storage. |
| `SANDBOX_BEDROCK_S3_PREFIX` | `""` | Key prefix for `s3` storage. Empty means `seizu-sandbox/<SEIZU_DEPLOYMENT_ID>`. |
| `SANDBOX_BEDROCK_S3_BUCKET_OWNER` | `""` | Account that must own the bucket. Empty means the runtime's account. |
| `SANDBOX_BEDROCK_S3_BUCKET_REGION` | `""` | Bucket region. Empty means the runtime's region. |
| `SANDBOX_BEDROCK_MAX_LIFETIME_SECONDS` | `28800` | Longest a sandbox session runs before AgentCore stops it (60–28800). Its idle timeout is `SANDBOX_SESSION_TIMEOUT_SECONDS`. |

`CHAT_SESSION_REAP_ENABLED` and `CHAT_SESSION_REAP_IDLE_SECONDS` control the
idle sweep described above.

## Other users of the sandbox

The `cve_dependency_remediation` [workflow](cve-remediation.html)
drives a sandbox directly, with no chat session involved, to run a coding-agent
CLI against a cloned repository with phase-isolated credentials. It shares
`SANDBOX_API_KEY` and `SANDBOX_DOMAIN` and adds its own `REMEDIATION_*`
settings. It requires `SANDBOX_PROVIDER=e2b`.

---

The internals — the backend protocol, how suspension and resume are implemented,
why results are routed by size rather than by the model, and what was measured
along the way — are in the
[sandbox decision log](../dev/decisions/sandbox.md).
