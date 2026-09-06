from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from hypothesis import given, settings
from hypothesis import strategies as st

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.report_config import User
from reporting.services.query_validator import ValidationResult

_FAKE_USER = User(
    user_id="test-user-id",
    sub="sub123",
    iss="https://idp.example.com",
    email="test@example.com",
    created_at="2024-01-01T00:00:00+00:00",
    last_login="2024-01-01T00:00:00+00:00",
)

_FAKE_CURRENT_USER = CurrentUser(user=_FAKE_USER, jwt_claims={}, permissions=ALL_PERMISSIONS)

json_scalar = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-1000, max_value=1000),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=30),
)
json_value = st.recursive(
    json_scalar,
    lambda children: st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=12), children, max_size=4),
    max_leaves=10,
)


async def _post_validate(payload: dict[str, object]) -> int:
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _FAKE_CURRENT_USER
    with patch(
        "reporting.routes.validate.validate_query",
        new=AsyncMock(return_value=ValidationResult()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/validate", json=payload)
    return response.status_code


@settings(max_examples=50, deadline=None)
@given(
    query=st.one_of(st.text(max_size=120), json_value),
    params=st.one_of(st.none(), st.dictionaries(st.text(max_size=12), json_value, max_size=4), json_value),
)
async def test_validate_endpoint_fuzzed_json_never_500s(query: object, params: object) -> None:
    payload = {"query": query, "params": params}

    status_code = await _post_validate(payload)

    assert status_code < 500
