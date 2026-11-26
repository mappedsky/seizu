#!/usr/bin/env python3
"""Check a sandbox image against the ``seizu-sandbox/1`` protocol.

Standard library only, so an image repository can vendor this file and run it
in its own CI. Start the image with an empty directory mounted at the workspace
and point this at it::

    docker run --rm -d -p 8080:8080 -v "$PWD/ws:/mnt/workspace" my-sandbox-image
    python sandbox_image_conformance.py --url http://localhost:8080

Exits 0 when every check passes, 1 otherwise. The contract is
docs/root/dev/sandbox-image-protocol.md and schema/sandbox-protocol-v1.json in
the Seizu repository; this checks behaviour the schema cannot express
(interpreter state, the marker rule, timeouts, concurrency, ``/ping``).

The checks wipe the workspace: never point this at a sandbox holding data.
"""

import argparse
import base64
import concurrent.futures
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

PROTOCOL = "seizu-sandbox/1"


class CheckFailed(AssertionError):
    pass


class Client:
    def __init__(self, url: str, request_timeout: float) -> None:
        self.url = url.rstrip("/")
        self.request_timeout = request_timeout

    def raw(self, body: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.url}/invocations",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.request_timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            payload = exc.read()
        try:
            parsed = json.loads(payload)
        except ValueError as exc:
            raise CheckFailed(f"response is not JSON: {payload[:200]!r}") from exc
        if not isinstance(parsed, dict) or "ok" not in parsed:
            raise CheckFailed(f"response is not an envelope: {parsed!r}")
        return parsed

    def call(self, op: str, args: dict[str, Any] | None = None, timeout_seconds: int = 30) -> dict[str, Any]:
        """Run an op that must succeed; return its result."""
        body = {"protocol": PROTOCOL, "op": op, "args": args or {}, "timeout_seconds": timeout_seconds}
        response = self.raw(body, timeout=timeout_seconds + 30)
        if response.get("ok") is not True:
            raise CheckFailed(f"{op} failed: {response.get('error')!r}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise CheckFailed(f"{op} returned no result object: {response!r}")
        return result

    def error(self, op: str, args: dict[str, Any] | None = None, protocol: str = PROTOCOL) -> str:
        """Run an op that must fail; return its error code."""
        body = {"protocol": protocol, "op": op, "args": args or {}, "timeout_seconds": 30}
        response = self.raw(body)
        if response.get("ok") is not False:
            raise CheckFailed(f"{op} should have failed, got {response!r}")
        error = response.get("error") or {}
        return str(error.get("code", ""))

    def ping(self) -> dict[str, Any]:
        with urllib.request.urlopen(f"{self.url}/ping", timeout=self.request_timeout) as response:
            if response.status != 200:
                raise CheckFailed(f"/ping returned HTTP {response.status}")
            parsed = json.loads(response.read())
        if not isinstance(parsed, dict):
            raise CheckFailed(f"/ping did not return an object: {parsed!r}")
        return parsed


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def _marker(thread: str) -> dict[str, str]:
    return {
        "deployment": "conformance",
        "purpose": "conformance-check",
        "thread": thread,
        "created_at": "2026-01-01T00:00:00+00:00",
    }


def build_checks(client: Client, *, home: str, workspace: str, timeouts: bool) -> list[tuple[str, Callable[[], None]]]:
    def ping_healthy() -> None:
        status = client.ping().get("status")
        expect(status in {"Healthy", "HealthyBusy"}, f"/ping status {status!r}")

    def wipe_empties_workspace() -> None:
        client.call("write_file", {"path": f"{home}/leftover.txt", "content_b64": base64.b64encode(b"x").decode()})
        client.call("wipe")
        names = {e["name"] for e in client.call("list_files", {"path": home})["entries"]}
        expect(not names, f"workspace not empty after wipe: {sorted(names)}")

    def hello_contract() -> None:
        result = client.call("hello")
        expect(1 in result.get("protocol_versions", []), f"protocol_versions {result.get('protocol_versions')!r}")
        expect(result.get("home") == home, f"home {result.get('home')!r}, expected {home!r}")
        expect(result.get("storage_semantics") in {"posix", "object"}, "storage_semantics")
        expect(result.get("marker") is None, "a wiped workspace must have no marker")

    def marker_written_once() -> None:
        first = client.call("hello", {"create_marker": _marker("first")})["marker"]
        expect(first == _marker("first"), f"marker not written as given: {first!r}")
        again = client.call("hello", {"create_marker": _marker("second")})["marker"]
        expect(again == _marker("first"), "an existing marker must never be overwritten")
        expect(client.call("hello")["marker"] == _marker("first"), "hello must read the marker back")

    def home_is_in_workspace() -> None:
        out = client.call("run_bash", {"command": 'cd "$HOME" && pwd -P'})
        expect(out["exit_code"] == 0, f"cd $HOME failed: {out!r}")
        expect(out["stdout"].strip().startswith(workspace), f"$HOME resolves to {out['stdout'].strip()!r}")

    def files_round_trip() -> None:
        blob = bytes(range(256)) * 4
        path = f"{home}/conformance/nested/blob.bin"
        written = client.call("write_file", {"path": path, "content_b64": base64.b64encode(blob).decode()})
        expect(written.get("size") == len(blob), f"write size {written.get('size')}")
        read = client.call("read_file", {"path": path})
        expect(base64.b64decode(read["content_b64"]) == blob, "read_file did not return the bytes written")
        expect(read.get("size") == len(blob), f"read size {read.get('size')}")
        entries = {e["name"]: e["type"] for e in client.call("list_files", {"path": f"{home}/conformance"})["entries"]}
        expect(entries.get("nested") == "dir", f"list_files entries {entries!r}")
        files = {
            e["name"]: e["type"] for e in client.call("list_files", {"path": f"{home}/conformance/nested"})["entries"]
        }
        expect(files.get("blob.bin") == "file", f"list_files entries {files!r}")

    def missing_file_is_not_found() -> None:
        code = client.error("read_file", {"path": f"{home}/does/not/exist"})
        expect(code == "not_found", f"error code {code!r}")

    def python_output_and_state() -> None:
        out = client.call("run_python", {"code": "import sys\nprint('hello')\nprint('warn', file=sys.stderr)"})
        expect(out.get("stdout") == "hello\n", f"stdout {out.get('stdout')!r}")
        expect("warn" in out.get("stderr", ""), f"stderr {out.get('stderr')!r}")
        client.call("run_python", {"code": "conformance_x = 41"})
        out = client.call("run_python", {"code": "conformance_x + 1"})
        expect(out.get("text") == "42", f"a trailing expression must become text; got {out!r}")
        out = client.call("run_python", {"code": "1/0"})
        error = out.get("error") or {}
        expect(error.get("name") == "ZeroDivisionError", f"error {error!r}")
        expect(bool(error.get("traceback")), "a Python error must carry its traceback")

    def python_timeout_restarts() -> None:
        client.call("run_python", {"code": "conformance_y = 1"})
        body = {"protocol": PROTOCOL, "op": "run_python", "args": {"code": "import time\ntime.sleep(30)"}}
        started = time.monotonic()
        response = client.raw({**body, "timeout_seconds": 2}, timeout=25)
        expect(time.monotonic() - started < 20, "run_python ignored timeout_seconds")
        if response.get("ok"):
            expect(response["result"].get("restarted") is True, f"timed-out run must report restarted: {response!r}")
        else:
            expect((response.get("error") or {}).get("code") == "timeout", f"unexpected error {response!r}")
        out = client.call("run_python", {"code": "conformance_y"})
        expect((out.get("error") or {}).get("name") == "NameError", "state must be gone after a restart")

    def bash_outcomes() -> None:
        out = client.call("run_bash", {"command": "echo out; echo err >&2; exit 3"})
        expect(out.get("stdout") == "out\n", f"stdout {out.get('stdout')!r}")
        expect(out.get("stderr") == "err\n", f"stderr {out.get('stderr')!r}")
        expect(out.get("exit_code") == 3, f"exit_code {out.get('exit_code')!r}")
        out = client.call("run_bash", {"command": "pwd -P", "cwd": f"{home}/conformance"})
        expect(out["stdout"].strip().endswith("/conformance"), f"cwd not honoured: {out!r}")

    def bash_timeout() -> None:
        body = {"protocol": PROTOCOL, "op": "run_bash", "args": {"command": "sleep 30"}, "timeout_seconds": 2}
        started = time.monotonic()
        response = client.raw(body, timeout=25)
        expect(time.monotonic() - started < 20, "run_bash ignored timeout_seconds")
        if response.get("ok"):
            expect(response["result"].get("timed_out") is True, f"timed-out command must say so: {response!r}")
        else:
            expect((response.get("error") or {}).get("code") == "timeout", f"unexpected error {response!r}")

    def concurrent_and_busy() -> None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(client.call, "run_bash", {"command": f"sleep 2; echo {n}"}, 30) for n in range(4)]
            time.sleep(0.5)
            busy = client.ping().get("status")
            outputs = sorted(f.result()["stdout"].strip() for f in futures)
        expect(outputs == ["0", "1", "2", "3"], f"concurrent outputs {outputs!r}")
        expect(busy == "HealthyBusy", f"/ping during work returned {busy!r}, expected HealthyBusy")
        expect(client.ping().get("status") == "Healthy", "/ping must return Healthy once work is done")

    def rejects_unknown() -> None:
        expect(client.error("no_such_op") == "unsupported_op", "unknown op must be unsupported_op")
        code = client.error("hello", protocol="seizu-sandbox/999")
        expect(code == "unsupported_protocol", f"unknown protocol gave {code!r}")

    def wipe_removes_marker() -> None:
        client.call("wipe")
        expect(client.call("hello")["marker"] is None, "wipe must remove the marker")

    checks: list[tuple[str, Callable[[], None]]] = [
        ("ping is healthy", ping_healthy),
        ("wipe empties the workspace", wipe_empties_workspace),
        ("hello contract", hello_contract),
        ("marker is written once and read back", marker_written_once),
        ("$HOME is inside the workspace mount", home_is_in_workspace),
        ("files round-trip as bytes", files_round_trip),
        ("missing file is not_found", missing_file_is_not_found),
        ("python output and persistent state", python_output_and_state),
        ("bash outcomes and cwd", bash_outcomes),
        ("concurrent calls and HealthyBusy", concurrent_and_busy),
        ("unknown op and protocol are rejected", rejects_unknown),
    ]
    if timeouts:
        checks += [
            ("python timeout restarts the interpreter", python_timeout_restarts),
            ("bash timeout", bash_timeout),
        ]
    checks.append(("wipe removes the marker", wipe_removes_marker))
    return checks


def run(url: str, *, home: str, workspace: str, timeouts: bool = True, out: Any = sys.stdout) -> bool:
    client = Client(url, request_timeout=60)
    failures = 0
    for name, check in build_checks(client, home=home, workspace=workspace, timeouts=timeouts):
        try:
            check()
        except Exception as exc:
            failures += 1
            print(f"FAIL  {name}: {exc}", file=out)
        else:
            print(f"ok    {name}", file=out)
    print(f"{'PASSED' if not failures else f'FAILED ({failures})'}", file=out)
    return failures == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8080", help="Base URL of the running image.")
    parser.add_argument("--home", default="/home/user", help="$HOME the image must report.")
    parser.add_argument("--workspace", default="/mnt/workspace", help="Where $HOME must resolve to.")
    parser.add_argument("--skip-timeouts", action="store_true", help="Skip the slower timeout checks.")
    args = parser.parse_args(argv)
    return 0 if run(args.url, home=args.home, workspace=args.workspace, timeouts=not args.skip_timeouts) else 1


if __name__ == "__main__":
    sys.exit(main())
