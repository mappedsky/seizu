Seizu (星図)
============

What is Seizu?
--------------

`Seizu (星図) <https://mappedsky.github.io/seizu/>`_ is a react/mui frontend and python backend for various forms of reporting of Neo4j graph data.
It is well suited for building reporting for tools like `cartography <https://github.com/lyft/cartography>`_ and `starbase <https://github.com/JupiterOne/starbase>`_

Seizu includes:

* A configuration-driven react/mui frontend, with support for a dashboard, arbitrary reports, using a row/panel based layout with various panel types for visualizing data
* **Spaces** for grouping related reports, with optional sub-spaces for organizing within a space and any one report pinned as the space's landing page
* An interactive **Query Console** for running ad-hoc Cypher queries, with graph, table, and raw result views, and a collapsible database schema browser showing available node labels, relationship types, and property keys
* Temporal-backed **Workflows** with sequential stages, parallel activities, named outputs, and time or graph-event schedules
* An **MCP server** at ``/api/v1/mcp`` that exposes user-defined Cypher-backed tools to LLM agents such as Claude
* A built-in **chat assistant** — an LLM agent that answers questions about your graph using the same tools and skills, with confirmation-gated writes, and can run headlessly on a schedule
* A mechanism of providing SSO for Neo4j, when Seizu is placed behind an OAuth2 proxy

Getting started
---------------

Seizu has a `quickstart guide <https://mappedsky.github.io/seizu/install/quickstart.html>`_, which can be used for evaluation, or development.

Documentation
-------------

* `Installation documentation <https://mappedsky.github.io/seizu/install/backend.html>`_
* `Upgrade guide <https://mappedsky.github.io/seizu/install/upgrading.html>`_
* `Dashboard configuration <https://mappedsky.github.io/seizu/install/dashboard.html>`_
* `Spaces documentation <https://mappedsky.github.io/seizu/install/spaces.html>`_
* `CLI documentation <https://mappedsky.github.io/seizu/install/cli.html>`_
* `Security guidance <https://mappedsky.github.io/seizu/install/security.html>`_
* `Query Console <https://mappedsky.github.io/seizu/install/query-console.html>`_
* `Chat assistant documentation <https://mappedsky.github.io/seizu/install/chat.html>`_
* `Workflow documentation <https://mappedsky.github.io/seizu/install/workflows.html>`_
* `Scheduled chat documentation <https://mappedsky.github.io/seizu/install/chat-schedules.html>`_
* `Temporal workflow documentation <https://mappedsky.github.io/seizu/install/temporal-workflows.html>`_
* `Scheduled cartography sync documentation <https://mappedsky.github.io/seizu/install/cartography-sync.html>`_
* `Sandbox delegation documentation <https://mappedsky.github.io/seizu/install/sandbox.html>`_
* `MCP Toolsets documentation <https://mappedsky.github.io/seizu/install/mcp-toolsets.html>`_
* `MCP Skillsets documentation <https://mappedsky.github.io/seizu/install/mcp-skillsets.html>`_
* `External MCP proxy documentation <https://mappedsky.github.io/seizu/install/external-mcp.html>`_
* `Basic development documentation <https://mappedsky.github.io/seizu/dev/dependencies.html>`_
* `Decision log <https://mappedsky.github.io/seizu/dev/decisions/index.html>`_ — why the code is the way it is, per product area

.. toctree::
    :caption: Installation & Configuration
    :hidden:

    install/quickstart
    install/backend
    install/upgrading
    install/dashboard
    install/spaces
    install/cli
    install/security
    install/query-console
    install/chat
    install/workflows
    install/scheduled-queries
    install/chat-schedules
    install/temporal-workflows
    install/cartography-sync
    install/sandbox
    install/mcp-toolsets
    install/agent-plugins
    install/mcp-skillsets
    install/external-mcp

.. toctree::
    :caption: Development
    :hidden:

    dev/dependencies
    dev/test
    dev/query-validation
    dev/decisions/index
    dev/contributing

.. toctree::
    :caption: Get In Touch
    :hidden:

    contact/security
    contact/code-of-conduct
