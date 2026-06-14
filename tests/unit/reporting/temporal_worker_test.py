from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock


async def test_run_worker_starts_and_exits_on_shutdown(mocker):
    """_run_worker connects, starts the worker, waits for shutdown, then exits."""

    @asynccontextmanager
    async def _noop_resources():
        yield

    mocker.patch("reporting.temporal_worker._bootstrap")
    mocker.patch("reporting.temporal_worker.chat_worker_resources", _noop_resources)
    mocker.patch("reporting.temporal_worker.Client.connect", AsyncMock(return_value=MagicMock()))

    mock_worker = AsyncMock()
    mocker.patch("reporting.temporal_worker.Worker", return_value=mock_worker)

    import reporting.temporal_worker as tw

    tw._shutdown_event.set()
    try:
        from reporting.temporal_worker import _run_worker

        await _run_worker()
    finally:
        tw._shutdown_event.clear()

    mock_worker.__aenter__.assert_called_once()


async def test_main_is_noop_when_worker_disabled(mocker):
    mocker.patch("reporting.temporal_worker.settings.TEMPORAL_WORKER_ENABLED", False)
    run = mocker.patch("reporting.temporal_worker._run_worker", AsyncMock())

    from reporting.temporal_worker import main

    main()

    run.assert_not_called()


async def test_main_runs_worker_when_enabled(mocker):
    mocker.patch("reporting.temporal_worker.settings.TEMPORAL_WORKER_ENABLED", True)
    mock_run = mocker.patch("asyncio.run")

    from reporting.temporal_worker import main

    main()

    assert mock_run.called
    # The coroutine passed to asyncio.run should be _run_worker
    coro = mock_run.call_args[0][0]
    assert hasattr(coro, "cr_code")
    assert "_run_worker" in coro.cr_code.co_qualname
    coro.close()  # prevent ResourceWarning


async def test_bootstrap_installs_handlers(mocker):
    installed = mocker.patch(
        "reporting.temporal_worker.install_shutdown_handlers",
    )
    from reporting.temporal_worker import _bootstrap

    _bootstrap()

    installed.assert_called_once()
