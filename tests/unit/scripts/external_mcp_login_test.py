import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from scripts.external_mcp_login import _pkce_pair, _write_env_value  # noqa: E402


def test_pkce_pair_is_url_safe_and_sha256_derived() -> None:
    import base64
    import hashlib

    verifier, challenge = _pkce_pair()

    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert "=" not in challenge


def test_write_env_value_replaces_value_without_touching_other_lines(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("FIRST=one\nMCP_EXTERNAL_PROXY_TOKEN=old\nLAST=three\n")
    env_file.chmod(0o640)

    _write_env_value(env_file, "MCP_EXTERNAL_PROXY_TOKEN", "new-secret")

    assert env_file.read_text() == "FIRST=one\nMCP_EXTERNAL_PROXY_TOKEN=new-secret\nLAST=three\n"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o640


def test_write_env_value_appends_to_file_without_trailing_newline(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("FIRST=one")

    _write_env_value(env_file, "MCP_EXTERNAL_PROXY_TOKEN", "new-secret")

    assert env_file.read_text() == "FIRST=one\nMCP_EXTERNAL_PROXY_TOKEN=new-secret\n"
