"""A reference ``seizu-sandbox/1`` image, in-process, for tests.

:class:`FakeSandboxImage` implements every protocol operation over a temporary
directory laid out like the real image (a workspace directory plus a ``home``
symlink into it), with a persistent Python kernel subprocess so interpreter
state, timeouts and restarts behave as the protocol requires.
:class:`FakeAgentCore` stands in for the ``bedrock-agentcore`` boto client:
one image per session id, ``StopRuntimeSession`` ending the kernel but keeping
the files -- the AgentCore semantics the backend relies on.

``serve()`` exposes an image over HTTP so the conformance checker can be tested
against it.
"""

import base64
import io
import json
import os
import select
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from reporting.services.sandbox_protocol import HOME, MARKER_FILENAME, PROTOCOL, ErrorCode, Op

_KERNEL = r"""
import ast, contextlib, io, json, sys, traceback
ns = {"__name__": "__main__"}
for line in sys.stdin:
    code = json.loads(line)["code"]
    out, err, text, error = io.StringIO(), io.StringIO(), "", None
    try:
        tree = ast.parse(code)
        last = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last = ast.Expression(tree.body.pop().value)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(compile(tree, "<cell>", "exec"), ns)
            if last is not None:
                value = eval(compile(last, "<cell>", "eval"), ns)
                if value is not None:
                    text = repr(value)
    except BaseException as exc:
        error = {"name": type(exc).__name__, "value": str(exc), "traceback": traceback.format_exc()}
    reply = {"stdout": out.getvalue(), "stderr": err.getvalue(), "text": text, "error": error}
    sys.__stdout__.write(json.dumps(reply) + "\n")
    sys.__stdout__.flush()
"""


class _OpFailed(Exception):
    def __init__(self, code: ErrorCode, message: str = "") -> None:
        super().__init__(message)
        self.code = code


class FakeSandboxImage:
    def __init__(
        self,
        root: Path,
        *,
        storage_semantics: str = "posix",
        protocol_versions: tuple[int, ...] = (1,),
        write_marker: bool = True,
    ) -> None:
        self.mount = root / "mnt" / "workspace"
        self.mount.mkdir(parents=True, exist_ok=True)
        self.home = root / "home"
        if not self.home.exists():
            self.home.symlink_to(self.mount)
        self.storage_semantics = storage_semantics
        self.protocol_versions = list(protocol_versions)
        self.write_marker = write_marker
        self.ops: list[str] = []
        self._kernel: subprocess.Popen[str] | None = None
        self._kernel_lock = threading.Lock()
        self._busy = 0
        self._busy_lock = threading.Lock()
        self._busy_changed = False

    # -- paths ---------------------------------------------------------------

    def real(self, path: str) -> Path:
        """Map the image's ``/home/user/...`` onto this fake's directory."""
        if path == HOME or path.startswith(f"{HOME}/"):
            return self.home / path[len(HOME) :].lstrip("/")
        return Path(path)

    # -- lifecycle -----------------------------------------------------------

    def stop(self) -> None:
        """End the kernel (as a stopped session would); files stay."""
        with self._kernel_lock:
            self._kill_kernel()

    def _kill_kernel(self) -> None:
        if self._kernel is not None:
            self._kernel.kill()
            self._kernel.wait()
            for pipe in (self._kernel.stdin, self._kernel.stdout):
                if pipe is not None:
                    pipe.close()
            self._kernel = None

    def _ensure_kernel(self) -> subprocess.Popen[str]:
        if self._kernel is None or self._kernel.poll() is not None:
            self._kernel = subprocess.Popen(
                [sys.executable, "-c", _KERNEL],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                cwd=self.home,
            )
        return self._kernel

    # -- protocol ------------------------------------------------------------

    def ping(self) -> dict[str, Any]:
        with self._busy_lock:
            return {"status": "HealthyBusy" if self._busy else "Healthy"}

    def handle(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._busy_lock:
            self._busy += 1
        try:
            if body.get("protocol") != PROTOCOL:
                raise _OpFailed(ErrorCode.UNSUPPORTED_PROTOCOL, str(body.get("protocol")))
            try:
                op = Op(body.get("op"))
            except ValueError:
                raise _OpFailed(ErrorCode.UNSUPPORTED_OP, str(body.get("op"))) from None
            self.ops.append(op.value)
            args = body.get("args") or {}
            timeout = int(body.get("timeout_seconds") or 30)
            result = getattr(self, f"_op_{op.value}")(args, timeout)
            return {"ok": True, "result": result}
        except _OpFailed as exc:
            return {"ok": False, "error": {"code": exc.code.value, "message": str(exc)}}
        except (KeyError, TypeError) as exc:
            return {"ok": False, "error": {"code": ErrorCode.INVALID_ARGS.value, "message": str(exc)}}
        finally:
            with self._busy_lock:
                self._busy -= 1

    def _marker_path(self) -> Path:
        return self.mount / MARKER_FILENAME

    def _op_hello(self, args: dict[str, Any], timeout: int) -> dict[str, Any]:
        marker_path = self._marker_path()
        if args.get("create_marker") and self.write_marker and not marker_path.exists():
            marker_path.write_text(json.dumps(args["create_marker"]))
        marker = json.loads(marker_path.read_text()) if marker_path.exists() else None
        return {
            "protocol_versions": self.protocol_versions,
            "capabilities": [],
            "home": HOME,
            "storage_semantics": self.storage_semantics,
            "marker": marker,
        }

    def _op_run_python(self, args: dict[str, Any], timeout: int) -> dict[str, Any]:
        code = args["code"]
        with self._kernel_lock:
            kernel = self._ensure_kernel()
            assert kernel.stdin is not None and kernel.stdout is not None
            kernel.stdin.write(json.dumps({"code": code}) + "\n")
            kernel.stdin.flush()
            ready, _, _ = select.select([kernel.stdout], [], [], timeout)
            if not ready:
                self._kill_kernel()
                return {"restarted": True}
            line = kernel.stdout.readline()
        if not line:
            with self._kernel_lock:
                self._kill_kernel()
            return {"restarted": True, "error": {"name": "KernelDied", "value": "", "traceback": ""}}
        return dict(json.loads(line))

    def _op_run_bash(self, args: dict[str, Any], timeout: int) -> dict[str, Any]:
        cwd = self.real(args["cwd"]) if args.get("cwd") else self.home
        env = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
        try:
            done = subprocess.run(
                ["bash", "-c", args["command"]], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            return {"stdout": out, "stderr": "", "exit_code": -1, "timed_out": True}
        return {"stdout": done.stdout, "stderr": done.stderr, "exit_code": done.returncode, "timed_out": False}

    def _op_read_file(self, args: dict[str, Any], timeout: int) -> dict[str, Any]:
        path = self.real(args["path"])
        if not path.is_file():
            raise _OpFailed(ErrorCode.NOT_FOUND, args["path"])
        data = path.read_bytes()
        return {"content_b64": base64.b64encode(data).decode(), "size": len(data)}

    def _op_write_file(self, args: dict[str, Any], timeout: int) -> dict[str, Any]:
        path = self.real(args["path"])
        data = base64.b64decode(args["content_b64"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"size": len(data)}

    def _op_list_files(self, args: dict[str, Any], timeout: int) -> dict[str, Any]:
        path = self.real(args["path"])
        if not path.is_dir():
            raise _OpFailed(ErrorCode.NOT_FOUND, args["path"])
        entries = [{"name": p.name, "type": "dir" if p.is_dir() else "file"} for p in sorted(path.iterdir())]
        return {"entries": entries}

    def _op_wipe(self, args: dict[str, Any], timeout: int) -> dict[str, Any]:
        removed = 0
        for child in list(self.mount.iterdir()):
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed += 1
        return {"removed": removed}

    # -- HTTP ----------------------------------------------------------------

    @contextmanager
    def serve(self) -> Iterator[str]:
        image = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                pass

            def _send(self, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/ping":
                    self._send(image.ping())
                else:
                    self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/invocations":
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length") or 0)
                self._send(image.handle(json.loads(self.rfile.read(length) or b"{}")))

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            server.server_close()
            self.stop()


class _Body:
    def __init__(self, data: bytes) -> None:
        self._stream = io.BytesIO(data)

    def read(self) -> bytes:
        return self._stream.read()


class FakeAgentCore:
    """Stand-in for the ``bedrock-agentcore`` data-plane client.

    One :class:`FakeSandboxImage` per session id. ``stopped`` records every
    StopRuntimeSession; ``invocations`` every (session id, op) pair.
    """

    def __init__(self, root: Path, **image_kwargs: Any) -> None:
        self.root = root
        self.image_kwargs = image_kwargs
        self.images: dict[str, FakeSandboxImage] = {}
        self.stopped: list[str] = []
        self.invocations: list[tuple[str, str]] = []
        self.conflicts_before_ready: int = 0

    def image(self, session_id: str) -> FakeSandboxImage:
        if session_id not in self.images:
            self.images[session_id] = FakeSandboxImage(self.root / session_id, **self.image_kwargs)
        return self.images[session_id]

    def invoke_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
        from botocore.exceptions import ClientError

        session_id = kwargs["runtimeSessionId"]
        body = json.loads(kwargs["payload"])
        if self.conflicts_before_ready > 0:
            self.conflicts_before_ready -= 1
            raise ClientError(
                {"Error": {"Code": "RetryableConflictException", "Message": "provisioning"}}, "InvokeAgentRuntime"
            )
        self.invocations.append((session_id, str(body.get("op"))))
        response = self.image(session_id).handle(body)
        return {"response": _Body(json.dumps(response).encode()), "runtimeSessionId": session_id}

    def stop_runtime_session(self, **kwargs: Any) -> dict[str, Any]:
        session_id = kwargs["runtimeSessionId"]
        self.stopped.append(session_id)
        if session_id in self.images:
            self.images[session_id].stop()
        return {}

    def lose_storage(self, session_id: str) -> None:
        """Simulate the provider erasing a session's workspace (expiry, runtime update)."""
        image = self.image(session_id)
        image.stop()
        image._op_wipe({}, 30)

    def close(self) -> None:
        for image in self.images.values():
            image.stop()
