Seizu (星図)
============

What is Seizu?
--------------

`Seizu (星図) <https://mappedsky.com/seizu/>`_ is a react/mui frontend and python backend for various forms of reporting of Neo4j graph data.
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

Seizu has a `quickstart guide <https://mappedsky.com/seizu/install/quickstart.html>`_, which can be used for evaluation, or development.

Documentation
-------------

* `Installation documentation <https://mappedsky.com/seizu/install/backend.html>`_
* `Kubernetes (Helm) installation <https://mappedsky.com/seizu/install/helm.html>`_
* `Upgrade guide <https://mappedsky.com/seizu/install/upgrading.html>`_
* `Dashboard configuration <https://mappedsky.com/seizu/install/dashboard.html>`_
* `Spaces documentation <https://mappedsky.com/seizu/install/spaces.html>`_
* `CLI documentation <https://mappedsky.com/seizu/install/cli.html>`_
* `Security guidance <https://mappedsky.com/seizu/install/security.html>`_
* `Query Console <https://mappedsky.com/seizu/install/query-console.html>`_
* `Chat assistant documentation <https://mappedsky.com/seizu/install/chat.html>`_
* `Workflow documentation <https://mappedsky.com/seizu/install/workflows.html>`_
* `Scheduled chat documentation <https://mappedsky.com/seizu/install/chat-schedules.html>`_
* `Built-in workflow documentation <https://mappedsky.com/seizu/install/built-in-workflows.html>`_
* `Scheduled cartography sync documentation <https://mappedsky.com/seizu/install/cartography-sync.html>`_
* `Sandbox delegation documentation <https://mappedsky.com/seizu/install/sandbox.html>`_
* `MCP Toolsets documentation <https://mappedsky.com/seizu/install/mcp-toolsets.html>`_
* `MCP Skillsets documentation <https://mappedsky.com/seizu/install/mcp-skillsets.html>`_
* `External MCP proxy documentation <https://mappedsky.com/seizu/install/external-mcp.html>`_
* `Basic development documentation <https://mappedsky.com/seizu/dev/dependencies.html>`_
* `Decision log <https://mappedsky.com/seizu/dev/decisions/index.html>`_ — why the code is the way it is, per product area

.. toctree::
    :caption: Installation & Configuration
    :hidden:

    install/quickstart
    install/backend
    install/helm
    install/upgrading
    install/dashboard
    install/spaces
    install/cli
    install/security
    install/query-console
    install/chat
    install/workflows
    install/chat-schedules
    install/built-in-workflows
    install/cve-remediation
    install/cartography-sync
    install/sandbox
    install/mcp-toolsets
    install/agent-plugins
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
