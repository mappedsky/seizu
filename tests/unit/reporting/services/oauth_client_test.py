import base64
import logging
import time

import httpx
import jwt
import pytest
from jwt.api_jwk import PyJWK

from reporting.services import oauth_client
from reporting.services.oauth_client import rewrite_to_external_origin

# EC P-256 keypair used to sign/verify ID tokens in tests (dev-only key).
_ID_TOKEN_JWK = {
    "kty": "EC",
    "use": "sig",
    "crv": "P-256",
    "kid": "-1gFFsnhuTW7Ym6yznOcZrpRiXiLQZ3NB6SvF6Wq1Eg",
    "x": "jCRyJB_B2VGnPTP6eHowt0W0OO6L4PUJrBiuQXIcnRY",
    "y": "6Tlze188qIGiwksLOMrzlV_OzSjvYM5kAEDC7rIFzBY",
    "alg": "ES256",
}
_ID_TOKEN_PRIVATE_KEY = (
    "LS0tLS1CRUdJTiBQUklWQVRFIEtFWS0tLS0tCk1FRUNBUUF3RXdZSEtvWkl6ajBDQVFZSUtvWkl6ajBEQVFjRUp6QWxBZ0VCQkNDb0dLd"
    "C9ET0VkQU4xS2xvQ3EKeG5VU3RZV0FBd3c0dnpacWtHaGtWQ2dyY3c9PQotLS0tLUVORCBQUklWQVRFIEtFWS0tLS0t"
)


def _sign_id_token(**claims) -> str:
    return jwt.encode(claims, base64.b64decode(_ID_TOKEN_PRIVATE_KEY), algorithm="ES256")


def _patch_idtoken_jwks(mocker):
    """Patch the ID-token JWKS client so real signature verification runs against the fake key."""
    signing_key = mocker.MagicMock()
    signing_key.key = PyJWK(_ID_TOKEN_JWK).key
    fake_client = mocker.MagicMock()
    fake_client.get_signing_key_from_jwt.return_value = signing_key
    mocker.patch("reporting.services.oauth_client._get_idtoken_jwks_client", return_value=fake_client)


@pytest.fixture(autouse=True)
def _split_authority_env(mocker):
    """The browser-vs-server hostname split docker-dev runs with."""
    oauth_client.reset_metadata_cache()
    mocker.patch("reporting.settings.OIDC_AUTHORITY", "http://localhost:9000/application/o/seizu")
    mocker.patch(
        "reporting.settings.OIDC_INTERNAL_AUTHORITY",
        "http://authentik-server:9000/application/o/seizu",
    )
    mocker.patch("reporting.settings.OIDC_CLIENT_ID", "seizu")
    mocker.patch("reporting.settings.OIDC_CLIENT_SECRET", "")
    mocker.patch("reporting.settings.OIDC_SCOPE", "openid email offline_access")
    mocker.patch("reporting.settings.OIDC_TOKEN_ENDPOINT_AUTH_METHOD", "none")
    mocker.patch("reporting.settings.OIDC_REVOCATION_ENDPOINT_AUTH_METHOD", "none")
    yield
    oauth_client.reset_metadata_cache()


async def test_rewrites_internal_origin_to_external():
    out = rewrite_to_external_origin("http://authentik-server:9000/application/o/authorize/?x=1")
    assert out == "http://localhost:9000/application/o/authorize/?x=1"


async def test_leaves_external_origin_untouched():
    out = rewrite_to_external_origin("http://localhost:9000/application/o/authorize/?x=1")
    assert out == "http://localhost:9000/application/o/authorize/?x=1"


async def test_leaves_unrelated_origin_untouched():
    # Some IDP-returned URL from a third origin (shouldn't happen with sane
    # Authentik config, but be conservative and not rewrite something we
    # don't recognize).
    out = rewrite_to_external_origin("http://other.example.com/oauth/authorize")
    assert out == "http://other.example.com/oauth/authorize"


async def test_no_rewrite_when_internal_unset(mocker):
    mocker.patch("reporting.settings.OIDC_INTERNAL_AUTHORITY", "")
    out = rewrite_to_external_origin("http://localhost:9000/authorize")
    assert out == "http://localhost:9000/authorize"


async def test_no_rewrite_when_internal_equals_external(mocker):
    mocker.patch("reporting.settings.OIDC_INTERNAL_AUTHORITY", "http://localhost:9000")
    mocker.patch("reporting.settings.OIDC_AUTHORITY", "http://localhost:9000")
    out = rewrite_to_external_origin("http://localhost:9000/authorize")
    assert out == "http://localhost:9000/authorize"


async def test_revoke_refresh_token_posts_to_revocation_endpoint(mocker):
    metadata = oauth_client.OIDCMetadata(
        issuer="http://idp.test",
        authorization_endpoint="http://idp.test/authorize",
        token_endpoint="http://idp.test/token",
        end_session_endpoint="http://idp.test/end-session",
        revocation_endpoint="http://idp.test/revoke",
        introspection_endpoint="http://idp.test/introspect",
        jwks_uri="http://idp.test/jwks",
    )
    mocker.patch("reporting.services.oauth_client.get_metadata", mocker.AsyncMock(return_value=metadata))
    calls = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeOAuthClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def revoke_token(self, url, token, token_type_hint, body):
            calls.append((url, token, token_type_hint, body))
            return FakeResponse()

    mocker.patch("reporting.services.oauth_client._build_oauth_client", return_value=FakeOAuthClient())

    await oauth_client.revoke_refresh_token(refresh_token="rt-active")

    assert calls == [
        (
            "http://idp.test/revoke",
            "rt-active",
            "refresh_token",
            "client_id=seizu",
        )
    ]


async def test_revoke_refresh_token_omits_public_client_body_for_confidential_revocation(mocker):
    mocker.patch("reporting.settings.OIDC_REVOCATION_ENDPOINT_AUTH_METHOD", "client_secret_basic")
    metadata = oauth_client.OIDCMetadata(
        issuer="http://idp.test",
        authorization_endpoint="http://idp.test/authorize",
        token_endpoint="http://idp.test/token",
        end_session_endpoint="http://idp.test/end-session",
        revocation_endpoint="http://idp.test/revoke",
        introspection_endpoint="http://idp.test/introspect",
        jwks_uri="http://idp.test/jwks",
    )
    mocker.patch("reporting.services.oauth_client.get_metadata", mocker.AsyncMock(return_value=metadata))

    calls = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeOAuthClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def revoke_token(self, url, token, token_type_hint, body):
            calls.append((url, token, token_type_hint, body))
            return FakeResponse()

    mocker.patch("reporting.services.oauth_client._build_oauth_client", return_value=FakeOAuthClient())

    await oauth_client.revoke_refresh_token(refresh_token="rt-active")

    assert calls == [("http://idp.test/revoke", "rt-active", "refresh_token", "")]


async def test_revoke_refresh_token_skips_when_endpoint_missing(mocker):
    metadata = oauth_client.OIDCMetadata(
        issuer="http://idp.test",
        authorization_endpoint="http://idp.test/authorize",
        token_endpoint="http://idp.test/token",
        end_session_endpoint="http://idp.test/end-session",
        revocation_endpoint=None,
        introspection_endpoint=None,
        jwks_uri="http://idp.test/jwks",
    )
    mocker.patch("reporting.services.oauth_client.get_metadata", mocker.AsyncMock(return_value=metadata))
    build_client_mock = mocker.patch("reporting.services.oauth_client._build_oauth_client")

    await oauth_client.revoke_refresh_token(refresh_token="rt-active")

    build_client_mock.assert_not_called()


async def test_build_authorize_url_uses_authlib_pkce_builder():
    url = await oauth_client.build_authorize_url(
        authorization_endpoint="http://authentik-server:9000/application/o/authorize/",
        state="state-123",
        code_verifier="verifier-123",
        redirect_uri="http://localhost:3000/api/v1/auth/callback",
    )

    assert url.startswith("http://localhost:9000/application/o/authorize/?")
    assert "state=state-123" in url
    assert "code_challenge=" in url
    assert "code_challenge_method=S256" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A3000%2Fapi%2Fv1%2Fauth%2Fcallback" in url


async def test_get_metadata_rejects_unexpected_issuer(mocker):
    mocker.patch("reporting.settings.OIDC_AUTHORITY", "https://expected.example.com/o/seizu")
    mocker.patch("reporting.settings.OIDC_INTERNAL_AUTHORITY", "")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "issuer": "https://evil.example.com/o/seizu",
                "authorization_endpoint": "https://evil.example.com/authorize",
                "token_endpoint": "https://evil.example.com/token",
                "jwks_uri": "https://evil.example.com/jwks",
            }

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            return FakeResponse()

    mocker.patch("reporting.services.oauth_client.httpx.AsyncClient", FakeAsyncClient)

    with pytest.raises(oauth_client.OAuthClientError, match="issuer mismatch"):
        await oauth_client.get_metadata()


async def test_build_post_logout_url_uses_external_end_session_endpoint(mocker):
    metadata = oauth_client.OIDCMetadata(
        issuer="http://idp.test",
        authorization_endpoint="http://idp.test/authorize",
        token_endpoint="http://idp.test/token",
        end_session_endpoint="http://authentik-server:9000/application/o/seizu/end-session/",
        revocation_endpoint="http://idp.test/revoke",
        introspection_endpoint="http://idp.test/introspect",
        jwks_uri="http://idp.test/jwks",
    )
    mocker.patch("reporting.services.oauth_client.get_metadata", mocker.AsyncMock(return_value=metadata))

    url = await oauth_client.build_post_logout_url(
        id_token_hint="id-token-1",
        post_logout_redirect_uri="http://localhost:3000/logged-out",
    )

    assert url is not None
    assert url.startswith("http://localhost:9000/application/o/seizu/end-session/?")
    assert "client_id=seizu" in url
    assert "id_token_hint=id-token-1" in url
    assert "post_logout_redirect_uri=http%3A%2F%2Flocalhost%3A3000%2Flogged-out" in url


async def test_build_post_logout_url_skips_when_endpoint_missing(mocker):
    metadata = oauth_client.OIDCMetadata(
        issuer="http://idp.test",
        authorization_endpoint="http://idp.test/authorize",
        token_endpoint="http://idp.test/token",
        end_session_endpoint=None,
        revocation_endpoint="http://idp.test/revoke",
        introspection_endpoint="http://idp.test/introspect",
        jwks_uri="http://idp.test/jwks",
    )
    mocker.patch("reporting.services.oauth_client.get_metadata", mocker.AsyncMock(return_value=metadata))

    url = await oauth_client.build_post_logout_url(
        id_token_hint=None,
        post_logout_redirect_uri="http://localhost:3000/logged-out",
    )

    assert url is None


async def test_build_authorize_url_appends_extra_params(mocker):
    """Provider-specific authorize params (e.g. Google's access_type/prompt) are merged in."""
    mocker.patch(
        "reporting.settings.OIDC_AUTHORIZE_EXTRA_PARAMS",
        {"access_type": "offline", "prompt": "consent"},
    )
    url = await oauth_client.build_authorize_url(
        authorization_endpoint="http://localhost:9000/application/o/authorize/",
        state="state-123",
        code_verifier="verifier-123",
        redirect_uri="http://localhost:3000/api/v1/auth/callback",
    )
    assert "access_type=offline" in url
    assert "prompt=consent" in url


def _introspection_metadata(introspection_endpoint: str | None = "http://idp.test/introspect"):
    return oauth_client.OIDCMetadata(
        issuer="http://localhost:9000/application/o/seizu",
        authorization_endpoint="http://idp.test/authorize",
        token_endpoint="http://idp.test/token",
        end_session_endpoint="http://idp.test/end-session",
        revocation_endpoint="http://idp.test/revoke",
        introspection_endpoint=introspection_endpoint,
        jwks_uri="http://idp.test/jwks",
    )


async def test_introspect_token_returns_claims_when_active(mocker):
    mocker.patch(
        "reporting.services.oauth_client.get_metadata",
        mocker.AsyncMock(return_value=_introspection_metadata()),
    )
    calls = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "active": True,
                "sub": "user-1",
                "email": "u@example.com",
                "iss": "http://localhost:9000/application/o/seizu",
                "client_id": "seizu",
            }

    class FakeOAuthClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def client_auth(self, method):
            return ("client_auth", method)

        async def introspect_token(self, url, token, token_type_hint, body, auth):
            calls.append((url, token, token_type_hint, body, auth))
            return FakeResponse()

    mocker.patch("reporting.services.oauth_client._build_oauth_client", return_value=FakeOAuthClient())

    claims = await oauth_client.introspect_token(token="opaque-at")

    assert claims["sub"] == "user-1"
    assert calls == [
        (
            "http://idp.test/introspect",
            "opaque-at",
            "access_token",
            "client_id=seizu",
            ("client_auth", "none"),
        )
    ]


async def test_introspect_token_rejects_wrong_audience(mocker):
    mocker.patch(
        "reporting.services.oauth_client.get_metadata",
        mocker.AsyncMock(return_value=_introspection_metadata()),
    )

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "active": True,
                "sub": "user-1",
                "email": "u@example.com",
                "iss": "http://localhost:9000/application/o/seizu",
                "client_id": "other-client",
            }

    class FakeOAuthClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def client_auth(self, method):
            return None

        async def introspect_token(self, url, token, token_type_hint, body, auth):
            return FakeResponse()

    mocker.patch("reporting.services.oauth_client._build_oauth_client", return_value=FakeOAuthClient())

    with pytest.raises(oauth_client.OAuthClientError, match="not intended for this client"):
        await oauth_client.introspect_token(token="opaque-at")


async def test_introspect_token_allows_missing_optional_profile_claims(mocker):
    mocker.patch(
        "reporting.services.oauth_client.get_metadata",
        mocker.AsyncMock(return_value=_introspection_metadata()),
    )

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "active": True,
                "sub": "user-1",
                "iss": "http://localhost:9000/application/o/seizu",
                "client_id": "seizu",
            }

    class FakeOAuthClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def client_auth(self, method):
            return None

        async def introspect_token(self, url, token, token_type_hint, body, auth):
            return FakeResponse()

    mocker.patch("reporting.services.oauth_client._build_oauth_client", return_value=FakeOAuthClient())

    claims = await oauth_client.introspect_token(token="opaque-at")

    assert claims["sub"] == "user-1"
    assert claims["iss"] == "http://localhost:9000/application/o/seizu"


async def test_introspect_token_raises_when_inactive(mocker):
    mocker.patch(
        "reporting.services.oauth_client.get_metadata",
        mocker.AsyncMock(return_value=_introspection_metadata()),
    )

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"active": False}

    class FakeOAuthClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def client_auth(self, method):
            return None

        async def introspect_token(self, url, token, token_type_hint, body, auth):
            return FakeResponse()

    mocker.patch("reporting.services.oauth_client._build_oauth_client", return_value=FakeOAuthClient())

    with pytest.raises(oauth_client.OAuthClientError, match="inactive"):
        await oauth_client.introspect_token(token="opaque-at")


async def test_introspect_token_raises_when_endpoint_missing(mocker):
    mocker.patch(
        "reporting.services.oauth_client.get_metadata",
        mocker.AsyncMock(return_value=_introspection_metadata(introspection_endpoint=None)),
    )
    build_client_mock = mocker.patch("reporting.services.oauth_client._build_oauth_client")

    with pytest.raises(oauth_client.OAuthClientError, match="no introspection_endpoint"):
        await oauth_client.introspect_token(token="opaque-at")
    build_client_mock.assert_not_called()


async def test_build_authorize_url_includes_nonce():
    url = await oauth_client.build_authorize_url(
        authorization_endpoint="http://localhost:9000/application/o/authorize/",
        state="state-123",
        code_verifier="verifier-123",
        redirect_uri="http://localhost:3000/api/v1/auth/callback",
        nonce="nonce-xyz",
    )
    assert "nonce=nonce-xyz" in url


async def test_get_metadata_refetches_after_ttl(mocker):
    mocker.patch("reporting.settings.OIDC_AUTHORITY", "https://expected.example.com/o/seizu")
    mocker.patch("reporting.settings.OIDC_INTERNAL_AUTHORITY", "")
    mocker.patch("reporting.settings.OIDC_DISCOVERY_CACHE_TTL_SECONDS", 0)
    calls = {"n": 0}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "issuer": "https://expected.example.com/o/seizu",
                "authorization_endpoint": "https://expected.example.com/authorize",
                "token_endpoint": "https://expected.example.com/token",
                "jwks_uri": "https://expected.example.com/jwks",
            }

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            calls["n"] += 1
            return FakeResponse()

    mocker.patch("reporting.services.oauth_client.httpx.AsyncClient", FakeAsyncClient)

    await oauth_client.get_metadata()
    await oauth_client.get_metadata()
    assert calls["n"] == 2  # TTL=0 → cache always stale → refetch each call


async def test_get_metadata_caches_within_ttl(mocker):
    mocker.patch("reporting.settings.OIDC_AUTHORITY", "https://expected.example.com/o/seizu")
    mocker.patch("reporting.settings.OIDC_INTERNAL_AUTHORITY", "")
    mocker.patch("reporting.settings.OIDC_DISCOVERY_CACHE_TTL_SECONDS", 3600)
    calls = {"n": 0}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "issuer": "https://expected.example.com/o/seizu",
                "authorization_endpoint": "https://expected.example.com/authorize",
                "token_endpoint": "https://expected.example.com/token",
                "jwks_uri": "https://expected.example.com/jwks",
            }

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            calls["n"] += 1
            return FakeResponse()

    mocker.patch("reporting.services.oauth_client.httpx.AsyncClient", FakeAsyncClient)

    await oauth_client.get_metadata()
    await oauth_client.get_metadata()
    assert calls["n"] == 1  # cached within TTL


async def test_validate_id_token_accepts_valid_token(mocker):
    mocker.patch(
        "reporting.services.oauth_client.get_metadata",
        mocker.AsyncMock(return_value=_introspection_metadata()),
    )
    _patch_idtoken_jwks(mocker)
    now = int(time.time())
    token = _sign_id_token(
        iss="http://localhost:9000/application/o/seizu",
        aud="seizu",
        exp=now + 3600,
        iat=now,
        nonce="nonce-xyz",
        sub="user-1",
    )

    claims = await oauth_client.validate_id_token(id_token=token, nonce="nonce-xyz")

    assert claims["sub"] == "user-1"


async def test_validate_id_token_rejects_nonce_mismatch(mocker):
    mocker.patch(
        "reporting.services.oauth_client.get_metadata",
        mocker.AsyncMock(return_value=_introspection_metadata()),
    )
    _patch_idtoken_jwks(mocker)
    now = int(time.time())
    token = _sign_id_token(
        iss="http://localhost:9000/application/o/seizu",
        aud="seizu",
        exp=now + 3600,
        iat=now,
        nonce="real-nonce",
        sub="user-1",
    )

    with pytest.raises(oauth_client.OAuthClientError, match="nonce mismatch"):
        await oauth_client.validate_id_token(id_token=token, nonce="different-nonce")


async def test_validate_id_token_rejects_issuer_mismatch(mocker):
    mocker.patch(
        "reporting.services.oauth_client.get_metadata",
        mocker.AsyncMock(return_value=_introspection_metadata()),
    )
    _patch_idtoken_jwks(mocker)
    now = int(time.time())
    token = _sign_id_token(
        iss="http://evil.example.com",
        aud="seizu",
        exp=now + 3600,
        iat=now,
        nonce="n",
        sub="user-1",
    )

    with pytest.raises(oauth_client.OAuthClientError, match="issuer mismatch"):
        await oauth_client.validate_id_token(id_token=token, nonce="n")


async def test_validate_id_token_rejects_bad_audience(mocker):
    mocker.patch(
        "reporting.services.oauth_client.get_metadata",
        mocker.AsyncMock(return_value=_introspection_metadata()),
    )
    _patch_idtoken_jwks(mocker)
    now = int(time.time())
    token = _sign_id_token(
        iss="http://localhost:9000/application/o/seizu",
        aud="some-other-client",
        exp=now + 3600,
        iat=now,
        nonce="n",
        sub="user-1",
    )

    with pytest.raises(oauth_client.OAuthClientError, match="validation failed"):
        await oauth_client.validate_id_token(id_token=token, nonce="n")


async def test_validate_id_token_requires_subject(mocker):
    mocker.patch(
        "reporting.services.oauth_client.get_metadata",
        mocker.AsyncMock(return_value=_introspection_metadata()),
    )
    _patch_idtoken_jwks(mocker)
    now = int(time.time())
    token = _sign_id_token(
        iss="http://localhost:9000/application/o/seizu",
        aud="seizu",
        exp=now + 3600,
        iat=now,
        nonce="n",
    )

    with pytest.raises(oauth_client.OAuthClientError, match="validation failed"):
        await oauth_client.validate_id_token(id_token=token, nonce="n")


async def test_validate_id_token_rejects_bad_authorized_party(mocker):
    mocker.patch(
        "reporting.services.oauth_client.get_metadata",
        mocker.AsyncMock(return_value=_introspection_metadata()),
    )
    _patch_idtoken_jwks(mocker)
    now = int(time.time())
    token = _sign_id_token(
        iss="http://localhost:9000/application/o/seizu",
        aud=["seizu", "other-api"],
        azp="other-client",
        exp=now + 3600,
        iat=now,
        nonce="n",
        sub="user-1",
    )

    with pytest.raises(oauth_client.OAuthClientError, match="authorized party"):
        await oauth_client.validate_id_token(id_token=token, nonce="n")


async def test_validate_id_token_requires_jwks_uri(mocker):
    metadata = oauth_client.OIDCMetadata(
        issuer="http://idp.test",
        authorization_endpoint="http://idp.test/authorize",
        token_endpoint="http://idp.test/token",
        end_session_endpoint=None,
        revocation_endpoint=None,
        introspection_endpoint=None,
        jwks_uri=None,
    )
    mocker.patch("reporting.services.oauth_client.get_metadata", mocker.AsyncMock(return_value=metadata))

    with pytest.raises(oauth_client.OAuthClientError, match="no jwks_uri"):
        await oauth_client.validate_id_token(id_token="x.y.z", nonce="n")


def _patch_discovery_by_authority(mocker, issuers: dict[str, str | Exception]):
    """Serve a discovery document per authority host, keyed by the requested URL."""

    class FakeResponse:
        def __init__(self, issuer: str) -> None:
            self._issuer = issuer

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "issuer": self._issuer,
                "authorization_endpoint": f"{self._issuer}/authorize",
                "token_endpoint": f"{self._issuer}/token",
                "jwks_uri": f"{self._issuer}/jwks",
            }

    requested: list[str] = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            requested.append(url)
            for authority, issuer in issuers.items():
                if url.startswith(authority):
                    if isinstance(issuer, Exception):
                        raise issuer
                    return FakeResponse(issuer)
            raise AssertionError(f"unexpected discovery URL: {url}")

    mocker.patch("reporting.services.oauth_client.httpx.AsyncClient", FakeAsyncClient)
    return requested


async def test_verify_issuer_consistency_reports_forked_identities(mocker, caplog):
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    mocker.patch("reporting.settings.OIDC_REQUIRE_CONSISTENT_ISSUER", False)
    _patch_discovery_by_authority(
        mocker,
        {
            "http://authentik-server:9000": "http://authentik-server:9000/application/o/seizu/",
            "http://localhost:9000": "http://localhost:9000/application/o/seizu/",
        },
    )

    with caplog.at_level(logging.ERROR):
        await oauth_client.verify_issuer_consistency()

    assert "OIDC issuer mismatch" in caplog.text
    assert "http://authentik-server:9000/application/o/seizu/" in caplog.text
    assert "http://localhost:9000/application/o/seizu/" in caplog.text


async def test_verify_issuer_consistency_fails_startup_when_required(mocker):
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    mocker.patch("reporting.settings.OIDC_REQUIRE_CONSISTENT_ISSUER", True)
    _patch_discovery_by_authority(
        mocker,
        {
            "http://authentik-server:9000": "http://authentik-server:9000/application/o/seizu/",
            "http://localhost:9000": "http://localhost:9000/application/o/seizu/",
        },
    )

    with pytest.raises(oauth_client.OAuthClientError, match="OIDC issuer mismatch"):
        await oauth_client.verify_issuer_consistency()


async def test_verify_issuer_consistency_accepts_one_stable_issuer(mocker, caplog):
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    mocker.patch("reporting.settings.OIDC_REQUIRE_CONSISTENT_ISSUER", True)
    _patch_discovery_by_authority(
        mocker,
        {
            "http://authentik-server:9000": "https://idp.example.com/application/o/seizu/",
            "http://localhost:9000": "https://idp.example.com/application/o/seizu/",
        },
    )

    with caplog.at_level(logging.WARNING):
        await oauth_client.verify_issuer_consistency()

    assert caplog.text == ""


async def test_verify_issuer_consistency_reports_trailing_slash_difference(mocker):
    """A trailing slash is still two identities: (iss, sub) stores iss verbatim."""
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    mocker.patch("reporting.settings.OIDC_REQUIRE_CONSISTENT_ISSUER", True)
    _patch_discovery_by_authority(
        mocker,
        {
            "http://authentik-server:9000": "https://idp.example.com/application/o/seizu",
            "http://localhost:9000": "https://idp.example.com/application/o/seizu/",
        },
    )

    with pytest.raises(oauth_client.OAuthClientError, match="OIDC issuer mismatch"):
        await oauth_client.verify_issuer_consistency()


async def test_configured_authority_still_matches_advertised_trailing_slash(mocker):
    """The *config* comparison stays lenient — the dev stack depends on it."""
    mocker.patch("reporting.settings.OIDC_AUTHORITY", "http://localhost:9000/application/o/seizu")
    mocker.patch("reporting.settings.OIDC_INTERNAL_AUTHORITY", "")
    _patch_discovery_by_authority(
        mocker,
        {"http://localhost:9000": "http://localhost:9000/application/o/seizu/"},
    )

    metadata = await oauth_client.get_metadata()

    assert metadata.issuer == "http://localhost:9000/application/o/seizu/"


async def test_verify_issuer_consistency_warns_when_discovery_unreachable(mocker, caplog):
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    mocker.patch("reporting.settings.OIDC_REQUIRE_CONSISTENT_ISSUER", True)
    _patch_discovery_by_authority(
        mocker,
        {
            "http://authentik-server:9000": "http://authentik-server:9000/application/o/seizu/",
            "http://localhost:9000": httpx.ConnectError("no route to host"),
        },
    )

    with caplog.at_level(logging.WARNING):
        await oauth_client.verify_issuer_consistency()

    assert "Could not verify" in caplog.text
    assert "http://localhost:9000" in caplog.text


async def test_verify_issuer_consistency_skipped_without_split_hostnames(mocker):
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    mocker.patch("reporting.settings.OIDC_INTERNAL_AUTHORITY", "http://localhost:9000/application/o/seizu/")
    requested = _patch_discovery_by_authority(mocker, {"http://localhost:9000": "http://localhost:9000/o/seizu"})

    await oauth_client.verify_issuer_consistency()

    assert requested == []


async def test_verify_issuer_consistency_skipped_when_auth_disabled(mocker):
    mocker.patch("reporting.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", False)
    requested = _patch_discovery_by_authority(mocker, {"http://": "http://idp.test"})

    await oauth_client.verify_issuer_consistency()

    assert requested == []


async def test_id_token_issuer_must_match_discovery_exactly(mocker):
    """A trailing-slash variant is a different identity, so it is rejected."""
    mocker.patch(
        "reporting.services.oauth_client.get_metadata",
        mocker.AsyncMock(return_value=_introspection_metadata()),
    )
    _patch_idtoken_jwks(mocker)
    now = int(time.time())
    token = _sign_id_token(
        iss="http://localhost:9000/application/o/seizu/",
        aud="seizu",
        exp=now + 3600,
        iat=now,
        nonce="n",
        sub="user-1",
    )

    with pytest.raises(oauth_client.OAuthClientError, match="issuer mismatch"):
        await oauth_client.validate_id_token(id_token=token, nonce="n")


async def test_introspection_issuer_must_match_discovery_exactly(mocker):
    mocker.patch(
        "reporting.services.oauth_client.get_metadata",
        mocker.AsyncMock(return_value=_introspection_metadata()),
    )

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "active": True,
                "sub": "user-1",
                "iss": "http://localhost:9000/application/o/seizu/",
                "aud": "seizu",
            }

    class FakeOAuthClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def client_auth(self, method):
            return None

        async def introspect_token(self, url, token, token_type_hint, body, auth):
            return FakeResponse()

    mocker.patch("reporting.services.oauth_client._build_oauth_client", return_value=FakeOAuthClient())

    with pytest.raises(oauth_client.OAuthClientError, match="issuer mismatch"):
        await oauth_client.introspect_token(token="opaque-token")


async def test_discovery_document_with_malformed_json_raises_oauth_error(mocker):
    """Best-effort callers (MCP metadata) catch OAuthClientError, not ValueError."""

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            return FakeResponse()

    mocker.patch("reporting.services.oauth_client.httpx.AsyncClient", FakeAsyncClient)

    with pytest.raises(oauth_client.OAuthClientError, match="not valid JSON"):
        await oauth_client.fetch_discovery_document("http://idp.test")
