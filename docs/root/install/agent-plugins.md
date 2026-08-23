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

The mapping key must equal `skillsetId`. Seeding validates the package before
installing it and compares its content digest, so an unchanged package does not
create another revision. Export preserves existing source declarations but
does not invent filesystem paths for plugins installed through another client.

The package must contain `plugin.json` at its root. Skills are discovered only
from immediate child directories under `skills/`, as required by 1.0.0; nested
namespaced discovery proposed for later versions is not enabled.

## Seizu extension

Add Seizu metadata under the stable extension namespace:

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "security-investigations",
  "extensions": {
    "com.mappedsky.seizu": {
      "skillsetId": "security_investigations",
      "skills": {
        "review-repository": {
          "skillId": "review_repository",
          "title": "Review repository",
          "enabled": true,
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

`skillsetId` is required, immutable after installation, and uses Seizu's
lower-snake identifier grammar. The MCP prompt name remains
`skillsetId__skillId`. `skillId` is optional; without it Seizu converts the
portable hyphenated Agent Skill name to lower snake case.

## Tool dependencies

Use the standard Agent Skills `allowed-tools` frontmatter field:

```yaml
---
name: review-repository
description: Review one repository for security issues.
allowed-tools: graph__query mcp:github/get_file
---
```

For Seizu-recognized values, `allowed-tools` declares both disclosure and a
required dependency. A skill is absent from an individual user's prompt list
when a required tool is unavailable to that user. It never grants a permission
or bypasses an action confirmation; the normal RBAC and confirmation checks
still apply. Unrecognized portable tokens are preserved and ignored by Seizu.

Supported Seizu forms are:

- exact Seizu MCP tool names, such as `graph__query`;
- existing external proxy names, such as `ext__github__get_file`;
- logical plugin MCP references, `mcp:<server>/<tool>`.

For a logical reference, `<server>` names an entry in the package's `mcp.json`.
Seizu never connects to that address directly. Configure the identity proxy's
`upstream_urls` entry to associate the portable endpoint with the proxy:

```json
[
  {
    "name": "github",
    "url": "https://proxy.example/mcp/github",
    "upstream_urls": ["https://api.example/mcp"],
    "transport": "streamable_http",
    "auth_mode": "header_delegation"
  }
]
```

The proxy URL remains the only network destination used by Seizu. `stdio` MCP
entries are retained as package diagnostics and are not started.

A proxy may also advertise aliases in its MCP initialize result under
`capabilities.extensions.com.mappedsky.seizu.upstreamUrls`. Operator-configured
`upstream_urls` always wins; advertised aliases are used only when exactly one
enabled proxy claims the package endpoint.

`MCP_EXTERNAL_PLUGIN_URL_MATCH_MODE` controls how the declaration binds to a
proxy. The default, `none`, ignores the package URL and uses an enabled proxy
whose name is the same as the `mcp.json` server name. `lax` prefers a configured
or advertised URL alias and falls back to that same-name lookup. `strict`
requires exactly one URL alias match. Every mode still requires the user's
discovered inventory to contain the exact remote tool.

## Files and scripts

Published package files are immutable MCP resources with URIs of the form:

```text
seizu://plugins/<plugin>/versions/<revision>/files/<path>
```

Each enabled skill is listed as one MCP resource carrying its title,
description and declared `allowed-tools`; the packaged files beneath it are not
enumerated. A resource template advertises
`seizu://plugins/{plugin_id}/versions/{revision}/files/{path}`, and any file in a
published revision can be read at that URI.

References and assets enter model context only when the agent reads their
resource. During chat, a selected skill is also materialized inside the
conversation sandbox under `/home/user/seizu_plugins/`. Scripts run only with
`sandbox__run_script`; arguments are passed as an argv array without a shell.
Seizu web and worker processes never execute package code.

The authoring UI stages the whole package in the browser. It provides structured
fields for the known `plugin.json` metadata and each skill's `SKILL.md` front
matter, instructions, tools, triggers, aliases, and template variables.
Supporting files can be created or uploaded into that skill's `references/`,
`scripts/`, or `assets/` directory; scripts are marked executable.

Nothing reaches the server until **Publish**, which submits the complete package
in one request and creates one immutable revision. Files the editor did not
change are carried by their SHA-256 digest rather than re-uploaded, so a
one-line edit to a skill does not push its assets back through the browser. The
publish names the revision it was loaded from and is refused with `409` if the
plugin has been published in the meantime, so concurrent edits cannot silently
revert each other. Unpublished edits live only in that browser tab; leaving the
editor prompts before discarding them. Default package bounds are 10 MiB
compressed, 25 MiB unpacked, 500 files, 10 MiB per file, and 512 KiB per
`SKILL.md`.

## Skill inputs

A skill declares its inputs in the Seizu extension, and `prompts/get` returns
two messages: the `SKILL.md` body exactly as packaged, then a rendered `Inputs`
block carrying this invocation's values. Write instructions that refer to an
input by name — "the `repo` input" — rather than substituting it, so the file
reads the same to a consumer that does not implement Seizu's extension.

`{% $name %}` placeholders in a body are still substituted, so existing packages
keep working, but publishing one records a `templated_skill_body` warning.

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

## Compatibility

Existing skillsets are projected into one plugin per skillset at startup, with
their existing `skillset__skill` prompt names preserved. The `/api/v1/skillsets`
REST routes, `skillsets__*` MCP tools, CLI commands, and permission names remain
compatibility aliases for one release.
