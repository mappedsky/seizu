"""Spike-only MCP upstream that reports which grant authorized each call.

Serves a single ``whoami`` tool over stateless Streamable HTTP on two ports,
one per Pomerium route, so a result says which route carried it. Every request
needs a bearer token; Authentik's userinfo endpoint is the authority on whether
it is live and whose it is, and the unverified JWT payload adds which client
the grant was issued to and a digest of its ``jti``. A missing or rejected
token gets a 401, which is what makes Pomerium start its upstream OAuth flow.

Standard library only; configured by environment:
  WHOAMI_PORTS              comma-separated ports, labelled a, b, ... in order
  WHOAMI_USERINFO_URL       Authentik userinfo endpoint, reached directly
  WHOAMI_USERINFO_HOST      Host header for it (the browser-facing authority)
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

logging.basicConfig(format="whoami-mcp: %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

USERINFO_URL = os.environ.get("WHOAMI_USERINFO_URL", "http://authentik-server:9000/application/o/userinfo/")
USERINFO_HOST = os.environ.get("WHOAMI_USERINFO_HOST", "localhost:9000")
PROTOCOL_VERSION = "2025-06-18"

WHOAMI_TOOL = {
    "name": "whoami",
    "description": "Report which upstream user and grant authorized this call.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}


def _jwt_payload(token: str) -> dict[str, Any]:
    try:
        segment = token.split(".")[1]
        segment += "=" * (-len(segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(segment))
        return payload if isinstance(payload, dict) else {}
    except (IndexError, ValueError):
        return {}


def _userinfo(token: str) -> dict[str, Any] | None:
    request = urllib.request.Request(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {token}", "Host": USERINFO_HOST},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.load(response)
            return body if isinstance(body, dict) else None
    except urllib.error.HTTPError as exc:
        logger.info("userinfo rejected the token: HTTP %s", exc.code)
        return None


def _handler(label: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "whoami-mcp"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send(self, status: int, body: dict[str, Any] | None, headers: dict[str, str] | None = None) -> None:
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _unauthorized(self, error: str | None) -> None:
            challenge = 'Bearer realm="whoami"'
            if error:
                challenge += f', error="{error}"'
            self._send(401, {"error": error or "unauthorized"}, {"WWW-Authenticate": challenge})

        def do_GET(self) -> None:
            self._send(405, {"error": "use POST"}, {"Allow": "POST"})

        def do_POST(self) -> None:
            authorization = self.headers.get("Authorization", "")
            token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
            if not token:
                logger.info("route %s: no bearer token -> 401", label)
                self._unauthorized(None)
                return
            user = _userinfo(token)
            if user is None:
                logger.info("route %s: token not accepted -> 401", label)
                self._unauthorized("invalid_token")
                return

            length = int(self.headers.get("Content-Length") or 0)
            try:
                message = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._send(400, {"error": "invalid JSON"})
                return
            if not isinstance(message, dict):
                self._send(400, {"error": "batches are not supported"})
                return

            method = message.get("method")
            if "id" not in message:
                self._send(202, None)
                return
            if method == "initialize":
                result: dict[str, Any] = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": f"whoami-{label}", "version": "0.1.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [WHOAMI_TOOL]}
            elif method == "tools/call" and (message.get("params") or {}).get("name") == "whoami":
                claims = _jwt_payload(token)
                grant_digest = hashlib.sha256(str(claims.get("jti", "")).encode()).hexdigest()[:12]
                report: dict[str, Any] = {
                    "route": label,
                    "upstream_user": {
                        key: user.get(key) for key in ("sub", "email", "preferred_username") if key in user
                    },
                    "grant": {
                        "client": claims.get("azp") or claims.get("aud"),
                        "jti_sha256": grant_digest,
                        "issued_at": claims.get("iat"),
                        "expires_at": claims.get("exp"),
                    },
                }
                logger.info(
                    "route %s: whoami as %s (grant %s)",
                    label,
                    user.get("preferred_username"),
                    grant_digest,
                )
                result = {"content": [{"type": "text", "text": json.dumps(report)}], "structuredContent": report}
            else:
                self._send(
                    200,
                    {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "method not found"}},
                )
                return
            self._send(200, {"jsonrpc": "2.0", "id": message["id"], "result": result})

    return Handler


def main() -> None:
    ports = [int(port) for port in os.environ.get("WHOAMI_PORTS", "8080,8081").split(",") if port.strip()]
    servers = []
    for index, port in enumerate(ports):
        label = chr(ord("a") + index)
        server = ThreadingHTTPServer(("0.0.0.0", port), _handler(label))
        servers.append(server)
        logger.info("route %s listening on :%d", label, port)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers[1:]]
    for thread in threads:
        thread.start()
    servers[0].serve_forever()


if __name__ == "__main__":
    main()
