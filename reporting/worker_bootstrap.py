"""Shared lifecycle helpers for long-running Seizu worker processes."""

import asyncio
import logging
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import FrameType

from reporting import settings
from reporting.services import report_store


def install_shutdown_handlers(event: asyncio.Event, logger: logging.Logger) -> None:
    """Set ``event`` when the process receives a normal shutdown signal."""

    def finalizer(sig: int, frame: FrameType | None) -> None:
        logger.info("Shutdown signal caught", extra={"signal": sig})
        event.set()

    signal.signal(signal.SIGTERM, finalizer)
    signal.signal(signal.SIGINT, finalizer)


async def initialize_report_store() -> None:
    """Initialize the configured store when the backend requires setup."""
    if settings.DYNAMODB_CREATE_TABLE or settings.REPORT_STORE_BACKEND == "sqlmodel":
        await report_store.initialize()


@asynccontextmanager
async def chat_worker_resources() -> AsyncIterator[None]:
    """Own report-store and chat-checkpointer lifecycle for headless workers."""
    from reporting.services.chat_graph import close_chat_checkpoints, initialize_chat_checkpoints

    await initialize_report_store()
    await initialize_chat_checkpoints()
    try:
        yield
    finally:
        await close_chat_checkpoints()
