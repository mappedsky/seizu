"""Encrypted store for each user's delegated OAuth grants to external MCP gateways.

A grant is what one user authorized Seizu to hold for one gateway resource: the
refresh token, the current access token, and the client and endpoint that renew
them. The whole grant, endpoint and client ID included, is encrypted as one
value by ``encryption.delegated_token_cipher``; nothing that steers a refresh is
stored in the clear (STO-015).

Every ciphertext is bound by its associated data to the owner, proxy and
resource of its row. A ciphertext copied onto another user's row fails to
decrypt instead of being used as that user's grant.

Rotation contract: ``load`` returns the row's revision, a random value that
changes on every write and never repeats, so a grant deleted and connected again
cannot match a revision read before. A refresh first takes the grant's refresh
claim (``claim_refresh``), which at most one process holds at a time, reloads,
and writes the rotated grant back with ``replace(..., expected_revision=...)``.
``replace`` returns None when another writer replaced the grant first. That
caller must reload and use the winner's grant, and must not treat its own failed
refresh as a revoked grant until it has reloaded.

Grants are never written to logs or model context; ``list_grants`` returns
metadata only.
"""

from __future__ import annotations

import base64
import binascii
import json
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any

from reporting.services import encryption, report_store

_AAD_PREFIX = b"seizu-delegated-token-v1\x00"


class VaultError(RuntimeError):
    """A stored grant exists but cannot be read: its key is gone, or the row was altered."""


@dataclass(frozen=True)
class DelegatedGrant:
    refresh_token: str
    token_endpoint: str
    client_id: str
    access_token: str | None = None
    # Unix seconds; None when the authorization server did not say.
    access_token_expires_at: int | None = None
    scope: str | None = None
    issuer: str | None = None


@dataclass(frozen=True)
class StoredGrant:
    grant: DelegatedGrant
    revision: str
    key_id: str
    updated_at: str


@dataclass(frozen=True)
class GrantSummary:
    """What is safe to show the grant's owner: no token, endpoint or client ID."""

    proxy_name: str
    resource: str
    key_id: str
    created_at: str
    updated_at: str


def _associated_data(user_id: str, proxy_name: str, resource: str) -> bytes:
    # A JSON array, so no choice of values can make two rows' data collide.
    return _AAD_PREFIX + json.dumps([user_id, proxy_name, resource], separators=(",", ":")).encode()


def _require(user_id: str, proxy_name: str, resource: str) -> None:
    if not (user_id and proxy_name and resource):
        raise ValueError("a delegated grant needs a user, a proxy and a resource")


def _parse_grant(plaintext: bytes) -> DelegatedGrant:
    data = json.loads(plaintext)
    if not isinstance(data, dict):
        raise ValueError("grant is not an object")
    grant = DelegatedGrant(**data)
    if not (grant.refresh_token and grant.token_endpoint and grant.client_id):
        raise ValueError("grant is missing its refresh token, token endpoint or client ID")
    return grant


async def _record(user_id: str, proxy_name: str, resource: str, grant: DelegatedGrant) -> dict[str, Any]:
    _require(user_id, proxy_name, resource)
    if not (grant.refresh_token and grant.token_endpoint and grant.client_id):
        raise ValueError("a delegated grant needs a refresh token, a token endpoint and a client ID")
    cipher = encryption.delegated_token_cipher()
    sealed = await cipher.encrypt(
        json.dumps(asdict(grant), separators=(",", ":")).encode(),
        _associated_data(user_id, proxy_name, resource),
    )
    return {
        "user_id": user_id,
        "proxy_name": proxy_name,
        "resource": resource,
        "key_id": cipher.key_id(sealed),
        "ciphertext": base64.b64encode(sealed).decode("ascii"),
        "revision": secrets.token_urlsafe(16),
    }


async def save(user_id: str, proxy_name: str, resource: str, grant: DelegatedGrant) -> str:
    """Store a newly authorized grant, replacing any earlier one; return its revision."""
    record = await _record(user_id, proxy_name, resource, grant)
    if not await report_store.put_delegated_token(record, expected_revision=None):
        raise RuntimeError("an unconditional grant write was not applied")
    return str(record["revision"])


async def replace(
    user_id: str, proxy_name: str, resource: str, grant: DelegatedGrant, *, expected_revision: str
) -> str | None:
    """Replace a grant only if it is still at ``expected_revision``; return the new revision, or None."""
    record = await _record(user_id, proxy_name, resource, grant)
    if not await report_store.put_delegated_token(record, expected_revision=expected_revision):
        return None
    return str(record["revision"])


async def claim_refresh(user_id: str, proxy_name: str, resource: str, *, lease_seconds: int) -> str | None:
    """Claim the right to refresh this grant for ``lease_seconds``; return the claim, or None.

    None means another process holds a live claim, or the grant is gone. A claim
    outlives a crashed holder only until its lease ends.
    """
    _require(user_id, proxy_name, resource)
    claimant = secrets.token_urlsafe(16)
    now = int(time.time())
    claimed = await report_store.claim_delegated_token_refresh(
        user_id, proxy_name, resource, claimant=claimant, expires_at=now + lease_seconds, now=now
    )
    return claimant if claimed else None


async def release_refresh(user_id: str, proxy_name: str, resource: str, claim: str) -> None:
    """Release a claim from ``claim_refresh``; a claim taken over after its lease is left alone."""
    await report_store.release_delegated_token_refresh(user_id, proxy_name, resource, claimant=claim)


async def load(user_id: str, proxy_name: str, resource: str) -> StoredGrant | None:
    """Return the user's grant for this resource, or None if they have not connected.

    Raises ``VaultError`` when a row exists but cannot be decrypted. The caller
    reports that as needing reconnection; the row is not deleted here.
    """
    _require(user_id, proxy_name, resource)
    row = await report_store.get_delegated_token(user_id, proxy_name, resource)
    if row is None:
        return None
    cipher = encryption.delegated_token_cipher()
    try:
        sealed = base64.b64decode(row["ciphertext"], validate=True)
        plaintext = await cipher.decrypt(sealed, _associated_data(user_id, proxy_name, resource))
        grant = _parse_grant(plaintext)
    except (binascii.Error, encryption.DecryptionError, ValueError, TypeError) as exc:
        raise VaultError(f"stored grant for proxy {proxy_name!r} cannot be read") from exc
    return StoredGrant(grant=grant, revision=str(row["revision"]), key_id=row["key_id"], updated_at=row["updated_at"])


async def delete(user_id: str, proxy_name: str | None = None, resource: str | None = None) -> int:
    """Delete a user's grants, narrowed by proxy and resource; return how many were removed."""
    if not user_id:
        raise ValueError("deleting delegated grants needs a user")
    if resource is not None and proxy_name is None:
        raise ValueError("a resource is only meaningful with its proxy")
    return await report_store.delete_delegated_tokens(user_id, proxy_name, resource)


async def list_grants(user_id: str) -> list[GrantSummary]:
    """List the user's grants without decrypting them."""
    return [
        GrantSummary(
            proxy_name=row["proxy_name"],
            resource=row["resource"],
            key_id=row["key_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in await report_store.list_delegated_tokens(user_id)
    ]
