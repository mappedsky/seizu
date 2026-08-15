"""Authorize the local external-MCP proxy and save its bearer in ``.env``.

This is intentionally standard-library only so it can run on the host without
installing a Python environment.  The proxy publishes OAuth server metadata and
supports dynamic registration, so every invocation uses a short-lived public
PKCE authorization flow and a loopback redirect URI.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

DEFAULT_PROXY_URL = "http://localhost:8081"
DEFAULT_CALLBACK_HOST = "127.0.0.1"
DEFAULT_CALLBACK_PORT = 8765
TOKEN_ENV_NAME = "MCP_EXTERNAL_PROXY_TOKEN"
# Authentik issues the proxy an access token valid for an hour and a refresh
# token valid for thirty days. Keeping only the access token is why a login
# "expired unexpectedly" an hour later, mid-investigation, with discovery
# silently degrading to ext__github__seizu_authenticate. Persist both, plus
# the dynamically registered client the refresh grant has to present.
REFRESH_ENV_NAME = "MCP_EXTERNAL_PROXY_REFRESH_TOKEN"
CLIENT_ENV_NAME = "MCP_EXTERNAL_PROXY_CLIENT_ID"


class LoginError(RuntimeError):
    """A safe-to-display OAuth login failure."""


def _json_request(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # OAuth response bodies can contain credentials. Never include them in
        # terminal output, even on failure.
        raise LoginError(f"{url} returned HTTP {exc.code}") from None
    except (OSError, ValueError) as exc:
        raise LoginError(f"could not read {url}: {exc}") from None
    if not isinstance(result, dict):
        raise LoginError(f"{url} returned an invalid JSON object")
    return result


def _form_request(url: str, payload: dict[str, str]) -> dict[str, Any]:
    data = urllib.parse.urlencode(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise LoginError(f"token exchange returned HTTP {exc.code}") from None
    except (OSError, ValueError) as exc:
        raise LoginError(f"token exchange failed: {exc}") from None
    if not isinstance(result, dict):
        raise LoginError("token endpoint returned an invalid JSON object")
    return result


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _read_env_value(path: Path, key: str) -> str | None:
    """Read one dotenv value, or None. Never logged: these are credentials."""
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            value = stripped.split("=", 1)[1].strip()
            return value or None
    return None


def _write_env_value(path: Path, key: str, value: str) -> None:
    """Atomically replace one dotenv value without exposing it to a shell."""
    original = path.read_text() if path.exists() else ""
    lines = original.splitlines(keepends=True)
    replacement = f"{key}={value}\n"
    found = False
    updated: list[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            if not found:
                updated.append(replacement)
                found = True
            continue
        updated.append(line)
    if not found:
        if updated and not updated[-1].endswith(("\n", "\r")):
            updated[-1] += "\n"
        updated.append(replacement)

    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.writelines(updated)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _callback_server(
    host: str, port: int, expected_state: str
) -> tuple[ThreadingHTTPServer, dict[str, str], threading.Event]:
    result: dict[str, str] = {}
    completed = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_error(404)
                return
            params = urllib.parse.parse_qs(parsed.query)
            state = params.get("state", [""])[0]
            if not secrets.compare_digest(state, expected_state):
                result["error"] = "OAuth callback state did not match"
                status = 400
                message = "Authentication failed. Return to the terminal for details."
            elif error := params.get("error", [""])[0]:
                result["error"] = f"authorization failed: {error}"
                status = 400
                message = "Authentication was not completed. Return to the terminal."
            elif code := params.get("code", [""])[0]:
                result["code"] = code
                status = 200
                message = "GitHub MCP authentication complete. You can close this tab."
            else:
                result["error"] = "OAuth callback did not contain an authorization code"
                status = 400
                message = "Authentication failed. Return to the terminal for details."
            body = f"<!doctype html><title>Seizu external MCP</title><p>{message}</p>".encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            completed.set()

        def log_message(self, _format: str, *_args: object) -> None:
            # The default logger includes the callback URL and authorization
            # code. Keep the credential out of terminal history.
            return

    server = ThreadingHTTPServer((host, port), CallbackHandler)
    server.timeout = 1
    return server, result, completed


def _wait_for_callback(
    server: ThreadingHTTPServer, result: dict[str, str], completed: threading.Event, timeout: int
) -> str:
    deadline = time.monotonic() + timeout
    try:
        while not completed.is_set() and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not completed.is_set():
        raise LoginError("timed out waiting for the browser callback")
    if error := result.get("error"):
        raise LoginError(error)
    if not (code := result.get("code")):
        raise LoginError("OAuth callback did not contain an authorization code")
    return code


def refresh(proxy_url: str, client_id: str, refresh_token: str) -> dict[str, str]:
    """Exchange a stored refresh token, or raise LoginError to fall back."""
    proxy_url = proxy_url.rstrip("/")
    metadata = _json_request(f"{proxy_url}/.well-known/oauth-authorization-server")
    token_endpoint = metadata.get("token_endpoint")
    if not isinstance(token_endpoint, str) or not token_endpoint:
        raise LoginError("proxy OAuth metadata is missing its token endpoint")
    token_response = _form_request(
        token_endpoint,
        {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh_token},
    )
    return _grant_from(token_response, client_id, fallback_refresh=refresh_token)


def _grant_from(token_response: dict[str, Any], client_id: str, fallback_refresh: str = "") -> dict[str, str]:
    access_token = token_response.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise LoginError("token endpoint did not return an access token")
    # A server that rotates refresh tokens returns a new one; one that does not
    # expects the old one to keep working, so carry it forward rather than
    # dropping the only thing that avoids the next browser round trip.
    rotated = token_response.get("refresh_token")
    refresh_token = rotated if isinstance(rotated, str) and rotated else fallback_refresh
    return {"access_token": access_token, "refresh_token": refresh_token, "client_id": client_id}


def login(proxy_url: str, callback_host: str, callback_port: int, timeout: int) -> dict[str, str]:
    proxy_url = proxy_url.rstrip("/")
    metadata = _json_request(f"{proxy_url}/.well-known/oauth-authorization-server")
    authorization_endpoint = metadata.get("authorization_endpoint")
    registration_endpoint = metadata.get("registration_endpoint")
    token_endpoint = metadata.get("token_endpoint")
    if not isinstance(authorization_endpoint, str) or not authorization_endpoint:
        raise LoginError("proxy OAuth metadata is missing its authorization endpoint")
    if not isinstance(registration_endpoint, str) or not registration_endpoint:
        raise LoginError("proxy OAuth metadata is missing its registration endpoint")
    if not isinstance(token_endpoint, str) or not token_endpoint:
        raise LoginError("proxy OAuth metadata is missing its token endpoint")

    redirect_uri = f"http://{callback_host}:{callback_port}/callback"
    registration = _json_request(
        registration_endpoint,
        {
            "client_name": "Seizu local external MCP",
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    client_id = registration.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise LoginError("proxy client registration did not return a client_id")

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    try:
        server, callback_result, completed = _callback_server(callback_host, callback_port, state)
    except OSError as exc:
        raise LoginError(f"could not listen on {callback_host}:{callback_port}: {exc}") from None
    scopes = metadata.get("scopes_supported")
    scope = " ".join(value for value in scopes if isinstance(value, str)) if isinstance(scopes, list) else ""
    authorization_url = f"{authorization_endpoint}?{
        urllib.parse.urlencode(
            {
                'response_type': 'code',
                'client_id': client_id,
                'redirect_uri': redirect_uri,
                'scope': scope,
                'state': state,
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
            }
        )
    }"

    print("Open this URL to authorize the local GitHub MCP proxy:")
    print(authorization_url)
    webbrowser.open(authorization_url)
    code = _wait_for_callback(server, callback_result, completed, timeout)
    token_response = _form_request(
        token_endpoint,
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code": code,
            "code_verifier": verifier,
        },
    )
    return _grant_from(token_response, client_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy-url", default=DEFAULT_PROXY_URL)
    parser.add_argument("--callback-host", default=DEFAULT_CALLBACK_HOST)
    parser.add_argument("--callback-port", type=int, default=DEFAULT_CALLBACK_PORT)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the stored refresh token and authorize from scratch in the browser.",
    )
    args = parser.parse_args(argv)
    grant: dict[str, str] | None = None
    stored_refresh = _read_env_value(args.env_file, REFRESH_ENV_NAME)
    stored_client = _read_env_value(args.env_file, CLIENT_ENV_NAME)
    if stored_refresh and stored_client and not args.force:
        try:
            grant = refresh(args.proxy_url, stored_client, stored_refresh)
            print("Renewed the stored proxy credential; no browser round trip was needed.")
        except LoginError as exc:
            # An expired or rejected refresh token is the ordinary end of a
            # thirty-day window, not an error worth failing on.
            print(f"Stored credential could not be renewed ({exc}); starting a new authorization.")
    if grant is None:
        try:
            grant = login(args.proxy_url, args.callback_host, args.callback_port, args.timeout)
        except LoginError as exc:
            print(f"External MCP login failed: {exc}", file=sys.stderr)
            return 1
    _write_env_value(args.env_file, TOKEN_ENV_NAME, grant["access_token"])
    if grant["refresh_token"]:
        _write_env_value(args.env_file, REFRESH_ENV_NAME, grant["refresh_token"])
    _write_env_value(args.env_file, CLIENT_ENV_NAME, grant["client_id"])
    print(f"Authentication complete; {TOKEN_ENV_NAME} was updated in {args.env_file}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
