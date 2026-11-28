"""Encrypted store for each user's delegated OAuth grants to external MCP gateways.

A grant is what one user authorized Seizu to hold for one gateway resource: the
refresh token, the current access token, and the client and endpoint that renew
them. The whole grant, endpoint and client ID included, is encrypted as one
value by ``encryption.delegated_token_cipher``; nothing that steers a refresh is
stored in the clear (STO-015).

Every ciphertext is bound by its associated data to the owner, proxy and
resource of its row. A ciphertext copied onto another user's row fails to
decrypt instead of being used as that user's grant.

Rotation contract: ``load`` returns the row's version. A refresh writes the
rotated grant back with ``replace(..., expected_version=...)``, which returns
None when another writer replaced the grant first. That caller must reload and
use the winner's grant, and must not treat its own failed refresh as a revoked
grant until it has reloaded.

Grants are never written to logs or model context; ``list_grants`` returns
metadata only.

The vault also keeps Seizu's own dynamic client registrations with gateway
authorization servers (STO-016). A registration's secret is what spends every
refresh token issued to that client, so it is encrypted like a grant.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import asdict, dataclass
from typing import Any

from reporting.services import encryption, report_store

_AAD_PREFIX = b"seizu-delegated-token-v1\x00"
_CLIENT_AAD_PREFIX = b"seizu-oauth-client-v1\x00"


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
    revocation_endpoint: str | None = None
    # The protected resource the grant was issued for (RFC 8707), resent on refresh.
    resource: str | None = None


@dataclass(frozen=True)
class ClientRegistration:
    """Seizu's client at one authorization server, as that server registered it."""

    client_id: str
    redirect_uri: str
    client_secret: str | None = None
    # "none" for a public client, which proves itself with PKCE alone.
    token_endpoint_auth_method: str = "client_secret_basic"


@dataclass(frozen=True)
class StoredGrant:
    grant: DelegatedGrant
    version: int
    key_id: str
    updated_at: str


@dataclass(frozen=True)
class GrantSummary:
    """What is safe to show the grant's owner: no token, endpoint or client ID."""

    proxy_name: str
    resource: str
    key_id: str
    version: int
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
    }


async def save(user_id: str, proxy_name: str, resource: str, grant: DelegatedGrant) -> int:
    """Store a newly authorized grant, replacing any earlier one; return its version."""
    version = await report_store.put_delegated_token(
        await _record(user_id, proxy_name, resource, grant), expected_version=None
    )
    if version is None:
        raise RuntimeError("an unconditional grant write reported no version")
    return version


async def replace(
    user_id: str, proxy_name: str, resource: str, grant: DelegatedGrant, *, expected_version: int
) -> int | None:
    """Replace a grant only if it is still at ``expected_version``; None means another writer won."""
    return await report_store.put_delegated_token(
        await _record(user_id, proxy_name, resource, grant), expected_version=expected_version
    )


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
    return StoredGrant(grant=grant, version=int(row["version"]), key_id=row["key_id"], updated_at=row["updated_at"])


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
            version=int(row["version"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in await report_store.list_delegated_tokens(user_id)
    ]


def _client_associated_data(issuer: str, client_id: str) -> bytes:
    return _CLIENT_AAD_PREFIX + json.dumps([issuer, client_id], separators=(",", ":")).encode()


async def save_client(issuer: str, registration: ClientRegistration) -> None:
    """Store a registration Seizu obtained from ``issuer``, replacing one with the same client ID."""
    if not (issuer and registration.client_id and registration.redirect_uri):
        raise ValueError("a client registration needs an issuer, a client ID and a redirect URI")
    cipher = encryption.delegated_token_cipher()
    sealed = await cipher.encrypt(
        json.dumps(asdict(registration), separators=(",", ":")).encode(),
        _client_associated_data(issuer, registration.client_id),
    )
    await report_store.put_oauth_client(
        {
            "issuer": issuer,
            "client_id": registration.client_id,
            "redirect_uri": registration.redirect_uri,
            "key_id": cipher.key_id(sealed),
            "ciphertext": base64.b64encode(sealed).decode("ascii"),
        }
    )


async def _open_client(row: dict[str, Any] | None) -> ClientRegistration | None:
    if row is None:
        return None
    cipher = encryption.delegated_token_cipher()
    try:
        sealed = base64.b64decode(row["ciphertext"], validate=True)
        data = json.loads(await cipher.decrypt(sealed, _client_associated_data(row["issuer"], row["client_id"])))
        registration = ClientRegistration(**data)
    except (binascii.Error, encryption.DecryptionError, ValueError, TypeError) as exc:
        raise VaultError(f"stored client registration for {row['issuer']!r} cannot be read") from exc
    if registration.client_id != row["client_id"] or registration.redirect_uri != row["redirect_uri"]:
        raise VaultError(f"stored client registration for {row['issuer']!r} does not match its row")
    return registration


async def load_client(issuer: str, client_id: str) -> ClientRegistration | None:
    """Return the registration a grant's refresh needs, or None if it is gone."""
    return await _open_client(await report_store.get_oauth_client(issuer, client_id))


async def find_client(issuer: str, redirect_uri: str) -> ClientRegistration | None:
    """Return the newest registration with ``issuer`` for ``redirect_uri``, or None."""
    return await _open_client(await report_store.find_oauth_client(issuer, redirect_uri))
