Seizu (星図)
============

What is Seizu?
--------------

`Seizu (星図) <https://mappedsky.github.io/seizu/>`_ is a react/mui frontend and python backend for various forms of reporting of Neo4j graph data.
It is well suited for building reporting for tools like `cartography <https://github.com/lyft/cartography>`_ and `starbase <https://github.com/JupiterOne/starbase>`_

Seizu includes:

* A configuration-driven react/mui frontend, with support for a dashboard, arbitrary reports, using a row/panel based layout with various panel types for visualizing data
* An interactive **Query Console** for running ad-hoc Cypher queries, with graph, table, and raw result views, and a collapsible database schema browser showing available node labels, relationship types, and property keys
* Temporal-backed **Workflows** that run named Cypher inputs on a time or graph-event schedule and pass their results through an ordered activity pipeline
* An **MCP server** at ``/api/v1/mcp`` that exposes user-defined Cypher-backed tools to LLM agents such as Claude
* A built-in **chat assistant** — an LLM agent that answers questions about your graph using the same tools and skills, with confirmation-gated writes, and can run headlessly on a schedule
* A mechanism of providing SSO for Neo4j, when Seizu is placed behind an OAuth2 proxy

Getting started
---------------

Seizu has a `quickstart guide <https://mappedsky.github.io/seizu/install/quickstart.html>`_, which can be used for evaluation, or development.

Documentation
-------------

* `Installation documentation <https://mappedsky.github.io/seizu/install/backend.html>`_
* `Dashboard configuration <https://mappedsky.github.io/seizu/install/dashboard.html>`_
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
* `Basic development documentation <https://mappedsky.github.io/seizu/dev/dependencies.html>`_

.. toctree::
    :caption: Installation & Configuration
    :hidden:

    install/quickstart
    install/backend
    install/dashboard
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
    install/mcp-skillsets

.. toctree::
    :caption: Development
    :hidden:

    dev/dependencies
    dev/test
    dev/query-validation
    dev/contributing

.. toctree::
    :caption: Get In Touch
    :hidden:

    contact/security
    contact/code-of-conduct
