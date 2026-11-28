"""Authenticated encryption behind a swappable cipher interface.

Every purpose that encrypts a secret gets its cipher from a factory in this
module: ``session_cipher`` for the BFF session and OAuth state cookies,
``delegated_token_cipher`` for the token vault. A factory is the only place
that knows where key material comes from, so another provider (for example AWS
KMS envelope encryption) is added by returning a different ``Cipher`` from it.
Callers never see a key.

``associated_data`` is authenticated but not encrypted. Callers use it to bind
a ciphertext to its purpose and, for stored records, to the record it was made
for, so a ciphertext presented for another purpose or copied onto another row
fails to decrypt instead of being accepted.

The interface is asynchronous because a remote provider makes a network call
per operation; the local AES-GCM implementations do not await anything.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from reporting import settings

KEY_BYTES = 32
NONCE_BYTES = 12
TAG_BYTES = 16

_KEYRING_FORMAT_VERSION = 1
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_GENERATE_HINT = "python -c 'import base64,os;print(base64.b64encode(os.urandom(32)).decode())'"


class DecryptionError(ValueError):
    """A ciphertext is malformed, was altered, or was made for other associated data."""


@runtime_checkable
class Cipher(Protocol):
    async def encrypt(self, plaintext: bytes, associated_data: bytes) -> bytes:
        """Return a ciphertext that only ``decrypt`` with the same associated data opens."""

    async def decrypt(self, sealed: bytes, associated_data: bytes) -> bytes:
        """Return the plaintext, or raise ``DecryptionError``."""

    def key_id(self, sealed: bytes) -> str:
        """Name the key that produced ``sealed``, for rotation bookkeeping.

        Raises ``DecryptionError`` when the ciphertext does not say.
        """


def decode_key(raw: str, setting: str) -> bytes:
    """Decode one base64 AES-256 key from the setting called ``setting``.

    Raises ``RuntimeError`` naming the setting when it is empty or invalid, so a
    misconfiguration fails at the first use rather than producing bad ciphertext.
    """
    if not raw:
        raise RuntimeError(f"{setting} must be configured ({KEY_BYTES} random bytes, base64-encoded)")
    try:
        key = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError(f"{setting} must be valid base64") from exc
    if len(key) != KEY_BYTES:
        raise RuntimeError(f"{setting} must decode to {KEY_BYTES} bytes (got {len(key)})")
    return key


def parse_keyring(raw: str, setting: str) -> list[tuple[str, bytes]]:
    """Parse ``id:base64key[,id:base64key...]``; the first entry is the current key.

    Key IDs are 1-64 characters of ``[A-Za-z0-9._-]`` and must be unique.
    """
    if not raw.strip():
        raise RuntimeError(
            f"{setting} must be configured as id:base64key[,id:base64key...]; generate a key with {_GENERATE_HINT}"
        )
    keys: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for entry in raw.split(","):
        key_id, sep, encoded = entry.strip().partition(":")
        if not sep:
            raise RuntimeError(f"{setting} entries must be id:base64key")
        if not _KEY_ID_RE.match(key_id):
            raise RuntimeError(f"{setting} key IDs must be 1-64 characters of letters, digits, '.', '_' or '-'")
        if key_id in seen:
            raise RuntimeError(f"{setting} names key ID {key_id!r} more than once")
        seen.add(key_id)
        keys.append((key_id, decode_key(encoded.strip(), f"{setting} key {key_id!r}")))
    return keys


class AESGCMCipher:
    """One AES-256-GCM key, producing ``nonce || ciphertext_and_tag`` with no header.

    This is the session and OAuth state cookies' existing wire format. It names
    no key, so rotating the key invalidates every outstanding ciphertext.
    """

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError(f"AES-256-GCM keys are {KEY_BYTES} bytes")
        self._aead = AESGCM(key)

    async def encrypt(self, plaintext: bytes, associated_data: bytes) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return nonce + self._aead.encrypt(nonce, plaintext, associated_data)

    async def decrypt(self, sealed: bytes, associated_data: bytes) -> bytes:
        if len(sealed) < NONCE_BYTES + TAG_BYTES:
            raise DecryptionError("ciphertext is too short")
        try:
            return self._aead.decrypt(sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], associated_data)
        except InvalidTag as exc:
            raise DecryptionError("ciphertext failed authentication") from exc

    def key_id(self, sealed: bytes) -> str:
        raise DecryptionError("single-key ciphertexts do not name their key")


class AESGCMKeyring:
    """AES-256-GCM over several named keys: encrypts with the first, decrypts with any.

    Output is ``version || len(key_id) || key_id || nonce || ciphertext_and_tag``.
    The header is authenticated as part of the associated data, so changing the
    named key ID fails decryption rather than selecting another key.

    Rotation: prepend a new key and keep the old ones until nothing sealed with
    them remains; ``key_id`` reports which key each stored ciphertext uses.
    """

    def __init__(self, keys: Sequence[tuple[str, bytes]]) -> None:
        if not keys:
            raise ValueError("a keyring needs at least one key")
        self._current = keys[0][0]
        self._aeads: dict[str, AESGCM] = {}
        for key_id, key in keys:
            if not _KEY_ID_RE.match(key_id):
                raise ValueError(f"invalid key ID {key_id!r}")
            if key_id in self._aeads:
                raise ValueError(f"duplicate key ID {key_id!r}")
            if len(key) != KEY_BYTES:
                raise ValueError(f"AES-256-GCM keys are {KEY_BYTES} bytes")
            self._aeads[key_id] = AESGCM(key)

    @property
    def current_key_id(self) -> str:
        return self._current

    @staticmethod
    def _header(key_id: str) -> bytes:
        encoded = key_id.encode("ascii")
        return bytes((_KEYRING_FORMAT_VERSION, len(encoded))) + encoded

    @staticmethod
    def _split(sealed: bytes) -> tuple[str, bytes, bytes]:
        """Return ``(key_id, header, body)`` from a keyring ciphertext."""
        if len(sealed) < 2 or sealed[0] != _KEYRING_FORMAT_VERSION:
            raise DecryptionError("ciphertext is not in the keyring format")
        header_length = 2 + sealed[1]
        if len(sealed) < header_length + NONCE_BYTES + TAG_BYTES:
            raise DecryptionError("ciphertext is too short")
        try:
            key_id = sealed[2:header_length].decode("ascii")
        except UnicodeDecodeError as exc:
            raise DecryptionError("ciphertext names an invalid key ID") from exc
        return key_id, sealed[:header_length], sealed[header_length:]

    async def encrypt(self, plaintext: bytes, associated_data: bytes) -> bytes:
        header = self._header(self._current)
        nonce = os.urandom(NONCE_BYTES)
        return header + nonce + self._aeads[self._current].encrypt(nonce, plaintext, header + associated_data)

    async def decrypt(self, sealed: bytes, associated_data: bytes) -> bytes:
        key_id, header, body = self._split(sealed)
        aead = self._aeads.get(key_id)
        if aead is None:
            raise DecryptionError(f"ciphertext was made with key {key_id!r}, which is not configured")
        try:
            return aead.decrypt(body[:NONCE_BYTES], body[NONCE_BYTES:], header + associated_data)
        except InvalidTag as exc:
            raise DecryptionError("ciphertext failed authentication") from exc

    def key_id(self, sealed: bytes) -> str:
        return self._split(sealed)[0]


def session_cipher() -> Cipher:
    """The cipher for the session and OAuth state cookies (``SESSION_TOKEN_ENCRYPTION_KEY``)."""
    return AESGCMCipher(decode_key(settings.SESSION_TOKEN_ENCRYPTION_KEY, "SESSION_TOKEN_ENCRYPTION_KEY"))


def delegated_token_cipher() -> Cipher:
    """The cipher for the token vault (``DELEGATED_TOKEN_ENCRYPTION_KEYS``).

    Deliberately not the session key: the Temporal worker needs this one and
    must not be able to open or forge browser sessions (STO-015).
    """
    return AESGCMKeyring(parse_keyring(settings.DELEGATED_TOKEN_ENCRYPTION_KEYS, "DELEGATED_TOKEN_ENCRYPTION_KEYS"))
