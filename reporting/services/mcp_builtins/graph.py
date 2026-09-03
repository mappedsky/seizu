"""Built-in ``graph__*`` tools — schema discovery and ad-hoc Cypher."""

from typing import Any

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.routes.query import _serialize_neo4j_value
from reporting.services import reporting_neo4j
from reporting.services.mcp_builtins.base import BuiltinGroup, BuiltinTool
from reporting.services.query_validator import validate_query
from reporting.services.result_limits import current_result_limits, stream_truncation

GROUP = "graph"


def _unindexed_scans(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Return non-index scan operators from a serialized Neo4j plan."""
    found: list[dict[str, Any]] = []

    def visit(operator: Any) -> None:
        if not isinstance(operator, dict):
            return
        raw_type = str(operator.get("operatorType") or "")
        operator_type = raw_type.split("@", 1)[0]
        # NodeIndexScan and its partitioned variants are index-backed. Other
        # *Scan operators enumerate nodes or relationships before filtering.
        if operator_type.endswith("Scan") and "Index" not in operator_type:
            raw_args = operator.get("args")
            args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
            found.append(
                {
                    "operator_type": operator_type,
                    "details": args.get("Details"),
                    "estimated_rows": args.get("EstimatedRows"),
                }
            )
        for child in operator.get("children") or []:
            visit(child)

    visit(plan)
    return found


def _max_estimated_rows(plan: dict[str, Any]) -> float:
    """Return the largest cardinality estimate in a serialized Neo4j plan."""
    maximum = 0.0

    def visit(operator: Any) -> None:
        nonlocal maximum
        if not isinstance(operator, dict):
            return
        raw_args = operator.get("args")
        args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
        try:
            maximum = max(maximum, float(args.get("EstimatedRows") or 0))
        except (TypeError, ValueError):
            pass
        for child in operator.get("children") or []:
            visit(child)

    visit(plan)
    return maximum


async def _handle_schema(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    return await reporting_neo4j.fetch_graph_schema()


async def _handle_query(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    cypher = str(args.get("query", "")).strip()
    if not cypher:
        return {"error": "query parameter is required"}
    validation = await validate_query(cypher)
    if validation.has_errors:
        return {"errors": validation.errors, "warnings": validation.warnings}
    unindexed_scans = _unindexed_scans(validation.plan)
    max_estimated_rows = _max_estimated_rows(validation.plan)
    unindexed_plan_too_large = bool(unindexed_scans) and (
        settings.MCP_GRAPH_QUERY_UNINDEXED_MAX_ESTIMATED_ROWS <= 0
        or max_estimated_rows > settings.MCP_GRAPH_QUERY_UNINDEXED_MAX_ESTIMATED_ROWS
    )
    if settings.MCP_GRAPH_QUERY_REJECT_UNINDEXED and (validation.performance_warnings or unindexed_plan_too_large):
        return {
            "error": "Query rejected because its execution plan is risky",
            "code": "query_plan_rejected",
            "unindexed_operators": unindexed_scans,
            "max_estimated_rows": max_estimated_rows,
            "performance_warnings": validation.performance_warnings,
            "plan": validation.plan,
            "warnings": validation.warnings,
        }
    # Stream and serialize under the caller's bounds rather than fetching
    # everything and trimming after. An unbounded MATCH is fast to issue and can
    # materialize the graph in worker memory before any limit is consulted.
    limits = current_result_limits()
    serialized, stopped_by = await reporting_neo4j.run_query_streamed(
        cypher,
        None,
        max_rows=limits.max_rows,
        max_bytes=limits.max_bytes,
        serialize=lambda record: {key: _serialize_neo4j_value(value) for key, value in record.items()},
    )
    payload: dict[str, Any] = {"results": serialized, "warnings": validation.warnings}
    if stopped_by:
        # The same shape every other truncation reports, so a later byte-bound
        # pass has a lower bound to carry rather than a length it might mistake
        # for a total -- and so clients see one contract, not three.
        payload |= stream_truncation(stopped_by, serialized, limits).fields()
    return payload


async def _handle_validate_query(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    cypher = str(args.get("query", "")).strip()
    if not cypher:
        return {"error": "query parameter is required"}
    validation = await validate_query(cypher)
    return {
        "valid": not validation.has_errors,
        "errors": validation.errors,
        "warnings": validation.warnings,
    }


async def _handle_explain(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    cypher = str(args.get("query", "")).strip()
    if not cypher:
        return {"error": "query parameter is required"}
    # Validation already ran EXPLAIN and retained its result, so returning that
    # plan applies the graph__query guards without planning the query twice.
    validation = await validate_query(cypher)
    if validation.has_errors:
        return {"errors": validation.errors, "warnings": validation.warnings}
    return {"plan": validation.plan, "warnings": validation.warnings}


GROUP_DEF = BuiltinGroup(
    name=GROUP,
    tools=[
        BuiltinTool(
            name="graph__schema",
            group=GROUP,
            description=(
                "Returns the available node labels, relationship types, property keys, and indexes "
                "(name, type, entity type, labels/types, properties, state) in the Neo4j graph database. "
                "Use the indexes to write queries that match on indexed labels/properties instead of "
                "scanning the whole graph."
            ),
            input_schema={"type": "object", "properties": {}},
            required_permissions=[Permission.QUERY_EXECUTE.value],
            handler=_handle_schema,
        ),
        BuiltinTool(
            name="graph__query",
            group=GROUP,
            description=(
                "Execute an ad-hoc read-only Cypher query against the Neo4j "
                "graph database. The query is validated and planned before execution — "
                "write operations are not permitted, and risky unindexed plans are rejected by default. "
                "A rejected unindexed query returns its EXPLAIN plan so it can be rewritten."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A read-only Cypher query to execute.",
                    }
                },
                "required": ["query"],
            },
            required_permissions=[Permission.QUERY_EXECUTE.value],
            handler=_handle_query,
            collection_key="results",
        ),
        BuiltinTool(
            name="graph__validate_query",
            group=GROUP,
            description=(
                "Validate a read-only Cypher query without executing it. Returns "
                "{valid, errors, warnings}: errors block (write operations, disallowed "
                "procedures, syntax), warnings do not. Use this to check a query before "
                "saving it as a toolset tool — toolset tools reject write/invalid Cypher, "
                "so validating first lets you fix issues before the (mutating) create call."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A read-only Cypher query to validate.",
                    }
                },
                "required": ["query"],
            },
            required_permissions=[Permission.QUERY_EXECUTE.value],
            handler=_handle_validate_query,
        ),
        BuiltinTool(
            name="graph__explain",
            group=GROUP,
            description=(
                "Return Neo4j's execution plan for a read-only Cypher query without running it. "
                "The query is validated with the same EXPLAIN used by graph__query (writes and disallowed "
                "procedures are rejected), and the retained planner result is returned as "
                "{plan, warnings}. Use the plan (operator types, estimated rows, index usage) to check "
                "that a query hits indexes and avoids full scans before saving or running it."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A read-only Cypher query to plan with EXPLAIN.",
                    }
                },
                "required": ["query"],
            },
            required_permissions=[Permission.QUERY_EXECUTE.value],
            handler=_handle_explain,
        ),
    ],
)
