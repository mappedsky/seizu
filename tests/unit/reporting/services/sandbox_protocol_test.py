"""The seizu-sandbox/1 protocol models and their published JSON Schema."""

import json
from pathlib import Path

from reporting.services import sandbox_protocol
from reporting.services.sandbox_protocol import OPS, HelloResult, Op, SandboxRequest, SandboxResponse

_SCHEMA_FILE = Path(__file__).resolve().parents[4] / "schema" / "sandbox-protocol-v1.json"


def test_the_checked_in_schema_matches_the_models() -> None:
    """Image repositories build against the file; `make schema` regenerates it."""
    assert json.loads(_SCHEMA_FILE.read_text()) == sandbox_protocol.json_schema()


def test_every_op_has_argument_and_result_models() -> None:
    assert set(OPS) == set(Op)
    assert set(sandbox_protocol.json_schema()["x-seizu-ops"]) == {op.value for op in Op}


def test_request_carries_the_protocol_id() -> None:
    request = SandboxRequest(op=Op.HELLO, timeout_seconds=5)
    assert json.loads(request.model_dump_json())["protocol"] == "seizu-sandbox/1"


def test_responses_tolerate_fields_added_later() -> None:
    """Additive changes stay within v1, so unknown fields must not break parsing."""
    response = SandboxResponse.model_validate({"ok": True, "result": {"x": 1}, "trace": "abc"})
    assert response.ok
    hello = HelloResult.model_validate(
        {"protocol_versions": [1, 2], "home": "/home/user", "marker": None, "future_field": True}
    )
    assert hello.storage_semantics == "posix"
