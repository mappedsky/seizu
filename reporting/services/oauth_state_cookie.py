"""Short-lived cookie that round-trips OAuth state across the IDP redirect.

When the browser hits ``GET /api/v1/auth/login``, the backend generates a
PKCE verifier + challenge and a random OAuth ``state`` value, then sets this
cookie containing ``{state, verifier, return_to, exp, nonce}``. The cookie travels
with the user through the IDP and back to ``/api/v1/auth/callback``, where
the backend verifies ``state`` matches the callback's query parameter and
uses ``verifier`` to complete the PKCE exchange.

The cookie is AES-GCM-encrypted with the session cookie's cipher
(``encryption.session_cipher``, keyed by ``SESSION_TOKEN_ENCRYPTION_KEY``). It uses ``SameSite=Lax`` because
it must be sent on the top-level cross-site redirect *back* from the IDP —
``Strict`` would suppress it on that hop.

It is short-lived (default 5 minutes), single-use (the callback clears it
on success), and self-validating against its own ``exp``.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from dataclasses import dataclass

from reporting.services import encryption

_NONCE_BYTES = encryption.NONCE_BYTES
_GCM_TAG_BYTES = encryption.TAG_BYTES
# AES-GCM associated data domain-separates this cookie from the session
# cookie, which shares the same encryption key. A ciphertext minted for one
# purpose fails authentication if presented as the other. Bump the version
# suffix if the payload schema changes incompatibly.
_AAD = b"seizu-oauth-state-v1"

STATE_COOKIE_NAME = "seizu_oauth_state"
STATE_COOKIE_MAX_AGE_SECONDS = 5 * 60


class OAuthStateCookieError(ValueError):
    """Raised when the state cookie is malformed, tampered with, or expired."""


@dataclass(frozen=True)
class OAuthStatePayload:
    state: str
    verifier: str
    return_to: str
    exp: int
    nonce: str


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


async def encrypt(payload: OAuthStatePayload) -> str:
    plaintext = json.dumps(
        {
            "state": payload.state,
            "verifier": payload.verifier,
            "return_to": payload.return_to,
            "exp": payload.exp,
            "nonce": payload.nonce,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return _b64url_encode(await encryption.session_cipher().encrypt(plaintext, _AAD))


async def decrypt(cookie_value: str, *, now: int | None = None) -> OAuthStatePayload:
    try:
        blob = _b64url_decode(cookie_value)
    except (ValueError, binascii.Error) as exc:
        raise OAuthStateCookieError("Malformed state cookie") from exc
    if len(blob) < _NONCE_BYTES + _GCM_TAG_BYTES:
        raise OAuthStateCookieError("State cookie too short")
    try:
        plaintext = await encryption.session_cipher().decrypt(blob, _AAD)
    except encryption.DecryptionError as exc:
        raise OAuthStateCookieError("State cookie integrity check failed") from exc

    try:
        data = json.loads(plaintext)
    except json.JSONDecodeError as exc:
        raise OAuthStateCookieError("State cookie payload is not valid JSON") from exc
    if not isinstance(data, dict):
        raise OAuthStateCookieError("State cookie payload is not an object")

    try:
        state = data["state"]
        verifier = data["verifier"]
        return_to = data["return_to"]
        exp = int(data["exp"])
        nonce = data["nonce"]
    except (KeyError, TypeError, ValueError) as exc:
        raise OAuthStateCookieError("State cookie payload is missing required fields") from exc
    if not all(isinstance(v, str) and v for v in (state, verifier, nonce)):
        raise OAuthStateCookieError("State cookie payload has invalid state/verifier/nonce")
    if not isinstance(return_to, str):
        raise OAuthStateCookieError("State cookie payload has invalid return_to")

    current = int(time.time()) if now is None else now
    if exp <= current:
        raise OAuthStateCookieError("State cookie has expired")

    return OAuthStatePayload(state=state, verifier=verifier, return_to=return_to, exp=exp, nonce=nonce)


def is_safe_return_to(value: str) -> bool:
    """Return True if ``value`` is a safe same-origin path-only redirect target.

    Rejects any value that could navigate the user to a foreign origin: empty
    strings, anything not starting with ``/``, protocol-relative URLs (``//``),
    backslash variants, or values containing newlines / control bytes that
    might confuse downstream redirect handling.
    """
    if not value:
        return False
    if not value.startswith("/"):
        return False
    if value.startswith("//") or value.startswith("/\\"):
        return False
    if any(ord(c) < 0x20 for c in value):
        return False
    return True
