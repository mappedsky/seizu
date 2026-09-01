# Agent Plugins

Seizu supports server-side installation of packages targeting Agent Plugins
1.0.0. One plugin is one Seizu skill namespace. Create and author a plugin in
the web UI at `/app/plugins`, install an existing ZIP there, or use the CLI:

```bash
seizu plugins validate ./my-plugin
seizu plugins install ./my-plugin
```

Packages can also be installed by the bulk YAML seeder. The source may be a
directory or ZIP and is resolved relative to the YAML file:

```yaml
plugins:
  security_investigations:
    source: plugins/security-investigations
    enabled: true
```

The mapping key must equal the plugin's id, which is derived from the package
`name` — see [Identity](#identity). Seeding validates the package before
installing it and compares its content digest, so an unchanged package does not
create another revision. Export preserves existing source declarations but
does not invent filesystem paths for plugins installed through another client.

The package must contain `plugin.json` at its root. Skills are discovered only
from immediate child directories under `skills/`, as required by 1.0.0; nested
namespaced discovery proposed for later versions is not enabled.

## Seizu extension

The `com.mappedsky.seizu` extension is optional: a stock Agent Plugins 1.0.0
package installs unmodified. Add it to give a skill a title, triggers, aliases,
or inputs:

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "security-investigations",
  "extensions": {
    "com.mappedsky.seizu": {
      "skills": {
        "review-repository": {
          "title": "Review repository",
          "triggers": ["Review a repository"],
          "parameters": [
            {"name": "repository", "type": "string", "required": true}
          ],
          "aliases": []
        }
      }
    }
  }
}
```

Each key under `skills` is a skill's directory name under `skills/`.

## Tool dependencies

Use the standard Agent Skills `allowed-tools` frontmatter field:

```yaml
---
name: review-repository
description: Review one repository for security issues.
allowed-tools: mcp__seizu__graph__query mcp__github__get_file_contents
---
```

Note that Seizu reads this field differently from the Agent Skills
specification, which calls it "tools that are pre-approved to run". Here it is a
**dependency**: a skill is absent from an individual user's prompt list when a
listed tool is unavailable to that user. It never grants a permission or
bypasses an action confirmation; the normal RBAC and confirmation checks still
apply. A package authored elsewhere that lists tools defensively therefore
becomes unavailable here if any one of them is missing. Tokens that are not MCP
references — `Read`, `Bash(git:*)` — are the consumer's own built-ins,
preserved and ignored.

Every dependency is named `mcp__<server>__<tool>`, the convention agent clients
already use, and Seizu is the server named `seizu`:

```yaml
allowed-tools: mcp__seizu__graph__query mcp__github__get_file_contents
```

A skill's *instructions* still name tools as the host presents them at call
time — `github_security__repo_risk_summary`, `ext__github__get_file_contents` —
because that is what the agent has to call. The frontmatter is the portable
contract; the body describes the tools of whichever host renders it, and the
rendered skill carries that host's resolved names.

`mcp__seizu__<tool>` names one of Seizu's own MCP tools; `seizu` is reserved and
cannot be claimed by anything else. Any other server must be declared in the
package's `mcp.json`.

**Seizu never connects to the address a package declares.** It only ever talks
to the external MCP proxies an operator has configured, so the declared URL is
at most a hint used to work out which proxy a server means.
`MCP_EXTERNAL_PLUGIN_URL_MATCH_MODE` decides whether it is used for even that:

| Mode | How a declared server binds to a proxy |
|------|----------------------------------------|
| `none` (default) | The URL is ignored entirely; the proxy whose name matches the `mcp.json` server name is used |
| `lax` | Prefer a proxy that claims the URL, else fall back to the name match |
| `strict` | Require exactly one proxy claiming the URL |

Under `lax` and `strict`, a proxy claims a URL through an `upstream_urls` entry
in `MCP_EXTERNAL_PROXIES`; see [External MCP](external-mcp.md) for configuring
proxies. In every mode the tool must also be present in that user's discovered
inventory. `stdio` entries are kept as package diagnostics and never started.

## Files and scripts

A skill can ship `references/`, `scripts/` and `assets/` alongside its
`SKILL.md`.

**Those files only reach a skill when the sandbox is enabled.** Rendering a
skill attaches and materializes its files when the skill ships `scripts/` *and*
the caller holds `sandbox:delegate`; scripts then run through
`sandbox__run_script` inside the conversation's sandbox. Seizu's web and worker
processes never execute package code.

Without the sandbox, a skill that references its files **still renders and still
runs** — the agent gets the instructions, just not the script files, so any step
that depends on running one cannot be carried out. See
[Sandbox](sandbox.md) for enabling it.

Packaged files are also readable as MCP resources by agents connected to
Seizu's MCP endpoint, which discover them without configuration.

Default package bounds are 10 MiB compressed, 25 MiB unpacked, 500 files,
10 MiB per file, and 512 KiB per `SKILL.md`.

## Identity

A package's Seizu namespace is derived from its `name` in `plugin.json`, and a
skill's id from its directory name: `security-review` becomes
`security_review`, so its skills are named `security_review__<skill>`. Nothing
declares an id separately — to change one, rename the package or the skill
directory.

A name therefore has to derive a valid identifier: lower-case words separated by
single hyphens, starting with a letter, at most 31 characters. `2fa-tools` and
anything longer are refused at publish with that constraint named.

## Turning skills on and off

A package does not declare whether its skills are on. Every skill a revision
introduces starts enabled, and republishing never changes what an operator
chose. Set it when installing or updating:

```bash
seizu plugins skill-disable security_review scan_repository
```

```yaml
plugins:
  security_review:
    source: plugins/security-review
    enabled: true
    skills:
      scan_repository: false
```

or from the plugin's detail dialog in the UI. `PUT /api/v1/plugins/{id}/skills/{skill_id}`
is the underlying call, and `plugins__set_skill_enabled` exposes it over MCP.

## Skill inputs

A skill declares its inputs as `parameters` in the Seizu extension. Seizu
injects the values for an invocation: `prompts/get` returns the `SKILL.md` body
exactly as packaged, followed by a rendered `Inputs` block holding this call's
values.

Write instructions that refer to an input by name — "the `repository` input" —
so the file reads the same to a consumer that does not implement Seizu's
extension.

## Revisions and package versions

Seizu assigns every publish a **revision** (`v1`, `v2`, …) and a **package
digest** over the package's contents. Those two are what identify a package
inside Seizu: the version history, restore, MCP resource URIs, sandbox
materialization paths, and seed idempotency all key on them, never on the
manifest's `version` field.

`version` in `plugin.json` is the author's own declaration and is not required
to change when contents do. Nothing inside Seizu breaks if it does not — but it
is the only handle an *exported* package carries. Once a package is downloaded
and installed elsewhere, tools outside Seizu have nothing but the version to
compare, so two revisions that both declare `1.0.0` are indistinguishable to
them. Publishing changed contents under an unchanged version therefore records a
non-blocking `unchanged_package_version` warning on the revision, visible in the
plugin's diagnostics.
