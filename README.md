![seizu logo](/images/logo-horizontal-black.svg#gh-light-mode-only)
![seizu logo](/images/logo-horizontal-white.svg#gh-dark-mode-only)

# Seizu (星図)

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/mappedsky/seizu/badge)](https://scorecard.dev/viewer/?uri=github.com/mappedsky/seizu)

## What is Seizu?

[Seizu (星図)](https://mappedsky.github.io/seizu/) is a star chart for your security graph: a React + Python frontend for Neo4j data, built to visualize, analyze, and automate security graphs built from tooling like [Cartography](https://github.com/cartography-cncf/cartography), [Starbase](https://github.com/JupiterOne/starbase), etc.

Seizu includes:

* Reports, browser-editable dashboards and markdown, with a row/panel layout and multiple panel types for visualizing Cypher query results
* Spaces, a grouping of reports
* An interactive Cypher query console with schema browsing and per-user history
* A built-in MCP server that exposes user-defined tools and skills so LLM agents can analyze the graph alongside you; skills are based on [Agent Plugins](https://agent-plugins.org/), and are reusable across seizu, claude and other agents that support agent plugins
* An AI agent, based on langgraph, backed by temporal, with sandbox support (with a persistent filesystem); orchestrated or one-shot. Has per-turn budgeting, and admin-configured, and user selectable provider selection (model/reasoning level); external MCP support, via connection to an MCP proxy
* An AI assistant, powered by the AI agent, which can help users do analysis, update reports, etc without leaving seizu
* Workflows, which can run module on a schedule, or triggered by graph updates or other worflows. Workflows are based on temporal, and can run complex chains of seqeuential stages, with activities that run in parallel per stage. All activities have named outputs, and can use data from named inputs (include graph queries, or output from AI agent module runs).
* Scheduled AI assistant chats, based on the AI agent and temporal, which can allow users to run analysis, build/update reports, and more on a schedule they define (permissioned separately than workflows)
* Native OIDC / JWT auth
* Fine-grained RBAC, for tight control over which users can use which features

## Getting started

Seizu has a [quickstart guide](https://mappedsky.github.io/seizu/install/quickstart.html), which can be used for evaluation, or development.

## Documentation

* [Quickstart](https://mappedsky.github.io/seizu/install/quickstart.html)
* [Installation documentation](https://mappedsky.github.io/seizu/install/backend.html)
* [Dashboard configuration](https://mappedsky.github.io/seizu/install/dashboard.html)
* [Scheduled query documentation](https://mappedsky.github.io/seizu/install/scheduled-queries.html)
* [Basic development documentation](https://mappedsky.github.io/seizu/dev/dependencies.html)
