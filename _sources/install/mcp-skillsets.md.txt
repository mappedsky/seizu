# MCP Skillsets

## Purpose

Seizu can expose user-defined MCP prompts as versioned **skillsets** and **skills**.
A skill is a prompt template with typed arguments. MCP clients discover enabled skills as prompts named `{skillset_id}__{skill_id}`.

## Managing Skillsets

Navigate to **MCP Skillsets** in the sidebar to view skillsets.

Creating a skillset requires:

| Field | Description |
|-------|-------------|
| id | Immutable lower_snake_case ID. |
| name | User-friendly display name. |
| description | Optional description. |
| enabled | When disabled, none of the skillset's skills are exposed via MCP. |

Skillsets and skills keep version history. Edit forms include an optional comment that is stored with each version.

## Managing Skills

Click a skillset row to manage its skills.

Creating a skill requires:

| Field | Description |
|-------|-------------|
| id | Immutable lower_snake_case ID. |
| name | User-friendly display name. |
| description | Optional description shown to MCP clients. |
| template | Prompt text using `{% $param_name %}` placeholders. |
| parameters | Typed arguments: `string`, `integer`, `float`, or `boolean`. |
| triggers | Optional list of phrases that describe when an agent should use the skill. |
| tools required | Optional list of MCP tool names, selected from Seizu tools, that the skill expects an agent to use. |
| enabled | When disabled, the skill is hidden from MCP clients. |

Parameter names and placeholders must be lower_snake_case. Unknown placeholders are rejected when creating or updating a skill. Skill templates only do variable substitution — they are not rendered as Markdoc and do not support tags or conditionals. Anything that is not a `{% $name %}` reference (including stray `{{...}}`) is preserved verbatim in the rendered prompt.
Required tools are stored as MCP names such as `graph__query` or `{toolset_id}__{tool_id}` and are validated against Seizu's tool catalog.

When a rendered skill has triggers or required tools, Seizu prepends generated frontmatter:

```yaml
---
triggers:
  - "summarize a vulnerable asset"
tools_required:
  - "graph__query"
---
```

The template editor only stores the prompt body. Users do not need to write this frontmatter by hand.

## CLI

```bash
seizu skillsets list
seizu skillsets create investigations "Investigations"
seizu skillsets skills create investigations summarize_node \
  --name "Summarize node" \
  --template "Summarize {% $node_id %} for a security analyst." \
  --parameters '[{"name":"node_id","type":"string","required":true}]' \
  --triggers '["summarize node context"]' \
  --tools-required '["graph__query"]'
seizu skillsets skills render investigations summarize_node --args-json '{"node_id":"abc"}'
```

Bulk seed/export supports top-level `skillsets` in the YAML config.
