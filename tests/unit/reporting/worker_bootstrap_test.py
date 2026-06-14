import asyncio
import logging
import signal
from unittest.mock import AsyncMock

import pytest

from reporting.worker_bootstrap import (
    chat_worker_resources,
    initialize_report_store,
    install_shutdown_handlers,
)


def test_install_shutdown_handlers_sets_event_on_sigterm():
    event = asyncio.Event()
    logger = logging.getLogger(__name__)
    install_shutdown_handlers(event, logger)

    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

    assert event.is_set()


def test_install_shutdown_handlers_sets_event_on_sigint():
    event = asyncio.Event()
    logger = logging.getLogger(__name__)
    install_shutdown_handlers(event, logger)

    signal.getsignal(signal.SIGINT)(signal.SIGINT, None)

    assert event.is_set()


async def test_initialize_report_store_skips_when_not_needed(mocker):
    mocker.patch("reporting.worker_bootstrap.settings.DYNAMODB_CREATE_TABLE", False)
    mocker.patch("reporting.worker_bootstrap.settings.REPORT_STORE_BACKEND", "dynamodb")
    init = mocker.patch("reporting.worker_bootstrap.report_store.initialize", AsyncMock())

    await initialize_report_store()

    init.assert_not_awaited()


async def test_initialize_report_store_runs_when_dynamodb_create_table(mocker):
    mocker.patch("reporting.worker_bootstrap.settings.DYNAMODB_CREATE_TABLE", True)
    init = mocker.patch("reporting.worker_bootstrap.report_store.initialize", AsyncMock())

    await initialize_report_store()

    init.assert_awaited_once()


async def test_chat_worker_resources_initializes_and_closes(mocker):
    mocker.patch("reporting.worker_bootstrap.initialize_report_store", AsyncMock())
    init_chat = mocker.patch(
        "reporting.services.chat_graph.initialize_chat_checkpoints",
        AsyncMock(),
    )
    close_chat = mocker.patch(
        "reporting.services.chat_graph.close_chat_checkpoints",
        AsyncMock(),
    )

    async with chat_worker_resources():
        pass

    init_chat.assert_awaited_once()
    close_chat.assert_awaited_once()


async def test_chat_worker_resources_closes_on_exception(mocker):
    mocker.patch("reporting.worker_bootstrap.initialize_report_store", AsyncMock())
    mocker.patch("reporting.services.chat_graph.initialize_chat_checkpoints", AsyncMock())
    close_chat = mocker.patch(
        "reporting.services.chat_graph.close_chat_checkpoints",
        AsyncMock(),
    )

    with pytest.raises(RuntimeError):
        async with chat_worker_resources():
            raise RuntimeError("boom")

    close_chat.assert_awaited_once()
