"""Atomic persistence for bounded groups of external input requests."""

from datetime import UTC, datetime

from sqlalchemy import delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from reporting.schema.chat_elicitations import ChatElicitation, ElicitationResponse, validate_response
from reporting.services.report_store.sql import ChatElicitationRecord as Record
from reporting.services.report_store.sql import ChatSessionRecord, _get_engine


def public(record: Record) -> ChatElicitation:
    item = ChatElicitation.model_validate_json(record.public_json)
    status = record.status
    if status != "consumed" and record.expires_at <= datetime.now(UTC).isoformat():
        status = "expired"
    return ChatElicitation.model_validate({**item.model_dump(), "status": status})


async def create(records: list[Record]) -> None:
    first = records[0]
    async with AsyncSession(_get_engine()) as session:
        # Serialize all writers for one owner's thread, including remote steps.
        owner = (
            await session.execute(
                select(ChatSessionRecord)
                .where(
                    col(ChatSessionRecord.user_id) == first.user_id,
                    col(ChatSessionRecord.thread_id) == first.thread_id,
                    col(ChatSessionRecord.origin) == "interactive",
                    col(ChatSessionRecord.retiring_at).is_(None),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if owner is None:
            raise ValueError("Interactive session unavailable")
        scope = (
            select(func.count())
            .select_from(Record)
            .where(
                col(Record.user_id) == first.user_id,
                col(Record.thread_id) == first.thread_id,
                col(Record.expires_at) > datetime.now(UTC).isoformat(),
            )
        )
        thread_count = (await session.execute(scope)).scalar_one()
        turn_count = (await session.execute(scope.where(col(Record.turn_id) == first.turn_id))).scalar_one()
        if thread_count + len(records) > 64 or turn_count + len(records) > 16:
            raise ValueError("Elicitation limit reached")
        session.add_all(records)
        await session.commit()


async def purge_expired(limit: int = 500) -> None:
    async with AsyncSession(_get_engine()) as session:
        ids = (
            select(Record.elicitation_id)
            .where(
                col(Record.expires_at) <= datetime.now(UTC).isoformat(),
            )
            .order_by(col(Record.expires_at))
            .limit(limit)
        )
        await session.execute(delete(Record).where(col(Record.elicitation_id).in_(ids)))
        await session.commit()


async def get(elicitation_id: str, user_id: str) -> Record | None:
    async with AsyncSession(_get_engine()) as session:
        return (
            await session.execute(
                select(Record).where(
                    col(Record.elicitation_id) == elicitation_id,
                    col(Record.user_id) == user_id,
                )
            )
        ).scalar_one_or_none()


async def bind_confirmation(record: Record, confirmation_id: str) -> None:
    async with AsyncSession(_get_engine()) as session:
        await session.execute(
            update(Record)
            .where(
                col(Record.user_id) == record.user_id,
                col(Record.group_id) == record.group_id,
                col(Record.status) != "consumed",
            )
            .values(confirmation_id=confirmation_id)
        )
        await session.commit()


async def for_confirmation(confirmation_id: str, user_id: str, thread_id: str | None) -> Record | None:
    async with AsyncSession(_get_engine()) as session:
        return (
            await session.execute(
                select(Record)
                .where(
                    col(Record.user_id) == user_id,
                    col(Record.thread_id) == thread_id,
                    col(Record.confirmation_id) == confirmation_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()


async def matching_delegation(user_id: str, thread_id: str, arguments_hash: str) -> list[Record]:
    async with AsyncSession(_get_engine()) as session:
        return list(
            (
                await session.execute(
                    select(Record)
                    .where(
                        col(Record.user_id) == user_id,
                        col(Record.thread_id) == thread_id,
                        col(Record.arguments_hash) == arguments_hash,
                        col(Record.status).in_(["accepted", "declined", "cancelled"]),
                        col(Record.expires_at) > datetime.now(UTC).isoformat(),
                    )
                    .order_by(col(Record.created_at).desc())
                    .limit(64)
                )
            )
            .scalars()
            .all()
        )


async def list_for_thread(user_id: str, thread_id: str) -> list[ChatElicitation]:
    async with AsyncSession(_get_engine()) as session:
        rows = (
            (
                await session.execute(
                    select(Record)
                    .where(
                        col(Record.user_id) == user_id,
                        col(Record.thread_id) == thread_id,
                    )
                    .order_by(col(Record.created_at).desc())
                    .limit(64)
                )
            )
            .scalars()
            .all()
        )
        return [public(row) for row in rows]


async def respond(record: Record, response: ElicitationResponse) -> bool:
    validate_response(public(record).requested_schema, response)
    async with AsyncSession(_get_engine()) as session:
        result = await session.execute(
            update(Record)
            .where(
                col(Record.elicitation_id) == record.elicitation_id,
                col(Record.user_id) == record.user_id,
                col(Record.status) == "pending",
                col(Record.expires_at) > datetime.now(UTC).isoformat(),
            )
            .values(
                status={"accept": "accepted", "decline": "declined", "cancel": "cancelled"}[response.action],
                response_json=response.model_dump_json(),
                decided_at=datetime.now(UTC).isoformat(),
            )
            .returning(Record.elicitation_id)
        )
        changed = result.scalar_one_or_none() is not None
        await session.commit()
        return changed


async def group(record: Record, *, claim: bool = False) -> list[Record]:
    async with AsyncSession(_get_engine()) as session:
        rows = list(
            (
                await session.execute(
                    select(Record)
                    .where(
                        col(Record.user_id) == record.user_id,
                        col(Record.group_id) == record.group_id,
                    )
                    .order_by(col(Record.elicitation_id))
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        if claim:
            if not rows or any(public(row).status not in {"accepted", "declined", "cancelled"} for row in rows):
                return []
            copies = [Record.model_validate(row.model_dump()) for row in rows]
            claimed = (
                (
                    await session.execute(
                        update(Record)
                        .where(
                            col(Record.user_id) == record.user_id,
                            col(Record.group_id) == record.group_id,
                            col(Record.status).in_(["accepted", "declined", "cancelled"]),
                            col(Record.expires_at) > datetime.now(UTC).isoformat(),
                        )
                        .values(status="consumed", response_json=None)
                        .returning(Record.elicitation_id)
                    )
                )
                .scalars()
                .all()
            )
            if len(claimed) != len(rows):
                await session.rollback()
                return []
            await session.commit()
            return copies
        return rows
