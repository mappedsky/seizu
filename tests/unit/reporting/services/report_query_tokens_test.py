"""Tests for reporting.services.report_query_tokens."""

import json
import time
from datetime import UTC, datetime, timedelta

import pytest

from reporting.authnz import CurrentUser
from reporting.schema.report_config import ReportVersion, User
from reporting.services import report_query_tokens as rqt

_FAKE_USER = User(
    user_id="user-1",
    sub="sub",
    iss="https://idp.example.com",
    email="u@example.com",
    created_at="2024-01-01T00:00:00+00:00",
    last_login="2024-01-01T00:00:00+00:00",
)


def _current_user(token_exp=None):
    claims = {}
    if token_exp is not None:
        claims["token_exp"] = token_exp
    return CurrentUser(user=_FAKE_USER, jwt_claims=claims, permissions=frozenset())


def _issue(mocker, current_user, **kwargs):
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    defaults = dict(
        report_id="r1",
        report_version=1,
        path="rows.0.panels.0.cypher",
        query="MATCH (n) RETURN n",
        allowed_param_names=[],
        static_params={},
    )
    defaults.update(kwargs)
    return rqt.issue_report_query_token(current_user=current_user, **defaults)


# ---------------------------------------------------------------------------
# _get_signing_secret
# ---------------------------------------------------------------------------


def test_get_signing_secret_uses_env_var(mocker):
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "my-secret")
    assert rqt._get_signing_secret() == b"my-secret"


def test_get_signing_secret_dev_fallback_when_auth_disabled(mocker):
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "")
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", False)
    secret = rqt._get_signing_secret()
    assert secret == rqt._DEV_FALLBACK_SECRET.encode("utf-8")


def test_get_signing_secret_raises_when_auth_required_and_no_secret(mocker):
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "")
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    with pytest.raises(RuntimeError, match="REPORT_QUERY_SIGNING_SECRET"):
        rqt._get_signing_secret()


# ---------------------------------------------------------------------------
# _get_token_expiry
# ---------------------------------------------------------------------------


def test_get_token_expiry_uses_jwt_exp(mocker):
    exp_dt = datetime.now(tz=UTC) + timedelta(minutes=10)
    user = _current_user(token_exp=exp_dt)
    result = rqt._get_token_expiry(user)
    assert result == int(exp_dt.timestamp())


def test_get_token_expiry_dev_fallback_when_auth_disabled(mocker):
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", False)
    user = _current_user()
    result = rqt._get_token_expiry(user)
    assert result > int(time.time())


def test_get_token_expiry_raises_when_auth_required_and_no_exp(mocker):
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    user = _current_user()
    with pytest.raises(RuntimeError, match="JWT exp claim"):
        rqt._get_token_expiry(user)


# ---------------------------------------------------------------------------
# _json_default
# ---------------------------------------------------------------------------


def test_json_default_serializes_datetime():
    dt = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    assert rqt._json_default(dt) == dt.isoformat()


def test_json_default_falls_back_to_str():
    result = rqt._json_default(object())
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# resolve_report_query_request — validation branches
# ---------------------------------------------------------------------------


def test_resolve_rejects_missing_dot(mocker):
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    with pytest.raises(ValueError, match="Invalid report query token"):
        rqt.resolve_report_query_request(token="nodot", current_user=_current_user(), params=None)


def test_resolve_rejects_wrong_kind(mocker):
    token = _issue(mocker, _current_user(token_exp=datetime.now(tz=UTC) + timedelta(minutes=10)))
    payload_bytes = rqt._b64url_decode(token.split(".")[0])
    payload = json.loads(payload_bytes)
    payload["kind"] = "wrong"
    new_encoded = rqt._b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    import hmac
    from hashlib import sha256

    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    sig = hmac.new(b"test-secret", new_encoded.encode("ascii"), sha256).digest()
    bad_token = f"{new_encoded}.{rqt._b64url_encode(sig)}"
    with pytest.raises(ValueError, match="kind"):
        rqt.resolve_report_query_request(token=bad_token, current_user=_current_user(), params=None)


def test_resolve_rejects_wrong_version(mocker):
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    import hmac
    from hashlib import sha256

    payload = {
        "v": 999,
        "kind": rqt._CAPABILITY_KIND,
        "user_id": "user-1",
        "report_id": "r1",
        "report_version": 1,
        "path": "rows.0.panels.0.cypher",
        "query": "MATCH (n) RETURN n",
        "allowed_param_names": [],
        "static_params": {},
        "exp": int(time.time()) + 600,
    }
    encoded = rqt._b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    sig = hmac.new(b"test-secret", encoded.encode("ascii"), sha256).digest()
    token = f"{encoded}.{rqt._b64url_encode(sig)}"
    with pytest.raises(ValueError, match="version"):
        rqt.resolve_report_query_request(token=token, current_user=_current_user(), params=None)


def test_resolve_rejects_wrong_user_id(mocker):
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    import hmac
    from hashlib import sha256

    payload = {
        "v": rqt._CAPABILITY_VERSION,
        "kind": rqt._CAPABILITY_KIND,
        "user_id": "other-user",
        "report_id": "r1",
        "report_version": 1,
        "path": "rows.0.panels.0.cypher",
        "query": "MATCH (n) RETURN n",
        "allowed_param_names": [],
        "static_params": {},
        "exp": int(time.time()) + 600,
    }
    encoded = rqt._b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    sig = hmac.new(b"test-secret", encoded.encode("ascii"), sha256).digest()
    token = f"{encoded}.{rqt._b64url_encode(sig)}"
    with pytest.raises(PermissionError, match="does not belong"):
        rqt.resolve_report_query_request(token=token, current_user=_current_user(), params=None)


def _make_token(mocker, overrides: dict) -> str:
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    import hmac
    from hashlib import sha256

    payload = {
        "v": rqt._CAPABILITY_VERSION,
        "kind": rqt._CAPABILITY_KIND,
        "user_id": "user-1",
        "report_id": "r1",
        "report_version": 1,
        "path": "rows.0.panels.0.cypher",
        "query": "MATCH (n) RETURN n",
        "allowed_param_names": [],
        "static_params": {},
        "exp": int(time.time()) + 600,
    }
    payload.update(overrides)
    encoded = rqt._b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    sig = hmac.new(b"test-secret", encoded.encode("ascii"), sha256).digest()
    return f"{encoded}.{rqt._b64url_encode(sig)}"


def test_resolve_rejects_invalid_exp_type(mocker):
    token = _make_token(mocker, {"exp": "not-an-int"})
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    with pytest.raises(ValueError, match="expiry"):
        rqt.resolve_report_query_request(token=token, current_user=_current_user(), params=None)


def test_resolve_rejects_missing_query(mocker):
    token = _make_token(mocker, {"query": 42})
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    with pytest.raises(ValueError, match="payload"):
        rqt.resolve_report_query_request(token=token, current_user=_current_user(), params=None)


def test_resolve_rejects_missing_report_id(mocker):
    token = _make_token(mocker, {"report_id": 42})
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    with pytest.raises(ValueError, match="payload"):
        rqt.resolve_report_query_request(token=token, current_user=_current_user(), params=None)


def test_resolve_rejects_invalid_allowed_param_names(mocker):
    token = _make_token(mocker, {"allowed_param_names": "not-a-list"})
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    with pytest.raises(ValueError, match="payload"):
        rqt.resolve_report_query_request(token=token, current_user=_current_user(), params=None)


def test_resolve_rejects_invalid_static_params(mocker):
    token = _make_token(mocker, {"static_params": "not-a-dict"})
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    with pytest.raises(ValueError, match="payload"):
        rqt.resolve_report_query_request(token=token, current_user=_current_user(), params=None)


def test_resolve_rejects_unexpected_request_params(mocker):
    token = _make_token(mocker, {"allowed_param_names": ["severity"]})
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    with pytest.raises(ValueError, match="Unexpected"):
        rqt.resolve_report_query_request(
            token=token, current_user=_current_user(), params={"severity": "HIGH", "extra": "bad"}
        )


def test_resolve_rejects_static_param_value_mismatch(mocker):
    token = _make_token(mocker, {"allowed_param_names": ["limit"], "static_params": {"limit": 10}})
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    with pytest.raises(ValueError, match="mismatch"):
        rqt.resolve_report_query_request(token=token, current_user=_current_user(), params={"limit": 99})


def test_resolve_succeeds_with_no_params(mocker):
    token = _make_token(mocker, {})
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    query, params = rqt.resolve_report_query_request(token=token, current_user=_current_user(), params=None)
    assert query == "MATCH (n) RETURN n"
    assert params == {}


# ---------------------------------------------------------------------------
# build_report_query_capabilities
# ---------------------------------------------------------------------------


def _report_version(config: dict) -> ReportVersion:
    return ReportVersion(
        report_id="r1",
        name="Test Report",
        version=1,
        config=config,
        created_at="2024-01-01T00:00:00+00:00",
        created_by="user-1",
        report_created_by="user-1",
        report_updated_by="user-1",
        access={"scope": "private"},
    )


def test_build_capabilities_returns_empty_on_invalid_config(mocker):
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", False)
    rv = _report_version({"not": "valid"})
    result = rqt.build_report_query_capabilities(rv, _current_user())
    assert result == {}


def test_build_capabilities_covers_panel_cypher(mocker):
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", False)
    config = {
        "name": "Test",
        "queries": {},
        "rows": [{"name": "Row 1", "panels": [{"type": "count", "cypher": "MATCH (n) RETURN count(n)"}]}],
    }
    rv = _report_version(config)
    user = _current_user(token_exp=datetime.now(tz=UTC) + timedelta(minutes=10))
    result = rqt.build_report_query_capabilities(rv, user)
    assert "rows.0.panels.0.cypher" in result
    assert isinstance(result["rows.0.panels.0.cypher"], str)


def test_build_capabilities_covers_details_cypher(mocker):
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", False)
    config = {
        "name": "Test",
        "queries": {},
        "rows": [
            {
                "name": "Row 1",
                "panels": [
                    {
                        "type": "table",
                        "cypher": "MATCH (n) RETURN n",
                        "details_cypher": "MATCH (n) RETURN n.details",
                    }
                ],
            }
        ],
    }
    rv = _report_version(config)
    user = _current_user(token_exp=datetime.now(tz=UTC) + timedelta(minutes=10))
    result = rqt.build_report_query_capabilities(rv, user)
    assert "rows.0.panels.0.cypher" in result
    assert "rows.0.panels.0.details_cypher" in result


def test_build_capabilities_covers_inputs(mocker):
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", False)
    config = {
        "name": "Test",
        "queries": {},
        "inputs": [
            {
                "input_id": "severity_input",
                "label": "Severity",
                "type": "autocomplete",
                "cypher": "MATCH (n) RETURN DISTINCT n.severity AS value",
            }
        ],
        "rows": [],
    }
    rv = _report_version(config)
    user = _current_user(token_exp=datetime.now(tz=UTC) + timedelta(minutes=10))
    result = rqt.build_report_query_capabilities(rv, user)
    assert "inputs.0.cypher" in result


def test_build_capabilities_skips_input_without_cypher(mocker):
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", False)
    config = {
        "name": "Test",
        "queries": {},
        "inputs": [{"input_id": "severity_input", "label": "Severity", "type": "text"}],
        "rows": [],
    }
    rv = _report_version(config)
    user = _current_user(token_exp=datetime.now(tz=UTC) + timedelta(minutes=10))
    result = rqt.build_report_query_capabilities(rv, user)
    assert result == {}
