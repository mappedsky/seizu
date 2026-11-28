import base64
import os

import pytest

from reporting.services.session_cookie import (
    SessionCookieError,
    SessionPayload,
    compute_cookie_max_age,
    decrypt,
    encrypt,
)


@pytest.fixture(autouse=True)
def _configure_key(mocker):
    mocker.patch(
        "reporting.settings.SESSION_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(os.urandom(32)).decode("ascii"),
    )


def _payload(refresh_token: str = "rt-abc", iat: int = 1_000, abs_exp: int = 10**12) -> SessionPayload:
    return SessionPayload(refresh_token=refresh_token, iat=iat, abs_exp=abs_exp)


async def test_encrypt_then_decrypt_roundtrips():
    payload = _payload(refresh_token="rt-xyz", iat=42, abs_exp=10**12)
    cookie = await encrypt(payload)
    out = await decrypt(cookie)
    assert out == payload


async def test_encrypt_then_decrypt_roundtrips_with_id_token():
    payload = SessionPayload(refresh_token="rt-xyz", iat=42, abs_exp=10**12, id_token="id-token")
    cookie = await encrypt(payload)
    out = await decrypt(cookie)
    assert out == payload


async def test_encrypt_produces_distinct_ciphertexts_for_same_payload():
    # Random nonce → cookies differ even for the same payload.
    payload = _payload()
    assert await encrypt(payload) != await encrypt(payload)


async def test_session_cookie_cannot_be_decrypted_as_state_cookie():
    """AAD domain separation: a session cookie fails state-cookie auth even with the shared key."""
    from reporting.services import oauth_state_cookie

    session = await encrypt(_payload())
    with pytest.raises(oauth_state_cookie.OAuthStateCookieError, match="integrity check failed"):
        await oauth_state_cookie.decrypt(session)


async def test_decrypt_rejects_malformed_base64():
    with pytest.raises(SessionCookieError, match="Malformed session cookie"):
        await decrypt("not!base64!@#")


async def test_decrypt_rejects_too_short_blob():
    too_short = base64.urlsafe_b64encode(b"abc").rstrip(b"=").decode("ascii")
    with pytest.raises(SessionCookieError, match="too short"):
        await decrypt(too_short)


async def test_decrypt_rejects_tampered_ciphertext():
    cookie = await encrypt(_payload())
    # Flip a byte in the ciphertext portion (after the nonce).
    raw = bytearray(base64.urlsafe_b64decode(cookie + "=" * (-len(cookie) % 4)))
    raw[-1] ^= 0x01
    tampered = base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode("ascii")
    with pytest.raises(SessionCookieError, match="integrity check failed"):
        await decrypt(tampered)


async def test_decrypt_rejects_wrong_key(mocker):
    cookie = await encrypt(_payload())
    mocker.patch(
        "reporting.settings.SESSION_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(os.urandom(32)).decode("ascii"),
    )
    with pytest.raises(SessionCookieError, match="integrity check failed"):
        await decrypt(cookie)


async def test_decrypt_rejects_expired_abs_exp():
    cookie = await encrypt(_payload(abs_exp=500))
    with pytest.raises(SessionCookieError, match="absolute expiry"):
        await decrypt(cookie, now=1_000)


async def test_decrypt_accepts_at_or_before_abs_exp():
    cookie = await encrypt(_payload(abs_exp=1_000))
    payload = await decrypt(cookie, now=999)
    assert payload.abs_exp == 1_000


async def test_unconfigured_key_raises():
    # _configure_key fixture overrides this — undo it for this one test.
    import reporting.settings as s

    original = s.SESSION_TOKEN_ENCRYPTION_KEY
    s.SESSION_TOKEN_ENCRYPTION_KEY = ""
    try:
        with pytest.raises(RuntimeError, match="must be configured"):
            await encrypt(_payload())
    finally:
        s.SESSION_TOKEN_ENCRYPTION_KEY = original


async def test_invalid_base64_key_raises(mocker):
    mocker.patch("reporting.settings.SESSION_TOKEN_ENCRYPTION_KEY", "not valid base64!@#")
    with pytest.raises(RuntimeError, match="valid base64"):
        await encrypt(_payload())


async def test_wrong_length_key_raises(mocker):
    mocker.patch(
        "reporting.settings.SESSION_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(b"too-short").decode("ascii"),
    )
    with pytest.raises(RuntimeError, match="32 bytes"):
        await encrypt(_payload())


async def test_compute_cookie_max_age_uses_rolling_window_when_far_from_abs_exp(mocker):
    mocker.patch("reporting.settings.SESSION_COOKIE_MAX_AGE_SECONDS", 18 * 60 * 60)
    assert compute_cookie_max_age(abs_exp=10**12, now=1_000) == 18 * 60 * 60


async def test_compute_cookie_max_age_caps_at_abs_exp_when_close(mocker):
    mocker.patch("reporting.settings.SESSION_COOKIE_MAX_AGE_SECONDS", 18 * 60 * 60)
    assert compute_cookie_max_age(abs_exp=1_100, now=1_000) == 100


async def test_compute_cookie_max_age_returns_zero_when_abs_exp_past(mocker):
    mocker.patch("reporting.settings.SESSION_COOKIE_MAX_AGE_SECONDS", 18 * 60 * 60)
    assert compute_cookie_max_age(abs_exp=500, now=1_000) == 0
