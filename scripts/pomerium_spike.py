#!/usr/bin/env python3
"""Host-side client for the Pomerium delegated-OAuth spike.

Plays Seizu's part in the design under evaluation: one confidential OAuth
client, registered once, holding a refresh token per user and calling MCP
routes as that user with no browser involved after the user connects. See
.config/dev/pomerium-spike/README.md for the questions each command answers.

Standard library only. Runs on the host (the browser and the loopback callback
must meet), against the stack started with the pomerium-spike profile:

  python3 scripts/pomerium_spike.py register
  python3 scripts/pomerium_spike.py connect editor --route a
  python3 scripts/pomerium_spike.py call editor --route a
  python3 scripts/pomerium_spike.py call editor --route b
  python3 scripts/pomerium_spike.py routes editor --route a
  python3 scripts/pomerium_spike.py refresh editor
  python3 scripts/pomerium_spike.py race editor

State (client credentials and per-user tokens) lives in
.compose/pomerium-spike/, which is git-ignored. Tokens are printed only as
short digests.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import secrets
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / ".compose" / "pomerium-spike"
CLIENT_FILE = STATE_DIR / "client.json"
USERS_DIR = STATE_DIR / "users"
ROUTES = {
    "a": "https://whoami-a.localhost.pomerium.io:8443",
    "b": "https://whoami-b.localhost.pomerium.io:8443",
}
CALLBACK_PORT = 8765
REDIRECT_URI = f"http://127.0.0.1:{CALLBACK_PORT}/callback"
PROTOCOL_VERSION = "2025-06-18"


class HTTPResult:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = {key.lower(): value for key, value in headers.items()}
        self.body = body

    def json(self) -> Any:
        content_type = self.headers.get("content-type", "")
        text = self.body.decode(errors="replace")
        if "text/event-stream" in content_type:
            data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
            text = data[-1] if data else "{}"
        return json.loads(text) if text.strip() else {}


def _digest(value: str | None) -> str:
    return hashlib.sha256((value or "").encode()).hexdigest()[:12] if value else "-"


def _ssl_context(cafile: Path) -> ssl.SSLContext:
    if not cafile.exists():
        sys.exit(f"CA not found at {cafile}; start the stack first (it is generated on first start).")
    return ssl.create_default_context(cafile=str(cafile))


def _request(
    ctx: ssl.SSLContext,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> HTTPResult:
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, context=ctx, timeout=30) as response:
            return HTTPResult(response.status, dict(response.headers), response.read())
    except urllib.error.HTTPError as exc:
        return HTTPResult(exc.code, dict(exc.headers), exc.read())


def _form(ctx: ssl.SSLContext, url: str, fields: dict[str, str], client: dict[str, Any]) -> HTTPResult:
    basic = base64.b64encode(f"{client['client_id']}:{client['client_secret']}".encode()).decode()
    return _request(
        ctx,
        "POST",
        url,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"Basic {basic}"},
        body=urllib.parse.urlencode(fields).encode(),
    )


def _well_known(issuer: str, name: str) -> str:
    """RFC 8414 section 3.1: the well-known segment goes before any issuer path."""
    parts = urllib.parse.urlsplit(issuer)
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, f"/.well-known/{name}{path}", "", ""))


def _challenge_metadata_url(result: HTTPResult) -> str | None:
    challenge = result.headers.get("www-authenticate", "")
    marker = 'resource_metadata="'
    if marker not in challenge:
        return None
    return challenge.split(marker, 1)[1].split('"', 1)[0]


def discover(ctx: ssl.SSLContext, route_url: str) -> dict[str, Any]:
    """Follow an unauthenticated 401 to the route's authorization server metadata."""
    probe = _mcp_post(ctx, route_url, None, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    metadata_url = _challenge_metadata_url(probe)
    if probe.status != 401 or not metadata_url:
        sys.exit(f"expected a 401 with resource_metadata from {route_url}, got HTTP {probe.status}: {probe.headers}")
    resource = _request(ctx, "GET", metadata_url).json()
    issuer = resource["authorization_servers"][0]
    server = _request(ctx, "GET", _well_known(issuer, "oauth-authorization-server")).json()
    return {"resource": resource, "server": server}


def _mcp_post(
    ctx: ssl.SSLContext, url: str, token: str | None, message: dict[str, Any], session: str | None = None
) -> HTTPResult:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session:
        headers["Mcp-Session-Id"] = session
    return _request(ctx, "POST", url + "/", headers=headers, body=json.dumps(message).encode())


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)


def _user_file(name: str) -> Path:
    return USERS_DIR / f"{name}.json"


def _require_client() -> dict[str, Any]:
    client = _load(CLIENT_FILE)
    if not client:
        sys.exit("no client registered; run `register` first")
    return client


def _require_user(name: str) -> dict[str, Any]:
    user = _load(_user_file(name))
    if not user:
        sys.exit(f"user {name!r} has not connected; run `connect {name}` first")
    return user


# ── commands ────────────────────────────────────────────────────────────────


def cmd_register(args: argparse.Namespace, ctx: ssl.SSLContext) -> None:
    """Register the single confidential client Seizu would use for every user."""
    found = discover(ctx, ROUTES[args.route])
    endpoint = found["server"].get("registration_endpoint")
    if not endpoint:
        sys.exit("authorization server advertises no registration_endpoint (is mcp_dynamic_client_registration on?)")
    result = _request(
        ctx,
        "POST",
        endpoint,
        headers={"Content-Type": "application/json"},
        body=json.dumps(
            {
                "client_name": "Seizu (pomerium spike)",
                "redirect_uris": [REDIRECT_URI],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "client_secret_basic",
            }
        ).encode(),
    )
    if result.status not in (200, 201):
        sys.exit(f"registration failed: HTTP {result.status} {result.body[:500]!r}")
    registration = result.json()
    _save(CLIENT_FILE, {**registration, "registered_via": args.route})
    print(f"registered client {registration['client_id']} via route {args.route}")
    has_secret = "yes" if registration.get("client_secret") else "no"
    print(f"  auth method: {registration.get('token_endpoint_auth_method')}  secret: {has_secret}")


def _wait_for_code(state: str) -> str:
    received: dict[str, str] = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            received.update(query)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Spike client received the authorization response. You can close this tab.")
            threading.Thread(target=server.shutdown, daemon=True).start()

    server = http.server.HTTPServer(("127.0.0.1", CALLBACK_PORT), Callback)
    server.serve_forever()
    server.server_close()
    if received.get("state") != state:
        sys.exit(f"state mismatch in callback: {received}")
    if "code" not in received:
        sys.exit(f"authorization failed: {received}")
    return received["code"]


def _authorize(ctx: ssl.SSLContext, client: dict[str, Any], route: str, browser: bool) -> dict[str, Any]:
    route_url = ROUTES[route]
    server = discover(ctx, route_url)["server"]
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = (
        server["authorization_endpoint"]
        + "?"
        + urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": client["client_id"],
                "redirect_uri": REDIRECT_URI,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "resource": route_url,
            }
        )
    )
    print(
        f"\nOpen this URL in a browser that trusts {STATE_DIR / 'ca.pem'} (or click through the warning):\n\n  {url}\n"
    )
    if browser:
        webbrowser.open(url)
    code = _wait_for_code(state)
    result = _form(
        ctx,
        server["token_endpoint"],
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
            "resource": route_url,
        },
        client,
    )
    if result.status != 200:
        sys.exit(f"code exchange failed: HTTP {result.status} {result.body[:500]!r}")
    tokens = result.json()
    return {
        "route": route,
        "token_endpoint": server["token_endpoint"],
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "expires_at": time.time() + float(tokens.get("expires_in") or 0),
        "scope": tokens.get("scope"),
    }


def _whoami(ctx: ssl.SSLContext, route: str, token: str) -> tuple[HTTPResult, dict[str, Any] | None]:
    url = ROUTES[route]
    init = _mcp_post(
        ctx,
        url,
        token,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pomerium-spike", "version": "0"},
            },
        },
    )
    if init.status != 200:
        return init, None
    session = init.headers.get("mcp-session-id")
    _mcp_post(ctx, url, token, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
    call = _mcp_post(
        ctx,
        url,
        token,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "whoami", "arguments": {}}},
        session,
    )
    if call.status != 200:
        return call, None
    body = call.json()
    result = body.get("result") or {}
    return call, result.get("structuredContent") or body


def _describe_failure(result: HTTPResult) -> str:
    detail = result.headers.get("www-authenticate") or result.body[:300].decode(errors="replace")
    return f"HTTP {result.status}: {detail}"


def cmd_connect(args: argparse.Namespace, ctx: ssl.SSLContext) -> None:
    """Authorize a user, repeating while Pomerium still needs upstream consent."""
    client = _require_client()
    for attempt in range(1, 4):
        print(f"── authorization round {attempt} for {args.user!r} on route {args.route}")
        user = _authorize(ctx, client, args.route, args.browser)
        _save(_user_file(args.user), user)
        result, report = _whoami(ctx, args.route, user["access_token"])
        if report is not None:
            print(f"connected. whoami: {json.dumps(report)}")
            print(
                f"refresh token issued: {'yes' if user['refresh_token'] else 'NO'} ({_digest(user['refresh_token'])})"
            )
            return
        print(
            f"call after round {attempt} failed ({_describe_failure(result)}); authorizing again for upstream consent"
        )
    sys.exit("still not authorized after three rounds")


def _refresh(ctx: ssl.SSLContext, client: dict[str, Any], user: dict[str, Any], refresh_token: str) -> HTTPResult:
    route_url = ROUTES[user["route"]]
    return _form(
        ctx,
        user["token_endpoint"],
        {"grant_type": "refresh_token", "refresh_token": refresh_token, "resource": route_url},
        client,
    )


def _apply_refresh(name: str, user: dict[str, Any], result: HTTPResult) -> dict[str, Any]:
    tokens = result.json()
    user = {
        **user,
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token") or user["refresh_token"],
        "expires_at": time.time() + float(tokens.get("expires_in") or 0),
    }
    _save(_user_file(name), user)
    return user


def cmd_call(args: argparse.Namespace, ctx: ssl.SSLContext) -> None:
    """Call whoami as a stored user, refreshing once on a 401 — no browser."""
    user = _require_user(args.user)
    route = args.route or user["route"]
    if route != user["route"]:
        print(f"note: token was issued for route {user['route']}, calling route {route}")
    result, report = _whoami(ctx, route, user["access_token"])
    if report is None and result.status == 401 and user.get("refresh_token"):
        print(f"401 ({_describe_failure(result)}); refreshing")
        refreshed = _refresh(ctx, _require_client(), user, user["refresh_token"])
        if refreshed.status != 200:
            sys.exit(f"refresh failed: HTTP {refreshed.status} {refreshed.body[:300]!r}")
        user = _apply_refresh(args.user, user, refreshed)
        result, report = _whoami(ctx, route, user["access_token"])
    if report is None:
        sys.exit(f"whoami failed on route {route}: {_describe_failure(result)}")
    print(json.dumps(report, indent=2))


def cmd_routes(args: argparse.Namespace, ctx: ssl.SSLContext) -> None:
    """Ask Pomerium which routes this user has connected upstream."""
    user = _require_user(args.user)
    route = args.route or user["route"]
    result = _request(
        ctx,
        "GET",
        ROUTES[route] + "/.pomerium/mcp/routes",
        headers={"Authorization": f"Bearer {user['access_token']}", "Accept": "application/json"},
    )
    print(f"HTTP {result.status}")
    try:
        print(json.dumps(result.json(), indent=2))
    except ValueError:
        print(result.body[:1000].decode(errors="replace"))


def cmd_refresh(args: argparse.Namespace, ctx: ssl.SSLContext) -> None:
    """Refresh once, then replay the old refresh token to see whether rotation revokes it."""
    client = _require_client()
    user = _require_user(args.user)
    old = user["refresh_token"]
    if not old:
        sys.exit("no refresh token stored")
    result = _refresh(ctx, client, user, old)
    if result.status != 200:
        sys.exit(f"refresh failed: HTTP {result.status} {result.body[:300]!r}")
    user = _apply_refresh(args.user, user, result)
    print(f"refreshed: {_digest(old)} -> {_digest(user['refresh_token'])} (rotated: {user['refresh_token'] != old})")
    replay = _refresh(ctx, client, user, old)
    print(f"replaying the old refresh token: HTTP {replay.status} {replay.body[:200].decode(errors='replace')}")


def cmd_race(args: argparse.Namespace, ctx: ssl.SSLContext) -> None:
    """Spend one refresh token from two workers at once, as two Seizu workers might."""
    client = _require_client()
    user = _require_user(args.user)
    token = user["refresh_token"]
    outcomes: list[HTTPResult | None] = [None] * args.workers
    barrier = threading.Barrier(args.workers)

    def spend(index: int) -> None:
        barrier.wait()
        outcomes[index] = _refresh(ctx, client, user, token)

    threads = [threading.Thread(target=spend, args=(i,)) for i in range(args.workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    winners = [result for result in outcomes if result is not None and result.status == 200]
    for index, result in enumerate(outcomes):
        assert result is not None
        print(f"worker {index}: HTTP {result.status}")
    print(f"{len(winners)} of {args.workers} concurrent refreshes succeeded")
    if winners:
        _apply_refresh(args.user, user, winners[-1])
        print("stored the last successful result; run `call` to check it is still the live one")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cafile", type=Path, default=STATE_DIR / "ca.pem")
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register", help="register the one confidential client")
    register.add_argument("--route", choices=ROUTES, default="a")
    register.set_defaults(func=cmd_register)

    connect = sub.add_parser("connect", help="authorize a user in the browser")
    connect.add_argument("user")
    connect.add_argument("--route", choices=ROUTES, default="a")
    connect.add_argument("--browser", action="store_true", help="also open the URL with the default browser")
    connect.set_defaults(func=cmd_connect)

    call = sub.add_parser("call", help="call whoami as a stored user")
    call.add_argument("user")
    call.add_argument("--route", choices=ROUTES)
    call.set_defaults(func=cmd_call)

    routes = sub.add_parser("routes", help="list the user's upstream connection status")
    routes.add_argument("user")
    routes.add_argument("--route", choices=ROUTES)
    routes.set_defaults(func=cmd_routes)

    refresh = sub.add_parser("refresh", help="refresh and replay the old token")
    refresh.add_argument("user")
    refresh.set_defaults(func=cmd_refresh)

    race = sub.add_parser("race", help="spend one refresh token concurrently")
    race.add_argument("user")
    race.add_argument("--workers", type=int, default=2)
    race.set_defaults(func=cmd_race)

    args = parser.parse_args()
    args.func(args, _ssl_context(args.cafile))


if __name__ == "__main__":
    main()
