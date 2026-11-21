import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.model_profiles import QueryConsoleModelOptions
from reporting.schema.query import GenerateQueryRequest, GenerateQueryResponse
from reporting.services import model_profiles, report_store
from reporting.services.query_generation import (
    QueryGenerationTimeout,
    QueryGenerationUnavailable,
    generate_query,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/v1/query/generate/options", response_model=QueryConsoleModelOptions)
async def generation_options(
    current: CurrentUser = Depends(require_permission(Permission.QUERY_EXECUTE, Permission.CHAT_USE)),
) -> QueryConsoleModelOptions:
    """The model profiles and reasoning levels the console's Ask mode offers."""
    return await model_profiles.query_console_options()


@router.post("/api/v1/query/generate", response_model=GenerateQueryResponse)
async def generate(
    body: GenerateQueryRequest,
    current: CurrentUser = Depends(require_permission(Permission.QUERY_EXECUTE, Permission.CHAT_USE)),
) -> Any:
    """Generate a read-only Cypher query from a plain-text question.

    One call to the model the profile's query console settings name; no chat session is created. The
    query is validated but not executed. A valid query is saved to the
    caller's query history with the question as its prompt, and its
    ``history_id`` returned so the console can run it by id. An invalid one is
    returned with its errors and not recorded.
    """
    try:
        result = await generate_query(
            body.question,
            current_query=body.current_query,
            model_profile_id=body.model_profile_id,
            reasoning_effort=body.reasoning_effort,
        )
    except QueryGenerationUnavailable as exc:
        # The message names what is wrong with the chosen profile; it carries
        # no provider or model output.
        logger.warning("Query generation unavailable", exc_info=exc)
        return JSONResponse(content={"error": str(exc) or "Query generation is not available"}, status_code=503)
    except QueryGenerationTimeout:
        logger.warning("Query generation timed out")
        return JSONResponse(content={"error": "Query generation timed out"}, status_code=504)
    except Exception:
        logger.exception("Query generation failed")
        return JSONResponse(content={"error": "Query generation failed"}, status_code=502)

    history_id: str | None = None
    if result.valid:
        try:
            item = await report_store.save_query_history(
                user_id=current.user.user_id,
                query=result.query,
                prompt=body.question.strip(),
            )
            history_id = item.history_id
        except Exception:
            logger.warning("Failed to save generated query to history", exc_info=True)
    logger.info(
        "Generated query",
        extra={"user": current.user.user_id, "valid": result.valid, "history_id": history_id},
    )
    return GenerateQueryResponse(
        query=result.query,
        explanation=result.explanation,
        errors=result.errors,
        warnings=result.warnings,
        history_id=history_id,
        model_id=result.model_id,
        reasoning_effort=result.reasoning_effort,
        profile_name=result.profile_name,
    )
