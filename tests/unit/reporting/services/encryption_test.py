import base64
import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from reporting.services import encryption
from reporting.services.encryption import (
    AESGCMCipher,
    AESGCMKeyring,
    Cipher,
    DecryptionError,
    decode_key,
    parse_keyring,
)


def _key_text() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


# ── single-key cipher (the cookies' format) ────────────────────────────────


async def test_single_key_cipher_round_trips_and_needs_the_same_associated_data():
    cipher = AESGCMCipher(os.urandom(32))
    sealed = await cipher.encrypt(b"secret", b"purpose-a")
    assert await cipher.decrypt(sealed, b"purpose-a") == b"secret"
    with pytest.raises(DecryptionError):
        await cipher.decrypt(sealed, b"purpose-b")


async def test_single_key_cipher_keeps_the_existing_cookie_wire_format():
    """nonce || ciphertext_and_tag, so cookies issued before the refactor still open."""
    key = os.urandom(32)
    nonce = os.urandom(12)
    legacy = nonce + AESGCM(key).encrypt(nonce, b"payload", b"seizu-session-v1")
    assert await AESGCMCipher(key).decrypt(legacy, b"seizu-session-v1") == b"payload"

    sealed = await AESGCMCipher(key).encrypt(b"payload", b"seizu-session-v1")
    assert AESGCM(key).decrypt(sealed[:12], sealed[12:], b"seizu-session-v1") == b"payload"


async def test_single_key_cipher_rejects_short_and_tampered_input():
    cipher = AESGCMCipher(os.urandom(32))
    with pytest.raises(DecryptionError, match="too short"):
        await cipher.decrypt(b"short", b"")
    sealed = bytearray(await cipher.encrypt(b"secret", b""))
    sealed[-1] ^= 1
    with pytest.raises(DecryptionError, match="authentication"):
        await cipher.decrypt(bytes(sealed), b"")


async def test_single_key_cipher_names_no_key():
    cipher = AESGCMCipher(os.urandom(32))
    with pytest.raises(DecryptionError):
        cipher.key_id(await cipher.encrypt(b"x", b""))


def test_both_implementations_satisfy_the_cipher_protocol():
    assert isinstance(AESGCMCipher(os.urandom(32)), Cipher)
    assert isinstance(AESGCMKeyring([("k1", os.urandom(32))]), Cipher)


# ── keyring ────────────────────────────────────────────────────────────────


async def test_keyring_encrypts_with_the_first_key_and_records_its_id():
    keyring = AESGCMKeyring([("new", os.urandom(32)), ("old", os.urandom(32))])
    sealed = await keyring.encrypt(b"secret", b"row")
    assert keyring.current_key_id == "new"
    assert keyring.key_id(sealed) == "new"
    assert await keyring.decrypt(sealed, b"row") == b"secret"


async def test_keyring_rotation_keeps_old_ciphertexts_readable():
    old_key, new_key = os.urandom(32), os.urandom(32)
    sealed_before = await AESGCMKeyring([("old", old_key)]).encrypt(b"secret", b"row")
    rotated = AESGCMKeyring([("new", new_key), ("old", old_key)])
    assert await rotated.decrypt(sealed_before, b"row") == b"secret"
    assert rotated.key_id(await rotated.encrypt(b"secret", b"row")) == "new"


async def test_keyring_refuses_a_ciphertext_whose_key_was_removed():
    sealed = await AESGCMKeyring([("old", os.urandom(32))]).encrypt(b"secret", b"row")
    with pytest.raises(DecryptionError, match="'old', which is not configured"):
        await AESGCMKeyring([("new", os.urandom(32))]).decrypt(sealed, b"row")


async def test_keyring_header_is_authenticated():
    """Renaming the key ID in a ciphertext must not select a different key."""
    key = os.urandom(32)
    keyring = AESGCMKeyring([("aa", key), ("bb", key)])
    sealed = bytearray(await keyring.encrypt(b"secret", b"row"))
    assert keyring.key_id(bytes(sealed)) == "aa"
    sealed[2:4] = b"bb"
    with pytest.raises(DecryptionError, match="authentication"):
        await keyring.decrypt(bytes(sealed), b"row")


async def test_keyring_binds_associated_data():
    keyring = AESGCMKeyring([("k1", os.urandom(32))])
    sealed = await keyring.encrypt(b"secret", b"alice")
    with pytest.raises(DecryptionError):
        await keyring.decrypt(sealed, b"bob")


async def test_keyring_rejects_input_not_in_its_format():
    keyring = AESGCMKeyring([("k1", os.urandom(32))])
    with pytest.raises(DecryptionError, match="keyring format"):
        await keyring.decrypt(await AESGCMCipher(os.urandom(32)).encrypt(b"x", b""), b"")
    with pytest.raises(DecryptionError, match="too short"):
        await keyring.decrypt(b"\x01\x02k1", b"")


@pytest.mark.parametrize(
    "keys",
    [[], [("bad id", os.urandom(32))], [("k1", os.urandom(32)), ("k1", os.urandom(32))], [("k1", b"short")]],
)
def test_keyring_rejects_invalid_keys(keys):
    with pytest.raises(ValueError):
        AESGCMKeyring(keys)


# ── configuration parsing ──────────────────────────────────────────────────


def test_decode_key_names_the_setting_in_every_error():
    with pytest.raises(RuntimeError, match="SOME_KEY must be configured"):
        decode_key("", "SOME_KEY")
    with pytest.raises(RuntimeError, match="SOME_KEY must be valid base64"):
        decode_key("not base64!@#", "SOME_KEY")
    with pytest.raises(RuntimeError, match="SOME_KEY must decode to 32 bytes"):
        decode_key(base64.b64encode(b"short").decode(), "SOME_KEY")


def test_parse_keyring_preserves_order_and_tolerates_spaces():
    first, second = _key_text(), _key_text()
    keys = parse_keyring(f" 2026-11:{first} , 2026-05:{second} ", "KEYS")
    assert [key_id for key_id, _ in keys] == ["2026-11", "2026-05"]
    assert keys[0][1] == base64.b64decode(first)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "must be configured"),
        ("   ", "must be configured"),
        ("nocolon", "must be id:base64key"),
        ("bad id:{key}", "key IDs must be"),
        ("k1:{key},k1:{key}", "more than once"),
        ("k1:not-base64!", "key 'k1' must be valid base64"),
    ],
)
def test_parse_keyring_rejects_bad_configuration(raw, message):
    with pytest.raises(RuntimeError, match=message):
        parse_keyring(raw.format(key=_key_text()), "KEYS")


# ── factories ──────────────────────────────────────────────────────────────


async def test_vault_and_session_ciphers_use_separate_keys(mocker):
    mocker.patch("reporting.settings.SESSION_TOKEN_ENCRYPTION_KEY", _key_text())
    mocker.patch("reporting.settings.DELEGATED_TOKEN_ENCRYPTION_KEYS", f"k1:{_key_text()}")
    session = encryption.session_cipher()
    vault = encryption.delegated_token_cipher()
    assert isinstance(session, AESGCMCipher)
    assert isinstance(vault, AESGCMKeyring)
    sealed = await vault.encrypt(b"grant", b"row")
    with pytest.raises(DecryptionError):
        await session.decrypt(sealed[4:], b"row")


def test_vault_cipher_requires_its_own_setting(mocker):
    mocker.patch("reporting.settings.SESSION_TOKEN_ENCRYPTION_KEY", _key_text())
    mocker.patch("reporting.settings.DELEGATED_TOKEN_ENCRYPTION_KEYS", "")
    with pytest.raises(RuntimeError, match="DELEGATED_TOKEN_ENCRYPTION_KEYS must be configured"):
        encryption.delegated_token_cipher()
