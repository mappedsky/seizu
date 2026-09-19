"""One access log record per HTTP request, on stdout.

Gunicorn's access log does not work under an ASGI worker. It never parses a
request, so the ``access_log_format`` and ``pre_request`` hook in
``gunicorn.conf`` produce nothing, and uvicorn's replacement logs five fields:
client address, method, path, HTTP version, status. Response time, byte counts
and the forwarded client address are only visible from inside the application.

This is deliberately the smaller half of the job. Request ids come from
``asgi_correlation_id``, whose log filter puts one on *every* record a request
produces rather than only this one, and the per-request span carrying route,
status and duration comes from the OpenTelemetry instrumentation. What is left
is a single readable line per request on stdout -- worth keeping rather than
left to the spans, because stdout is still there when a trace backend is not,
and whatever makes the backend unreachable is often what the requests worth
reading about were failing on.

The fields go out as structured extras rather than packed into one formatted
string: under a JSON formatter a field is queryable and a packed string is not.
The message itself stays readable for anyone reading the log directly.

Two deliberate omissions from the gunicorn format this replaces. ``%(u)s``
(remote user) has no equivalent: identity is resolved by a per-route FastAPI
dependency (``authnz.get_current_user``) that runs far inside this middleware
and does not publish its result on the ASGI scope, and the AUDIT records that
dependency emits already carry the user. ``%(t)s`` (request time) is left to the
formatter, which timestamps every record anyway.
"""

from __future__ import annotations

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

ACCESS_LOGGER_NAME = "reporting.access"

_logger = logging.getLogger(ACCESS_LOGGER_NAME)


def _header(scope: Scope, name: str) -> str | None:
    wanted = name.encode("latin-1")
    for key, value in scope.get("headers", ()):
        if key.lower() == wanted:
            return value.decode("latin-1")
    return None


def _client_addr(scope: Scope) -> str | None:
    client = scope.get("client")
    if not client:
        return None
    host, port = client
    return f"{host}:{port}"


def _request_target(scope: Scope) -> str:
    """Path with the query string, as the request line carried it."""
    path = scope.get("path", "")
    query = scope.get("query_string", b"")
    if not query:
        return path
    return f"{path}?{query.decode('latin-1')}"


def silence_uvicorn_access_log() -> None:
    """Stop uvicorn emitting its own, smaller access line.

    Leaving both on logs every request twice. This does what uvicorn's own
    ``access_log=False`` does, reached directly because the gunicorn worker class
    is constructed in the arbiter before the application is imported and takes no
    application configuration.

    ``logging.conf`` still gives ``gunicorn.access`` an explicit handler, which
    is what the worker wires ``uvicorn.access`` up from. That is the fallback: if
    this middleware is ever removed, uvicorn's access log comes back rather than
    access logging disappearing silently, which is how it was lost before.
    """
    logger = logging.getLogger("uvicorn.access")
    logger.handlers = []
    logger.propagate = False


class AccessLogMiddleware:
    """Log one record per HTTP request, once its response has been sent.

    Pure ASGI rather than ``BaseHTTPMiddleware``, which buffers the response and
    would break the chat turn streams.

    Belongs just inside ``CorrelationIdMiddleware`` and outside everything else:
    outside so that a response short-circuited by an inner middleware -- a CSRF
    rejection, a timeout, an MCP request -- is logged like any other, and inside
    the correlation middleware so the request id is set in the context by the
    time this record is emitted.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        status: int | None = None
        bytes_sent = 0

        async def _send(message: Message) -> None:
            nonlocal status, bytes_sent
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                bytes_sent += len(message.get("body", b""))
            await send(message)

        started = time.perf_counter()
        try:
            await self._app(scope, receive, _send)
        except BaseException:
            # The server turns an unhandled exception into a response this send
            # channel never sees, and a request that failed is the one most worth
            # a record. A status already sent is kept: the failure came part-way
            # through a response that did start.
            self._log(scope, 500 if status is None else status, bytes_sent, started)
            raise
        self._log(scope, status, bytes_sent, started)

    def _log(self, scope: Scope, status: int | None, bytes_sent: int, started: float) -> None:
        duration_us = int((time.perf_counter() - started) * 1_000_000)
        client_addr = _client_addr(scope)
        target = _request_target(scope)

        _logger.info(
            '%s - "%s %s HTTP/%s" %s %dus',
            client_addr or "-",
            scope.get("method", "-"),
            target,
            scope.get("http_version", "-"),
            "-" if status is None else status,
            duration_us,
            extra={
                "method": scope.get("method"),
                "target": target,
                "route": scope.get("path"),
                "http_version": scope.get("http_version"),
                "status": status,
                "duration_us": duration_us,
                "bytes_sent": bytes_sent,
                # The request's declared length, which is what the gunicorn
                # format's `%({Content-Length}i)s` reported -- not a count of
                # body bytes actually read.
                "content_length": _header(scope, "content-length"),
                "client_addr": client_addr,
                "forwarded_for": _header(scope, "x-forwarded-for"),
                "user_agent": _header(scope, "user-agent"),
                "accept": _header(scope, "accept"),
            },
        )
