import asyncio
from typing import Any

from reporting.app import _CSRF_FAILURE_BODY, _TIMEOUT_RESPONSE_BODY, _CSRFMiddleware, _TimeoutMiddleware


async def test_timeout_middleware_returns_504_for_slow_http_request():
    sent_messages: list[dict[str, Any]] = []

    async def slow_app(scope, receive, send):
        await asyncio.sleep(0.01)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    middleware = _TimeoutMiddleware(slow_app, timeout=0.001)
    await middleware({"type": "http", "path": "/", "method": "GET"}, receive, send)

    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 504
    assert sent_messages[0]["headers"] == [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(_TIMEOUT_RESPONSE_BODY)).encode()),
    ]
    assert sent_messages[1]["type"] == "http.response.body"
    assert sent_messages[1]["body"] == _TIMEOUT_RESPONSE_BODY
    assert sent_messages[1]["more_body"] is False


async def test_timeout_middleware_closes_started_response_on_timeout():
    sent_messages: list[dict[str, Any]] = []

    async def streaming_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"partial", "more_body": True})
        await asyncio.sleep(0.01)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    middleware = _TimeoutMiddleware(streaming_app, timeout=0.001, exempt_paths=frozenset())
    await middleware({"type": "http", "path": "/stream", "method": "GET"}, receive, send)

    assert sent_messages[-1] == {"type": "http.response.body", "body": b"", "more_body": False}


async def test_timeout_middleware_exempts_chat_stream():
    sent_messages: list[dict[str, Any]] = []

    async def slow_chat_app(scope, receive, send):
        await asyncio.sleep(0.01)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"done", "more_body": False})

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    middleware = _TimeoutMiddleware(slow_chat_app, timeout=0.001)
    await middleware({"type": "http", "path": "/api/v1/chat/stream", "method": "POST"}, receive, send)

    assert sent_messages[-1] == {"type": "http.response.body", "body": b"done", "more_body": False}


async def test_timeout_middleware_passes_through_non_http_scope():
    called = False

    async def passthrough_app(scope, receive, send):
        nonlocal called
        called = True

    async def receive() -> dict[str, Any]:
        raise AssertionError("receive should not be called")

    async def send(message: dict[str, Any]) -> None:
        raise AssertionError("send should not be called")

    middleware = _TimeoutMiddleware(passthrough_app, timeout=0.001)
    await middleware({"type": "lifespan"}, receive, send)

    assert called is True


# --------------------------------------------------------------------------- #
#  CSRF middleware                                                             #
# --------------------------------------------------------------------------- #


async def _run_csrf(scope: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """Drive the CSRF middleware once, returning sent messages + downstream-called flag."""
    sent: list[dict[str, Any]] = []
    downstream_called = False

    async def downstream(_scope, _receive, _send):
        nonlocal downstream_called
        downstream_called = True

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = _CSRFMiddleware(downstream, cookie_name="seizu_session")
    await middleware(scope, receive, send)
    return sent, downstream_called


def _http_scope(method: str, headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    return {"type": "http", "method": method, "path": "/api/v1/auth/refresh", "headers": headers}


async def test_csrf_passes_get_requests_without_check():
    sent, called = await _run_csrf(_http_scope("GET", [(b"cookie", b"seizu_session=anything")]))
    assert called is True
    assert sent == []


async def test_csrf_passes_post_when_no_cookie_present():
    sent, called = await _run_csrf(_http_scope("POST", []))
    assert called is True
    assert sent == []


async def test_csrf_passes_post_when_cookie_but_different_name():
    headers = [(b"cookie", b"some_other_cookie=value")]
    sent, called = await _run_csrf(_http_scope("POST", headers))
    assert called is True
    assert sent == []


async def test_csrf_blocks_post_when_cookie_present_but_header_missing():
    headers = [(b"cookie", b"seizu_session=ENCRYPTED")]
    sent, called = await _run_csrf(_http_scope("POST", headers))
    assert called is False
    assert sent[0]["status"] == 403
    assert sent[1]["body"] == _CSRF_FAILURE_BODY


async def test_csrf_passes_post_when_cookie_and_header_present():
    headers = [
        (b"cookie", b"seizu_session=ENCRYPTED"),
        (b"x-seizu-csrf", b"1"),
    ]
    sent, called = await _run_csrf(_http_scope("POST", headers))
    assert called is True
    assert sent == []


async def test_csrf_treats_empty_header_value_as_missing():
    headers = [
        (b"cookie", b"seizu_session=ENCRYPTED"),
        (b"x-seizu-csrf", b"   "),  # whitespace only
    ]
    sent, called = await _run_csrf(_http_scope("POST", headers))
    assert called is False
    assert sent[0]["status"] == 403


async def test_csrf_blocks_even_when_bearer_also_present():
    """If the request has BOTH cookie and Bearer, treat as cookie-auth and require CSRF."""
    headers = [
        (b"cookie", b"seizu_session=ENCRYPTED"),
        (b"authorization", b"Bearer some-jwt"),
    ]
    sent, called = await _run_csrf(_http_scope("POST", headers))
    assert called is False
    assert sent[0]["status"] == 403


async def test_csrf_passes_through_non_http_scope():
    sent, called = await _run_csrf({"type": "lifespan"})
    assert called is True
    assert sent == []
