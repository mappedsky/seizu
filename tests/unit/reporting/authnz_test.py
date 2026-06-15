import base64
from unittest.mock import AsyncMock

import jwt
import pytest
from jwt.api_jwk import PyJWK

import reporting.authnz as authnz_module
from reporting.authnz import CurrentUser, _get_jwt_payload, get_current_user

# JWK that matches the private key
FAKE_KEY = {
    "kty": "EC",
    "use": "sig",
    "crv": "P-256",
    "kid": "-1gFFsnhuTW7Ym6yznOcZrpRiXiLQZ3NB6SvF6Wq1Eg",
    "x": "jCRyJB_B2VGnPTP6eHowt0W0OO6L4PUJrBiuQXIcnRY",
    "y": "6Tlze188qIGiwksLOMrzlV_OzSjvYM5kAEDC7rIFzBY",
    "alg": "ES256",
}

# This key is technically valid for encoding JWTs, but it's been generated only for development, and
# isn't used elsewhere.
FAKE_PRIVATE_KEY = (
    "LS0tLS1CRUdJTiBQUklWQVRFIEtFWS0tLS0tCk1FRUNBUUF3RXdZSEtvWkl6ajBDQVFZSUtvWkl6ajBEQVFjRUp6QWxBZ0VCQkNDb0dLd"
    "C9ET0VkQU4xS2xvQ3EKeG5VU3RZV0FBd3c0dnpacWtHaGtWQ2dyY3c9PQotLS0tLUVORCBQUklWQVRFIEtFWS0tLS0t"
)


def _make_mock_signing_key(mocker):
    """Return a mock signing key backed by the fake EC public key."""
    signing_key = mocker.MagicMock()
    signing_key.key = PyJWK(FAKE_KEY).key
    return signing_key


def _make_mock_client(mocker, signing_key):
    """Return a mock PyJWKClient that returns the given signing key."""
    mock_client = mocker.MagicMock()
    mock_client.get_signing_key_from_jwt.return_value = signing_key
    return mock_client


@pytest.fixture(autouse=True)
def reset_jwks_client():
    original = authnz_module._jwks_client
    authnz_module._jwks_client = None
    yield
    authnz_module._jwks_client = original


async def test__get_jwt_payload_valid_token(mocker):
    """_get_jwt_payload decodes a valid JWT token."""
    mocker.patch("reporting.settings.JWT_AUDIENCE", "")
    signing_key = _make_mock_signing_key(mocker)
    mock_client = _make_mock_client(mocker, signing_key)
    mocker.patch("reporting.authnz._get_jwks_client", return_value=mock_client)
    encoded = jwt.encode(
        {"email": "test@example.com"},
        base64.b64decode(FAKE_PRIVATE_KEY),
        algorithm="ES256",
    )
    payload = await _get_jwt_payload(encoded)
    assert payload == {"email": "test@example.com"}


async def test__get_jwt_payload_with_audience(mocker):
    """JWT audience claim is accepted when JWT_AUDIENCE matches."""
    mocker.patch("reporting.settings.JWT_AUDIENCE", "myapp")
    signing_key = _make_mock_signing_key(mocker)
    mock_client = _make_mock_client(mocker, signing_key)
    mocker.patch("reporting.authnz._get_jwks_client", return_value=mock_client)
    encoded = jwt.encode(
        {"email": "test@example.com", "aud": "myapp"},
        base64.b64decode(FAKE_PRIVATE_KEY),
        algorithm="ES256",
    )
    payload = await _get_jwt_payload(encoded)
    assert payload["email"] == "test@example.com"


async def test__get_jwt_payload_audience_mismatch(mocker):
    """JWT with wrong audience claim is rejected."""
    mocker.patch("reporting.settings.JWT_AUDIENCE", "myapp")
    signing_key = _make_mock_signing_key(mocker)
    mock_client = _make_mock_client(mocker, signing_key)
    mocker.patch("reporting.authnz._get_jwks_client", return_value=mock_client)
    encoded = jwt.encode(
        {"email": "test@example.com", "aud": "other-app"},
        base64.b64decode(FAKE_PRIVATE_KEY),
        algorithm="ES256",
    )
    with pytest.raises(jwt.exceptions.InvalidAudienceError):
        await _get_jwt_payload(encoded)


async def test__get_jwt_payload_aud_in_token_but_not_configured(mocker):
    """Token with aud claim fails when JWT_AUDIENCE is not set (PyJWT requirement)."""
    mocker.patch("reporting.settings.JWT_AUDIENCE", "")
    signing_key = _make_mock_signing_key(mocker)
    mock_client = _make_mock_client(mocker, signing_key)
    mocker.patch("reporting.authnz._get_jwks_client", return_value=mock_client)
    encoded = jwt.encode(
        {"email": "test@example.com", "aud": "myapp"},
        base64.b64decode(FAKE_PRIVATE_KEY),
        algorithm="ES256",
    )
    with pytest.raises(jwt.exceptions.InvalidAudienceError):
        await _get_jwt_payload(encoded)


def test__get_jwks_client_uses_configured_timeout(mocker):
    mock_client = mocker.MagicMock()
    jwk_ctor = mocker.patch("reporting.authnz.PyJWKClient", return_value=mock_client)
    mocker.patch("reporting.settings.JWKS_URL", "https://idp.example.com/jwks")
    mocker.patch("reporting.settings.JWKS_FETCH_TIMEOUT", 17)

    from reporting.authnz import _get_jwks_client

    assert _get_jwks_client() == mock_client
    jwk_ctor.assert_called_once_with(
        "https://idp.example.com/jwks",
        cache_keys=True,
        timeout=17,
    )


async def test_validate_bearer_token_returns_jwt_payload_on_success(mocker):
    """A verifiable JWT short-circuits — introspection is never consulted."""
    from reporting.authnz import validate_bearer_token

    mocker.patch("reporting.authnz._get_jwt_payload", new=AsyncMock(return_value={"sub": "s1"}))
    introspect = mocker.patch("reporting.services.oauth_client.introspect_token", new=AsyncMock())

    payload = await validate_bearer_token("tok")

    assert payload == {"sub": "s1"}
    introspect.assert_not_called()


async def test_validate_bearer_token_reraises_when_introspection_disabled(mocker):
    """A non-JWT token is rejected outright when introspection is off."""
    from reporting.authnz import validate_bearer_token

    mocker.patch("reporting.settings.OIDC_ENABLE_TOKEN_INTROSPECTION", False)
    mocker.patch(
        "reporting.authnz._get_jwt_payload",
        new=AsyncMock(side_effect=jwt.DecodeError("not a jwt")),
    )
    introspect = mocker.patch("reporting.services.oauth_client.introspect_token", new=AsyncMock())

    with pytest.raises(jwt.PyJWTError):
        await validate_bearer_token("opaque")
    introspect.assert_not_called()


async def test_validate_bearer_token_falls_back_to_introspection(mocker):
    """An opaque token is validated via RFC 7662 introspection when enabled."""
    from reporting.authnz import validate_bearer_token

    mocker.patch("reporting.settings.OIDC_ENABLE_TOKEN_INTROSPECTION", True)
    mocker.patch(
        "reporting.authnz._get_jwt_payload",
        new=AsyncMock(side_effect=jwt.DecodeError("not a jwt")),
    )
    mocker.patch(
        "reporting.services.oauth_client.introspect_token",
        new=AsyncMock(return_value={"active": True, "sub": "opaque-sub", "email": "u@example.com"}),
    )

    payload = await validate_bearer_token("opaque")

    assert payload["sub"] == "opaque-sub"


async def test_validate_bearer_token_introspection_failure_raises_invalid_token(mocker):
    """Introspection rejection surfaces as a PyJWTError so callers handle it uniformly."""
    from reporting.authnz import validate_bearer_token
    from reporting.services import oauth_client

    mocker.patch("reporting.settings.OIDC_ENABLE_TOKEN_INTROSPECTION", True)
    mocker.patch(
        "reporting.authnz._get_jwt_payload",
        new=AsyncMock(side_effect=jwt.DecodeError("not a jwt")),
    )
    mocker.patch(
        "reporting.services.oauth_client.introspect_token",
        new=AsyncMock(side_effect=oauth_client.OAuthClientError("token inactive")),
    )

    with pytest.raises(jwt.PyJWTError):
        await validate_bearer_token("opaque")


async def test_get_current_user_auth_disabled(mocker):
    """In dev mode (auth disabled) a synthetic dev user is returned without a token."""
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", False)
    mocker.patch(
        "reporting.settings.DEVELOPMENT_ONLY_AUTH_USER_EMAIL",
        "devuser@example.com",
    )
    from reporting.schema.report_config import User

    fake_user = User(
        user_id="dev-uid",
        sub="devuser@example.com",
        iss="dev",
        email="devuser@example.com",
        created_at="2024-01-01T00:00:00+00:00",
        last_login="2024-01-01T00:00:00+00:00",
    )
    mock_get_or_create = mocker.patch(
        "reporting.services.report_store.get_or_create_user",
        new=AsyncMock(return_value=fake_user),
    )
    result = await get_current_user(credentials=None)
    assert isinstance(result, CurrentUser)
    assert result.user.email == "devuser@example.com"
    mock_get_or_create.assert_called_once_with(
        sub="devuser@example.com",
        iss="dev",
        email="devuser@example.com",
        display_name=None,
        preferred_username=None,
    )


async def test_get_current_user_no_credentials_raises(mocker):
    """When auth is required but no credentials provided, 401 is raised."""
    from fastapi import HTTPException

    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=None)
    assert exc_info.value.status_code == 401


async def test_get_current_user_extracts_sub_and_iss(mocker):
    """get_current_user extracts sub and iss from JWT and calls get_or_create_user."""
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    mocker.patch("reporting.settings.JWT_AUDIENCE", "")
    signing_key = _make_mock_signing_key(mocker)
    mock_client = _make_mock_client(mocker, signing_key)
    mocker.patch("reporting.authnz._get_jwks_client", return_value=mock_client)

    from fastapi.security import HTTPAuthorizationCredentials

    from reporting.schema.report_config import User

    fake_user = User(
        user_id="uid1",
        sub="sub123",
        iss="https://idp.example.com",
        email="alice@example.com",
        display_name="Alice",
        preferred_username="alice",
        created_at="2024-01-01T00:00:00+00:00",
        last_login="2024-01-01T00:00:00+00:00",
    )
    mock_get_or_create = mocker.patch(
        "reporting.services.report_store.get_or_create_user",
        new=AsyncMock(return_value=fake_user),
    )

    encoded = jwt.encode(
        {
            "email": "alice@example.com",
            "sub": "sub123",
            "iss": "https://idp.example.com",
            "name": "Alice",
            "preferred_username": "alice",
            "iat": 1704067200,
        },
        base64.b64decode(FAKE_PRIVATE_KEY),
        algorithm="ES256",
    )
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=encoded)
    result = await get_current_user(credentials=credentials)
    mock_get_or_create.assert_called_once_with(
        sub="sub123",
        iss="https://idp.example.com",
        email="alice@example.com",
        display_name="Alice",
        preferred_username="alice",
        role=None,
    )
    assert result.user.user_id == "uid1"


async def test_get_current_user_allows_missing_email(mocker):
    """Email is optional profile data; durable identity is still iss + sub."""
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    mocker.patch("reporting.settings.JWT_AUDIENCE", "")
    signing_key = _make_mock_signing_key(mocker)
    mock_client = _make_mock_client(mocker, signing_key)
    mocker.patch("reporting.authnz._get_jwks_client", return_value=mock_client)

    from fastapi.security import HTTPAuthorizationCredentials

    from reporting.schema.report_config import User

    fake_user = User(
        user_id="uid1",
        sub="sub123",
        iss="https://idp.example.com",
        email=None,
        display_name=None,
        preferred_username="alice",
        created_at="2024-01-01T00:00:00+00:00",
        last_login="2024-01-01T00:00:00+00:00",
    )
    mock_get_or_create = mocker.patch(
        "reporting.services.report_store.get_or_create_user",
        new=AsyncMock(return_value=fake_user),
    )

    encoded = jwt.encode(
        {
            "sub": "sub123",
            "iss": "https://idp.example.com",
            "preferred_username": "alice",
            "iat": 1704067200,
        },
        base64.b64decode(FAKE_PRIVATE_KEY),
        algorithm="ES256",
    )
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=encoded)
    result = await get_current_user(credentials=credentials)

    mock_get_or_create.assert_called_once_with(
        sub="sub123",
        iss="https://idp.example.com",
        email=None,
        display_name=None,
        preferred_username="alice",
        role=None,
    )
    assert result.user.email is None


async def test_get_current_user_rejects_missing_subject_claim(mocker):
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    mocker.patch("reporting.settings.JWT_AUDIENCE", "")
    signing_key = _make_mock_signing_key(mocker)
    mock_client = _make_mock_client(mocker, signing_key)
    mocker.patch("reporting.authnz._get_jwks_client", return_value=mock_client)
    get_or_create = mocker.patch("reporting.services.report_store.get_or_create_user", new=AsyncMock())

    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    encoded = jwt.encode(
        {
            "iss": "https://idp.example.com",
            "iat": 1704067200,
        },
        base64.b64decode(FAKE_PRIVATE_KEY),
        algorithm="ES256",
    )
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=encoded)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=credentials)

    assert exc_info.value.status_code == 401
    get_or_create.assert_not_called()


async def test_get_current_user_rejects_missing_issuer_claim(mocker):
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    mocker.patch("reporting.settings.JWT_AUDIENCE", "")
    signing_key = _make_mock_signing_key(mocker)
    mock_client = _make_mock_client(mocker, signing_key)
    mocker.patch("reporting.authnz._get_jwks_client", return_value=mock_client)
    get_or_create = mocker.patch("reporting.services.report_store.get_or_create_user", new=AsyncMock())

    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    encoded = jwt.encode(
        {
            "sub": "sub123",
            "iat": 1704067200,
        },
        base64.b64decode(FAKE_PRIVATE_KEY),
        algorithm="ES256",
    )
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=encoded)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=credentials)

    assert exc_info.value.status_code == 401
    get_or_create.assert_not_called()


async def test_get_current_user_resolves_permissions_from_role_claim(mocker):
    """Permissions are resolved from the RBAC_ROLE_CLAIM in the JWT payload."""
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    mocker.patch("reporting.settings.JWT_AUDIENCE", "")
    mocker.patch("reporting.settings.RBAC_ROLE_CLAIM", "seizu_role")
    mocker.patch("reporting.settings.RBAC_DEFAULT_ROLE", "seizu-viewer")
    signing_key = _make_mock_signing_key(mocker)
    mock_client = _make_mock_client(mocker, signing_key)
    mocker.patch("reporting.authnz._get_jwks_client", return_value=mock_client)

    from fastapi.security import HTTPAuthorizationCredentials

    from reporting.schema.report_config import User

    fake_user = User(
        user_id="uid1",
        sub="sub123",
        iss="https://idp.example.com",
        email="alice@example.com",
        created_at="2024-01-01T00:00:00+00:00",
        last_login="2024-01-01T00:00:00+00:00",
    )
    mock_get_or_create = mocker.patch(
        "reporting.services.report_store.get_or_create_user",
        new=AsyncMock(return_value=fake_user),
    )

    encoded = jwt.encode(
        {
            "email": "alice@example.com",
            "sub": "sub123",
            "iss": "https://idp.example.com",
            "seizu_role": "seizu-admin",
            "iat": 1704067200,
        },
        base64.b64decode(FAKE_PRIVATE_KEY),
        algorithm="ES256",
    )
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=encoded)
    result = await get_current_user(credentials=credentials)
    assert "toolsets:write" in result.permissions
    assert "reports:read" in result.permissions
    # The observed role claim is synced to the user profile so headless
    # callers (Temporal workflows) can resolve permissions later.
    assert mock_get_or_create.call_args.kwargs["role"] == "seizu-admin"


async def test_get_current_user_dev_mode_has_all_permissions(mocker):
    """Dev mode (auth disabled) returns ALL_PERMISSIONS."""
    from reporting.authnz.permissions import ALL_PERMISSIONS

    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", False)
    mocker.patch(
        "reporting.settings.DEVELOPMENT_ONLY_AUTH_USER_EMAIL",
        "devuser@example.com",
    )
    from reporting.schema.report_config import User

    fake_user = User(
        user_id="dev-uid",
        sub="devuser@example.com",
        iss="dev",
        email="devuser@example.com",
        created_at="2024-01-01T00:00:00+00:00",
        last_login="2024-01-01T00:00:00+00:00",
    )
    mocker.patch(
        "reporting.services.report_store.get_or_create_user",
        new=AsyncMock(return_value=fake_user),
    )
    result = await get_current_user(credentials=None)
    assert result.permissions == ALL_PERMISSIONS
