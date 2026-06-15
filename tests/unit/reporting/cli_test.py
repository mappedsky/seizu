"""Tests for reporting.cli (uvicorn entrypoint)."""


def test_main_calls_uvicorn_run(mocker):
    mock_run = mocker.patch("reporting.cli.uvicorn.run")

    from reporting.cli import main

    main()

    mock_run.assert_called_once_with(
        "reporting.asgi:application",
        host=mocker.ANY,
        port=mocker.ANY,
        factory=False,
    )
