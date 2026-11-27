"""The conformance checker against the reference fake image.

Run both ways: a conforming image passes every check, and an image that breaks
one rule fails, so the checker cannot pass vacuously.
"""

import io
from pathlib import Path
from typing import Any

import pytest

from scripts import sandbox_image_conformance as conformance
from tests.unit.reporting.services.sandbox_protocol_fake import FakeSandboxImage

# The fake image listens on loopback; nothing leaves the machine.
pytestmark = pytest.mark.allow_network


def _run(image: FakeSandboxImage, **kwargs: Any) -> tuple[bool, str]:
    out = io.StringIO()
    with image.serve() as url:
        passed = conformance.run(url, home="/home/user", workspace=str(image.mount.resolve()), out=out, **kwargs)
    return passed, out.getvalue()


def test_the_reference_image_conforms(tmp_path: Path) -> None:
    passed, report = _run(FakeSandboxImage(tmp_path))
    assert passed, report
    assert "FAIL" not in report


def test_an_image_that_overwrites_the_marker_fails(tmp_path: Path) -> None:
    class Overwriting(FakeSandboxImage):
        def _op_hello(self, args: dict[str, Any], timeout: int) -> dict[str, Any]:
            if args.get("create_marker") and self._marker_path().exists():
                self._marker_path().unlink()
            return super()._op_hello(args, timeout)

    passed, report = _run(Overwriting(tmp_path), timeouts=False)
    assert not passed
    assert "FAIL  marker is written once and read back" in report


def test_an_image_that_forgets_interpreter_state_fails(tmp_path: Path) -> None:
    class Forgetful(FakeSandboxImage):
        def _op_run_python(self, args: dict[str, Any], timeout: int) -> dict[str, Any]:
            result = super()._op_run_python(args, timeout)
            self.stop()
            return result

    passed, report = _run(Forgetful(tmp_path), timeouts=False)
    assert not passed
    assert "FAIL  python output and persistent state" in report


def test_main_returns_nonzero_when_unreachable() -> None:
    assert conformance.main(["--url", "http://127.0.0.1:9", "--skip-timeouts"]) == 1
