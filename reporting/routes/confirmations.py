from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query

from reporting.authnz import CurrentUser, get_current_user
from reporting.schema.chat import CHAT_THREAD_ID_PATTERN
from reporting.schema.confirmations import (
    ActionConfirmationPublic,
    ConfirmationDecisionRequest,
    ConfirmationListResponse,
    ConfirmationResponse,
)
from reporting.services import action_confirmations, report_store

router = APIRouter()

_CONFIRMATION_ID_PATTERN = r"^[0-9a-f]{32}$"


@router.get("/api/v1/confirmations", response_model=ConfirmationListResponse)
async def list_confirmations(
    thread_id: str = Query(min_length=1, max_length=32, pattern=CHAT_THREAD_ID_PATTERN),
    current: CurrentUser = Depends(get_current_user),
) -> ConfirmationListResponse:
    confirmations = await report_store.list_action_confirmations(
        user_id=current.user.user_id,
        source="chat",
        session_key=thread_id,
        status="pending",
    )
    return ConfirmationListResponse(
        confirmations=[
            ActionConfirmationPublic.from_confirmation(confirmation)
            for confirmation in confirmations
            if not action_confirmations.is_expired(confirmation)
        ]
    )


@router.get("/api/v1/confirmations/batch/{batch_id}", response_model=ConfirmationListResponse)
async def list_batch_confirmations(
    batch_id: str = Path(min_length=32, max_length=32, pattern=_CONFIRMATION_ID_PATTERN),
    current: CurrentUser = Depends(get_current_user),
) -> ConfirmationListResponse:
    batch = await report_store.list_batch_action_confirmations(
        user_id=current.user.user_id,
        batch_id=batch_id,
    )
    return ConfirmationListResponse(
        confirmations=[
            ActionConfirmationPublic.from_confirmation(c)
            for c in batch
            if c.status != "pending" or not action_confirmations.is_expired(c)
        ]
    )


@router.get("/api/v1/confirmations/{confirmation_id}", response_model=ConfirmationResponse)
async def get_confirmation(
    confirmation_id: str = Path(min_length=32, max_length=32, pattern=_CONFIRMATION_ID_PATTERN),
    current: CurrentUser = Depends(get_current_user),
) -> ConfirmationResponse:
    confirmation = await report_store.get_action_confirmation(confirmation_id, user_id=current.user.user_id)
    if confirmation is None:
        raise HTTPException(status_code=404, detail="Confirmation not found")
    return ConfirmationResponse(confirmation=ActionConfirmationPublic.from_confirmation(confirmation))


@router.post("/api/v1/confirmations/{confirmation_id}/decision", response_model=ConfirmationResponse)
async def decide_confirmation(
    confirmation_id: str = Path(min_length=32, max_length=32, pattern=_CONFIRMATION_ID_PATTERN),
    body: ConfirmationDecisionRequest = Body(...),
    current: CurrentUser = Depends(get_current_user),
) -> ConfirmationResponse:
    confirmation = await action_confirmations.decide_confirmation(
        confirmation_id=confirmation_id,
        user_id=current.user.user_id,
        decision=body.decision,
    )
    if confirmation is None:
        raise HTTPException(status_code=404, detail="Confirmation not found")
    return ConfirmationResponse(confirmation=ActionConfirmationPublic.from_confirmation(confirmation))
