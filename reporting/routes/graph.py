import logging
from typing import Any

import neo4j.exceptions
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.query import GraphSchemaResponse
from reporting.services import reporting_neo4j

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/v1/graph/schema", response_model=GraphSchemaResponse)
async def get_graph_schema(
    current: CurrentUser = Depends(require_permission(Permission.QUERY_EXECUTE)),
) -> Any:
    """Return node labels, relationship types, property keys, and indexes.

    Runs fixed introspection queries without saving to query history.
    Requires query:execute permission (same as the ad-hoc query console).
    """
    try:
        return GraphSchemaResponse(**await reporting_neo4j.fetch_graph_schema())
    except neo4j.exceptions.Neo4jError:
        logger.exception("Graph schema query failed")
        return JSONResponse(content={"error": "Graph schema query failed"}, status_code=500)
    except Exception:
        logger.exception("Graph schema query failed")
        return JSONResponse(content={"error": "Graph schema query failed"}, status_code=500)
