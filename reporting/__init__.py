from reporting import setup_logging  # noqa:F401

# Reported to REST clients (the OpenAPI document) and to MCP clients (the
# `serverInfo` of `initialize` and `server/discover`) alike, so the two never
# disagree about which Seizu a caller is talking to.
__version__ = "1.0.0"
