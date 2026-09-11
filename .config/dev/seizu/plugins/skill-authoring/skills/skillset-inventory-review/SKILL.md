---
name: skillset-inventory-review
description: Inspect existing Seizu skillsets and skills, then recommend concrete
  improvements to templates, triggers, parameters, and required tools.
allowed-tools: mcp__seizu__skillsets__list mcp__seizu__skillsets__get mcp__seizu__skillsets__list_versions
  mcp__seizu__skillsets__list_skills mcp__seizu__skillsets__get_skill mcp__seizu__skillsets__list_skill_versions
---
Review Seizu's skillset and skill inventory for the area the `focus` input names. Use the skillsets MCP tools as the source of truth; do not infer stored skills from memory.

Inputs — the values arrive in the `## Inputs` block below these instructions:
- `focus` — the skillset, skill, domain, or review goal to concentrate on.
- `include_versions` — whether to review version history as well.

Workflow:
1. Call `skillsets__list` to identify available skillsets.
2. For every relevant skillset, call `skillsets__get` and `skillsets__list_skills`.
3. For every relevant skill, call `skillsets__get_skill` and inspect its name, description, parameters, triggers, tools_required, enabled state, and effective_enabled state.
4. If `include_versions` is true, call `skillsets__list_versions` for relevant skillsets and `skillsets__list_skill_versions` for relevant skills to identify recent churn or stale definitions.

Review criteria:
- Skill and skillset IDs are lower_snake_case, specific, and stable.
- Descriptions explain when to use the skill, not just what it is named.
- Templates include clear workflow steps, output expectations, tool-use order, and evidence/uncertainty rules.
- Parameters are minimal, typed correctly, and have defaults only when a safe default exists.
- Triggers are natural phrases users are likely to ask.
- tools_required contains every tool the rendered skill instructs the agent to call, and no unrelated tools.
- Disabled skills or disabled parent skillsets are called out with operational impact.

Output format:
- `Inventory Summary`: count skillsets and skills reviewed; note disabled items.
- `High-Value Skills`: skills that are well-structured and why.
- `Issues`: ordered findings with skillset_id, skill_id when applicable, impact, and exact recommended change.
- `Suggested Edits`: concrete new descriptions, triggers, parameters, tools_required, or template changes.
- `Follow-up`: any additional skillsets or skills that should be created.
- Keep the response evidence-backed and separate confirmed stored metadata from recommendations.
