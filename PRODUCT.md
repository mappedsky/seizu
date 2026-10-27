# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Security engineers (cloud and product security) at organizations that already
build a security graph in Neo4j, most often with Cartography. They own the
graph: they write Cypher, build and maintain reports and spaces, author tools
and skills, configure workflows and scheduled chats, and investigate with the
chat assistant. They are technical, fluent in their own infrastructure, and
come to Seizu to answer a question about their environment or to automate
answering it.

Other audiences (readers of published dashboards, external agents connecting
over MCP) exist but are secondary; design decisions favour the engineer who
builds and operates the graph.

## Product Purpose

Seizu (星図, "star chart") is a configuration-driven reporting, analysis and
automation platform for Neo4j security graphs. It turns a graph that would
otherwise be reachable only through raw Cypher into reports, an interactive
query console, an AI assistant that can investigate and edit reports, and
durable workflows that act on what the graph shows.

Success means a security engineer can go from a question about their
environment to a trustworthy answer, a shared report, or a recurring automation
without leaving Seizu and without handing their data to a vendor.

## Positioning

- **Open source and self-hosted.** It runs in the operator's own
  infrastructure over their own Neo4j; no vendor holds the data.
- **Bring-your-own graph.** Any Neo4j security graph — Cartography, Starbase,
  or custom — not a fixed vendor schema.
- **Config-as-code.** Reports, spaces, toolsets, skills, roles and workflows
  are versioned, YAML-seedable and exportable.
- **Agent-native.** The same tools and skills serve the in-app assistant,
  scheduled chats, workflows and external agents over MCP, all under one RBAC
  model. Skills are Agent Plugins packages, portable to other agents.

## Operating Context

- Deployed by the security team itself (Docker Compose for evaluation and
  development, Helm on Kubernetes for production), with OIDC/JWT auth and
  role-based permissions (Viewer ⊂ Editor ⊂ Admin plus user-defined roles).
- Data arrives from Cartography syncs, which Seizu can schedule itself.
- Daily work spans: reading dashboards and reports; ad-hoc Cypher in the query
  console with schema browsing and history; multi-turn investigations with the
  chat assistant (planned, parallel steps, sandboxed code execution, budgeted
  per turn); building workflows from staged, parallel activities; reviewing
  scheduled chat and workflow runs.
- Most entities carry version history, and editing is a deliberate, attributed
  act (who saved what, when, with what comment).
- Mutating actions taken by the assistant are confirmation-gated unless the
  user holds and explicitly uses the bypass permission.

## Capabilities and Constraints

- Web app: React 19 + TypeScript + Material-UI, served by a FastAPI backend.
  Desktop-first operational tool; dark theme is the identity.
- Core surfaces: dashboard and reports (row/panel layout, Cypher-backed panels
  including counts, tables, bar/pie charts, graph views, markdown), spaces and
  sub-spaces, query console, chat assistant (with a details pane showing plan
  steps and model thinking), plugins/skills, toolsets, workflows, scheduled
  chats, roles, model profiles.
- Features are gated by server feature flags and per-user permissions; the UI
  must hide or disable what a user cannot do rather than fail on click.
- User identity is shown as display name/username/email, never raw ids.
- Terminology: reports, spaces/sub-spaces, panels, queries, toolsets/tools,
  plugins/skills, workflows/stages/activities, scheduled chats, turns.

## Brand Commitments

- Name: **Seizu (星図)**. Tagline: "A star chart for your security graph."
- Identity is a deep-space observatory, defined in AGENTS.MD (Branding) and
  `src/theme/brand.ts`, which is the source of truth for colour tokens. No new
  brand colours or fonts may be introduced.
- Logo assets: `public/static/images/` and `images/` (horizontal lockups,
  mark-only variants, favicon), with fixed mark geometry documented in
  AGENTS.MD.

## Evidence on Hand

- Product documentation under `docs/root/install/` (quickstart, chat, spaces,
  workflows, sandbox, agent plugins, external MCP, Helm, security) and the
  public docs site at mappedsky.com/seizu.
- OpenSSF Scorecard badge on the repository.
- A seed dashboard (`.config/dev/seizu/reporting-dashboard.yaml`) providing
  real example reports over Cartography data.
- No customer logos, testimonials, case studies, usage figures or pricing
  exist; none may be fabricated.

## Product Principles

1. **The graph is the operator's.** Never imply data leaves their
   infrastructure or depends on a vendor.
2. **Show the work.** Answers — from a panel or the assistant — should expose
   the query, plan, steps and cost behind them so an engineer can trust or
   check them.
3. **Everything durable is versioned and attributable.** Edits, restores and
   automated changes are visible as history, with who and when.
4. **Permission-aware, fail closed.** The interface reflects what the user may
   do; risky actions ask for confirmation.
5. **One capability, every surface.** A tool or skill built once should work
   the same in the UI, the assistant, workflows and over MCP.

## Accessibility & Inclusion

No formal standard is mandated. WCAG 2.1 AA is the working floor (brand tokens
already include AAA-contrast variants for light surfaces); full keyboard
operation matters for the query console and chat.
