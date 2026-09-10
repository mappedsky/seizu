---
name: skill-authoring-workflow
description: Design, create, or update Seizu skillsets, skills, toolsets, and Cypher-backed
  tools, including the self-referential skill_authoring skillset itself.
allowed-tools: mcp__seizu__skillsets__list mcp__seizu__skillsets__get mcp__seizu__skillsets__create
  mcp__seizu__skillsets__update mcp__seizu__skillsets__list_versions mcp__seizu__skillsets__list_skills
  mcp__seizu__skillsets__get_skill mcp__seizu__skillsets__list_skill_versions mcp__seizu__skillsets__create_skill
  mcp__seizu__skillsets__update_skill mcp__seizu__skillsets__render_skill mcp__seizu__toolsets__list
  mcp__seizu__toolsets__get mcp__seizu__toolsets__create mcp__seizu__toolsets__update
  mcp__seizu__toolsets__list_versions mcp__seizu__toolsets__list_tools mcp__seizu__toolsets__get_tool
  mcp__seizu__toolsets__create_tool mcp__seizu__toolsets__update_tool mcp__seizu__graph__schema
  mcp__seizu__graph__validate_query mcp__seizu__graph__explain
---
Help implement the Seizu skill authoring request the `request` input carries.

Inputs — the values arrive in the `## Inputs` block below these instructions: They are inputs to THIS skill, not arguments to any tool.
- `request` — the skillset or skill authoring change to design or apply.
- `target_skillset_id` — the existing or proposed skillset id, if known.
- `target_skill_id` — the existing or proposed skill id, if known.
- `dry_run` — when true, produce payloads without writing anything.

Never pass `request`, `target_skillset_id`, `target_skill_id`, or `dry_run` as
parameters to a `skillsets__*` tool — they are not recognized tool fields. When you
call a tool, map `target_skillset_id` to the tool's `skillset_id` and
`target_skill_id` to the tool's `skill_id`. Use `dry_run` only to decide whether to
call a write tool at all; never send it as a tool argument.

Tool arguments — use exactly these field names and no others:
- skillsets__create: skillset_id, name, description, enabled
- skillsets__update: skillset_id, name, description, enabled, comment
- skillsets__create_skill: skillset_id, skill_id, name, template, description, parameters, triggers, tools_required, enabled
- skillsets__update_skill: skillset_id, skill_id, name, template, description, parameters, triggers, tools_required, enabled, comment
- toolsets__create: toolset_id, name, description, enabled
- toolsets__update: toolset_id, name, description, enabled, comment
- toolsets__create_tool: toolset_id, tool_id, name, description, cypher, parameters, enabled
- toolsets__update_tool: toolset_id, tool_id, name, description, cypher, parameters, enabled, comment
`name` and `template` are required on every create_skill and update_skill call;
`name` and `cypher` are required on every create_tool and update_tool call.
`parameters` is a list of objects shaped {name, type, description, required, default}.

Full-object writes: skillsets__update, skillsets__update_skill, toolsets__update, and
toolsets__update_tool REPLACE the stored record. Any optional field you omit resets to
its default (enabled -> true, parameters / triggers / tools_required -> empty). Always
call the matching get tool first (`skillsets__get` / `skillsets__get_skill` /
`toolsets__get` / `toolsets__get_tool`) and resend the complete merged object, keeping
every existing field you are not deliberately changing.

Cypher tools: toolsets__create_tool / toolsets__update_tool carry a read-only `cypher`
query. A write or invalid query is rejected by the create/update call, and creating a
tool is a mutating (approval-gated) action — never spend an approval on a query you have
not checked. Before any tool create/update with cypher:
1. Call `graph__schema` to see available node labels, relationship types, property keys,
   and indexes.
2. Call `graph__validate_query` with the tool's exact cypher. If `valid` is false, fix
   the query and re-validate; do not call create_tool/update_tool until it is valid.
3. Call `graph__explain` and read the returned plan. Prefer queries that match an indexed
   label/property (see the indexes from graph__schema) and avoid full scans (e.g.
   AllNodesScan, or a label scan over a large label); adjust the query to use an index
   where possible, and surface any unavoidable full scan as a risk.
4. Keep the tool's `parameters` in sync with the cypher: every `$param` the query
   references MUST be declared in `parameters` (with a type), and remove any declared
   parameter the new query no longer uses. create_tool/update_tool rejects a query that
   references an undeclared parameter (it would otherwise fail every call), so when you
   change a query's parameters you must update the `parameters` list in the same write.
Only after the cypher validates and the plan is acceptable, call create_tool/update_tool.

Operating rules:
- Treat `dry_run=true` as plan-only. Do not call create or update tools in dry-run mode; produce the exact proposed payloads instead.
- Treat `dry_run=false` as an apply request: after inspecting current state and validating cypher, you MUST call the create/update tools in this same run. Do not stop after validation or after presenting payloads — produce the output sections AND apply the changes in one pass. (Writes are confirmation-gated, so the user still approves each mutation.) The default is to apply; only use dry_run=true when the request explicitly asks for a preview or plan.
- Never delete a skillset or skill from this workflow. If deletion seems necessary, recommend it separately and require explicit user confirmation outside this skill.
- Preserve existing enabled state unless the request explicitly says otherwise.
- Use lower_snake_case IDs, at most 31 characters each (so the full skillset__skill / toolset__tool name stays under the 64-char limit). Do not rename IDs unless the user explicitly requests a migration.
- Keep templates actionable: include required tool order, output format, evidence rules, and limits.
- Every tool named in a template's workflow must appear in tools_required unless it is only mentioned as an example.

Workflow:
1. Determine the scope from the request: does it target skills/skillsets, toolsets/tools, or both? Run only the discovery and writes for the scope it actually involves. Do not inspect the side you are not changing — for a tools-only request, skip `skillsets__list` and every `skillsets__*` call; for a skills-only request, skip `toolsets__list` and every `toolsets__*` and `graph__*` call. This conserves the per-step tool-call budget.

Skill / skillset work (only if in scope):
2. Call `skillsets__list`. If `target_skillset_id` is provided, call `skillsets__get` and `skillsets__list_skills`; if it is missing, decide whether to call `skillsets__create`.
3. If `target_skill_id` is provided, call `skillsets__get_skill` and inspect the existing template, parameters, triggers, tools_required, and enabled state (use `skillsets__list_skill_versions` / `skillsets__list_versions` to review history before editing).
4. Validate any proposed template placeholders against the proposed parameter list — every placeholder must have a matching parameter with a supported type.
5. Prepare or call `skillsets__create` / `skillsets__update` for the skillset and `skillsets__create_skill` / `skillsets__update_skill` for the skill.

Toolset / tool work (only if in scope):
6. Call `toolsets__list`. For a target toolset call `toolsets__get` and `toolsets__list_tools` (and `toolsets__get_tool` for an existing tool); create the toolset with `toolsets__create` if it is missing.
7. Before any `toolsets__create_tool` / `toolsets__update_tool` whose `cypher` you set or change, run the "Cypher tools" checks (graph__schema -> graph__validate_query -> graph__explain) and only write once the cypher is valid and the plan is acceptable. Call `graph__schema` once and reuse it for every tool in the same toolset rather than re-fetching it per tool.

8. After applying any change, call the relevant get/list tool to verify the stored result.

Output format:
- `Intent`: restate the requested skill or tool authoring change.
- `Current State`: summarize the discovered existing skillset/skill or toolset/tool state.
- `Plan`: ordered implementation steps.
- `Payloads`: include exact skillset, skill, toolset, and tool payloads, including parameters, triggers, tools_required, enabled, template, and (for tools) the validated cypher.
- `Applied Changes`: if `dry_run=false`, list each tool call made and the resulting IDs/versions.
- `Verification`: describe post-write checks or dry-run validation.
- `Risks and Follow-up`: note permission limits, blocked tools, missing required data, or manual review needed.
