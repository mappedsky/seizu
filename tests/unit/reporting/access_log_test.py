"""Access logging has to work in the image as shipped.

Gunicorn's own access log is dead under an ASGI worker: it never parses a
request, so ``--access-logfile`` and ``access_log_format`` produce nothing and
uvicorn owns the access line instead. The handoff between the two is where it
was lost -- every HTTP request went unlogged, with no error anywhere to say so.

Three steps conspired. Gunicorn installs a stdout handler on ``gunicorn.access``
from ``--access-logfile=-``, then applies ``logging.conf`` as ``logconfig_dict``;
``dictConfig`` removes existing handlers from any logger it configures, and
``logging.conf`` declared ``gunicorn.access`` with no ``handlers`` of its own,
relying on ``propagate: True`` to reach root's stdout handler. The worker then
copies that logger's (now empty) handler list onto ``uvicorn.access`` and sets
``propagate = False`` on the copy, destroying the propagation the config
depended on. Uvicorn gates access logging on ``uvicorn.access.hasHandlers()``,
which stops at ``propagate = False`` and so returns False -- switching access
logging off for the life of the process.

Access logging is now the application's job, in three parts, and this module
covers all of them:

* ``reporting.access_log.AccessLogMiddleware`` writes the stdout line, with the
  fields uvicorn's own line does not carry.
* ``asgi_correlation_id`` owns the request id, and ``logging.conf`` attaches its
  filter to every handler so the id lands on *every* record of a request.
* ``telemetry.instrument_fastapi`` emits the per-request span.

The worker handoff is still covered, because ``logging.conf`` still wires
``gunicorn.access`` up properly on purpose: it is the fallback that brings
uvicorn's access log back if the middleware is ever removed, instead of access
logging vanishing silently the way it did before.
"""

import io
import json
import logging
import os
import pathlib
import sys
from typing import Any, NamedTuple

import pytest
import yaml
from asgi_correlation_id import CorrelationIdFilter, CorrelationIdMiddleware
from gunicorn import glogging
from gunicorn.config import Config
from uvicorn_worker import UvicornWorker

from reporting.access_log import (
    ACCESS_LOGGER_NAME,
    AccessLogMiddleware,
    silence_uvicorn_access_log,
)
from reporting.services import telemetry

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_LOGGING_CONF = _REPO_ROOT / "logging.conf"
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_DEV_ENTRYPOINT = _REPO_ROOT / "scripts" / "dev_entrypoint.sh"

# `uvicorn.workers` is deprecated upstream in favour of the `uvicorn-worker`
# distribution, and warns on import -- which this suite turns into an error.
_WORKER_CLASS = "uvicorn_worker.UvicornWorker"
_DEPRECATED_WORKER_MODULE = "uvicorn.workers"

# Every logger the handoff writes to. The whole suite shares one process and
# `dictConfig` rewrites these globally, so they are saved and restored.
_TOUCHED_LOGGERS = (
    "",
    "gunicorn.error",
    "gunicorn.access",
    "uvicorn.error",
    "uvicorn.access",
)


def _shipped_logging_config() -> dict:
    return yaml.safe_load(os.path.expandvars(_LOGGING_CONF.read_text()))


# --------------------------------------------------------------------------
# The gunicorn -> uvicorn logging handoff, against the shipped config
# --------------------------------------------------------------------------


class _Streams(NamedTuple):
    stdout: io.StringIO
    stderr: io.StringIO


@pytest.fixture
def production_streams(tmp_path, monkeypatch):
    """Configure logging the way the container does, and yield its streams.

    The two steps run in the order the image performs them: gunicorn reads
    ``logging.conf`` into ``logconfig_dict`` and calls ``dictConfig`` (having
    first installed the ``--access-logfile`` handler that ``dictConfig`` then
    removes), and then the ASGI worker is constructed, rewiring uvicorn's
    loggers from gunicorn's.

    ``sys.stdout`` and ``sys.stderr`` are replaced before any of it so that the
    ``ext://`` handlers in ``logging.conf`` resolve to streams this test can
    read, rather than depending on how pytest happens to be capturing output.
    """
    saved = {
        name: (
            logging.getLogger(name).handlers[:],
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
        )
        for name in _TOUCHED_LOGGERS
    }

    streams = _Streams(io.StringIO(), io.StringIO())
    monkeypatch.setattr(sys, "stdout", streams.stdout)
    monkeypatch.setattr(sys, "stderr", streams.stderr)

    cfg = Config()
    # What the Dockerfile CMD passes.
    cfg.set("accesslog", "-")
    cfg.set("errorlog", "-")
    cfg.set("logconfig_dict", _shipped_logging_config())
    # Keeps the worker's heartbeat file out of the real /run/seizu.
    cfg.set("worker_tmp_dir", str(tmp_path))

    worker = None
    try:
        gunicorn_logger = glogging.Logger(cfg)
        worker = UvicornWorker(0, os.getpid(), [], None, 60.0, cfg, gunicorn_logger)
        yield streams
    finally:
        # Constructing a worker opens an unlinked heartbeat file it would
        # normally hold for its whole life. Left to the garbage collector it
        # raises ResourceWarning, which this suite treats as an error.
        if worker is not None:
            worker.tmp.close()
        for name, (handlers, level, propagate) in saved.items():
            logger = logging.getLogger(name)
            logger.handlers[:] = handlers
            logger.level = level
            logger.propagate = propagate


@pytest.mark.parametrize("logger_name", ["uvicorn.access", "uvicorn.error"])
def test_uvicorn_loggers_have_handlers(production_streams, logger_name):
    """``hasHandlers()`` is uvicorn's own gate on emitting an access line.

    The application silences ``uvicorn.access`` at startup, because the
    middleware is the access log now. This asserts the state it is silenced
    *from*: if the middleware goes away, uvicorn's access log has to come back
    rather than leaving the deployment with no access logging at all.
    """
    assert logging.getLogger(logger_name).hasHandlers(), (
        f"{logger_name} has no reachable handler, so uvicorn will not log; "
        "the gunicorn.* logger it is copied from needs explicit handlers"
    )


def test_access_line_reaches_stdout_as_json(production_streams):
    """End to end: a record on the access logger lands on stdout, formatted."""
    logging.getLogger(ACCESS_LOGGER_NAME).info('127.0.0.1:0 - "GET /api/v1/healthcheck HTTP/1.1" 200')

    written = production_streams.stdout.getvalue().strip()
    assert written, "no access line reached stdout"

    record = json.loads(written.splitlines()[-1])
    assert record["name"] == ACCESS_LOGGER_NAME
    assert "/api/v1/healthcheck" in record["message"]
    assert record["correlation_id"] == "-", "outside a request there is no id to carry"


def test_error_line_is_logged_once(production_streams):
    """``gunicorn.error`` names a handler, so it must not also propagate.

    With propagation left on it reaches its own console_stderr *and* root's
    console_stdout, and every startup and error line appears twice in the pod
    logs. Gunicorn sets propagate=False on this logger before applying the
    file, so the duplication only happens if the file switches it back on.
    """
    logging.getLogger("gunicorn.error").info("Booting worker with pid: 1")

    stderr_lines = production_streams.stderr.getvalue().strip().splitlines()
    assert len(stderr_lines) == 1, "gunicorn.error line was duplicated on stderr"
    assert json.loads(stderr_lines[0])["name"] == "gunicorn.error"
    assert not production_streams.stdout.getvalue(), (
        "gunicorn.error propagated to root's stdout handler as well as its own "
        "stderr handler, so every line is emitted twice"
    )


# --------------------------------------------------------------------------
# The shipped configuration files
# --------------------------------------------------------------------------


def test_shipped_logging_conf_gives_access_logger_explicit_handlers():
    """Guards the cause at the file, so a regression names it directly."""
    access = _shipped_logging_config()["loggers"]["gunicorn.access"]

    assert access.get("handlers"), (
        "gunicorn.access must list handlers explicitly: the ASGI worker copies "
        "them onto uvicorn.access and disables propagation on that copy, so a "
        "logger that only reaches root by propagation emits nothing"
    )


def test_shipped_logging_conf_filters_every_handler_for_the_request_id():
    """The id is only on every record if the filter is on every handler."""
    config = _shipped_logging_config()

    assert "correlation_id" in config["filters"]
    for name, handler in config["handlers"].items():
        assert "correlation_id" in handler.get("filters", []), (
            f"handler {name} does not apply the correlation_id filter, so records reaching it carry no request id"
        )
    assert "%(correlation_id)s" in config["formatters"]["json"]["format"], (
        "the filter sets record.correlation_id but the formatter never emits it"
    )


@pytest.mark.parametrize("path", [_DOCKERFILE, _DEV_ENTRYPOINT], ids=lambda p: p.name)
def test_entrypoints_use_the_maintained_worker_class(path):
    """Both launchers must agree, and on the non-deprecated distribution.

    The dev entrypoint drifting from the Dockerfile would mean development and
    production differ in exactly the layer this module covers.
    """
    text = path.read_text()

    assert _WORKER_CLASS in text, f"{path.name} does not use {_WORKER_CLASS}"
    assert _DEPRECATED_WORKER_MODULE not in text, (
        f"{path.name} still references {_DEPRECATED_WORKER_MODULE}, which is deprecated upstream and warns on import"
    )


# --------------------------------------------------------------------------
# The middleware
# --------------------------------------------------------------------------

_BODY_CHUNKS = (b"hello", b" world")


def _http_scope(**overrides: Any) -> dict[str, Any]:
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/reports",
        "query_string": b"limit=10",
        "http_version": "1.1",
        "client": ("10.0.0.7", 54321),
        "headers": [
            (b"content-length", b"91"),
            (b"x-forwarded-for", b"203.0.113.9"),
            (b"user-agent", b"seizu-cli/1.2"),
            (b"accept", b"application/json"),
        ],
    }
    scope.update(overrides)
    return scope


async def _receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _discard(message: dict[str, Any]) -> None:
    return None


def _responder(status: int = 200, chunks: tuple[bytes, ...] = _BODY_CHUNKS) -> Any:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": status, "headers": []})
        for index, chunk in enumerate(chunks):
            await send(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": index < len(chunks) - 1,
                }
            )

    return app


@pytest.fixture
def access_records():
    """Collect access records, filtered the way ``logging.conf`` filters them.

    A handler on the logger itself rather than ``caplog``, which reads through
    root: ``logging.conf`` is applied when ``reporting`` is imported, and it
    gives this logger ``propagate: False``.
    """
    logger = logging.getLogger(ACCESS_LOGGER_NAME)
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture(level=logging.INFO)
    handler.addFilter(CorrelationIdFilter(uuid_length=32, default_value="-"))
    saved_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(saved_level)


async def test_middleware_records_the_full_field_set(access_records):
    """Every field the dead gunicorn ``access_log_format`` used to name."""
    await AccessLogMiddleware(_responder())(_http_scope(), _receive, _discard)

    assert len(access_records) == 1
    record = access_records[0]
    assert record.status == 200
    assert record.bytes_sent == len(b"".join(_BODY_CHUNKS))
    assert record.method == "GET"
    assert record.route == "/api/v1/reports"
    assert record.target == "/api/v1/reports?limit=10"
    assert record.http_version == "1.1"
    assert record.client_addr == "10.0.0.7:54321"
    assert record.content_length == "91"
    assert record.forwarded_for == "203.0.113.9"
    assert record.user_agent == "seizu-cli/1.2"
    assert record.accept == "application/json"
    assert record.duration_us >= 0
    # The message stays readable for anyone tailing the pod logs.
    assert '"GET /api/v1/reports?limit=10 HTTP/1.1" 200' in record.getMessage()


@pytest.mark.parametrize("scope_type", ["lifespan", "websocket"])
async def test_middleware_passes_non_http_scopes_through(access_records, scope_type):
    seen = False

    async def app(scope: Any, receive: Any, send: Any) -> None:
        nonlocal seen
        seen = True

    await AccessLogMiddleware(app)({"type": scope_type}, _receive, _discard)

    assert seen, "the wrapped app was not reached"
    assert access_records == [], f"{scope_type} is not an HTTP request and has no access line"


async def test_middleware_logs_a_failed_request_as_500_and_reraises(access_records):
    """The server turns the exception into a 500 this middleware never sees."""

    async def broken(scope: Any, receive: Any, send: Any) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await AccessLogMiddleware(broken)(_http_scope(), _receive, _discard)

    assert len(access_records) == 1
    assert access_records[0].status == 500


async def test_middleware_keeps_a_status_already_sent_when_a_stream_fails(access_records):
    """A turn stream that dies part-way sent its 200 long before it failed."""

    async def failing_stream(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"partial", "more_body": True})
        raise RuntimeError("upstream went away")

    with pytest.raises(RuntimeError):
        await AccessLogMiddleware(failing_stream)(_http_scope(), _receive, _discard)

    record = access_records[0]
    assert record.status == 200, "a response that started is not a 500"
    assert record.bytes_sent == len(b"partial")


# --------------------------------------------------------------------------
# Request ids, end to end through the correlation middleware
# --------------------------------------------------------------------------

_INBOUND_ID = "0123456789abcdef0123456789abcdef"


async def test_access_record_carries_an_inbound_request_id(access_records):
    scope = _http_scope(headers=[(b"x-request-id", _INBOUND_ID.encode())])

    await CorrelationIdMiddleware(AccessLogMiddleware(_responder()))(scope, _receive, _discard)

    assert access_records[0].correlation_id == _INBOUND_ID


async def test_access_record_carries_a_generated_request_id(access_records):
    """No inbound header: the middleware still has to produce a full id.

    This is the ordering the app depends on. Reversed -- access logging outside
    the correlation middleware -- the id would not be set in the context yet and
    every access line would fall back to the filter's default.
    """
    await CorrelationIdMiddleware(AccessLogMiddleware(_responder()))(_http_scope(), _receive, _discard)

    correlation_id = access_records[0].correlation_id
    assert correlation_id != "-", "the access record fell back to the filter default"
    assert len(correlation_id) == 32


async def test_response_echoes_the_request_id():
    sent: list[dict[str, Any]] = []

    async def capture(message: dict[str, Any]) -> None:
        sent.append(message)

    await CorrelationIdMiddleware(AccessLogMiddleware(_responder()))(_http_scope(), _receive, capture)

    start = next(message for message in sent if message["type"] == "http.response.start")
    headers = {key.lower(): value for key, value in start["headers"]}
    assert b"x-request-id" in headers, "a client cannot correlate without the id on the response"


def test_silence_uvicorn_access_log_leaves_it_unable_to_emit():
    logger = logging.getLogger("uvicorn.access")
    saved = (logger.handlers[:], logger.propagate)
    try:
        logger.handlers = [logging.NullHandler()]
        logger.propagate = True

        silence_uvicorn_access_log()

        assert not logger.hasHandlers(), "uvicorn would still log its own, smaller access line"
    finally:
        logger.handlers, logger.propagate = saved


# --------------------------------------------------------------------------
# The application's wiring
# --------------------------------------------------------------------------


def test_app_wraps_access_logging_in_the_correlation_middleware():
    """Order and position of both, on the real application.

    ``user_middleware`` is outermost first. Both belong ahead of everything
    else so a response an inner middleware short-circuits is still logged, and
    the correlation middleware has to be ahead of the access log so the id
    exists by the time the record is written.
    """
    from reporting.app import create_app

    classes = [middleware.cls for middleware in create_app().user_middleware]

    assert classes.index(CorrelationIdMiddleware) < classes.index(AccessLogMiddleware), (
        "access logging is outside the correlation middleware, so its records carry no request id"
    )
    assert classes.index(AccessLogMiddleware) == 1, (
        "access logging is not outermost-but-one, so some responses go unlogged"
    )


def test_instrument_fastapi_skips_when_tracing_is_off(mocker):
    instrument = mocker.patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app")
    mocker.patch("reporting.services.telemetry.settings.TELEMETRY_ENABLED", False)

    telemetry.instrument_fastapi(object())

    instrument.assert_not_called()


def test_instrument_fastapi_excludes_the_healthcheck(mocker):
    """Health checks are high volume and carry no signal."""
    instrument = mocker.patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app")
    mocker.patch("reporting.services.telemetry.settings.TELEMETRY_ENABLED", True)
    mocker.patch(
        "reporting.services.telemetry.settings.TELEMETRY_OTLP_ENDPOINT",
        "http://collector:4318/v1/traces",
    )
    app = object()

    telemetry.instrument_fastapi(app)

    instrument.assert_called_once_with(app, excluded_urls="healthcheck")
