"""Token vault tests against the real SQL store on in-memory SQLite."""

import asyncio
import base64
import os
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from reporting.services import report_store, token_vault
from reporting.services.report_store.sql import SQLModelReportStore
from reporting.services.token_vault import DelegatedGrant, VaultError

GATEWAY = "https://whoami-a.gateway.test"


def _key_text() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


@pytest.fixture(autouse=True)
def _vault_key(mocker):
    mocker.patch("reporting.settings.DELEGATED_TOKEN_ENCRYPTION_KEYS", f"k1:{_key_text()}")


@pytest.fixture(autouse=True)
async def _store():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    store = SQLModelReportStore()
    with (
        patch("reporting.services.report_store.sql._get_engine", return_value=engine),
        patch.object(report_store, "get_store", return_value=store),
    ):
        yield store
    await engine.dispose()


def _grant(refresh: str = "rt-1", access: str | None = "at-1") -> DelegatedGrant:
    return DelegatedGrant(
        refresh_token=refresh,
        token_endpoint=f"{GATEWAY}/.pomerium/mcp/token",
        client_id="seizu-client",
        access_token=access,
        access_token_expires_at=2_000_000_000,
        scope="mcp",
        issuer=GATEWAY,
    )


async def test_save_then_load_round_trips_the_grant():
    version = await token_vault.save("alice", "corp", GATEWAY, _grant())
    stored = await token_vault.load("alice", "corp", GATEWAY)
    assert stored is not None
    assert stored.grant == _grant()
    assert stored.version == version == 1
    assert stored.key_id == "k1"


async def test_load_returns_none_before_the_user_connects():
    assert await token_vault.load("alice", "corp", GATEWAY) is None


async def test_no_secret_is_stored_in_the_clear(_store):
    await token_vault.save("alice", "corp", GATEWAY, _grant(refresh="rt-secret", access="at-secret"))
    row = await _store.get_delegated_token("alice", "corp", GATEWAY)
    stored = repr(row)
    for secret in ("rt-secret", "at-secret", "seizu-client", ".pomerium/mcp/token"):
        assert secret not in stored
        assert secret.encode() not in base64.b64decode(row["ciphertext"])


async def test_grants_are_owner_scoped():
    await token_vault.save("alice", "corp", GATEWAY, _grant(refresh="rt-alice"))
    await token_vault.save("bob", "corp", GATEWAY, _grant(refresh="rt-bob"))
    alice = await token_vault.load("alice", "corp", GATEWAY)
    bob = await token_vault.load("bob", "corp", GATEWAY)
    assert alice is not None and alice.grant.refresh_token == "rt-alice"
    assert bob is not None and bob.grant.refresh_token == "rt-bob"


async def test_a_ciphertext_copied_to_another_users_row_does_not_decrypt(_store):
    await token_vault.save("alice", "corp", GATEWAY, _grant(refresh="rt-alice"))
    await token_vault.save("bob", "corp", GATEWAY, _grant(refresh="rt-bob"))
    alice_row = await _store.get_delegated_token("alice", "corp", GATEWAY)
    await _store.put_delegated_token({**alice_row, "user_id": "bob"}, expected_version=None)
    with pytest.raises(VaultError):
        await token_vault.load("bob", "corp", GATEWAY)


async def test_a_ciphertext_moved_to_another_resource_does_not_decrypt(_store):
    await token_vault.save("alice", "corp", GATEWAY, _grant())
    row = await _store.get_delegated_token("alice", "corp", GATEWAY)
    await _store.put_delegated_token({**row, "resource": "https://other.gateway.test"}, expected_version=None)
    with pytest.raises(VaultError):
        await token_vault.load("alice", "corp", "https://other.gateway.test")


async def test_replace_succeeds_only_at_the_expected_version():
    await token_vault.save("alice", "corp", GATEWAY, _grant(refresh="rt-1"))
    assert await token_vault.replace("alice", "corp", GATEWAY, _grant(refresh="rt-2"), expected_version=1) == 2
    # A second writer that also read version 1 loses and must reload.
    assert await token_vault.replace("alice", "corp", GATEWAY, _grant(refresh="rt-x"), expected_version=1) is None
    stored = await token_vault.load("alice", "corp", GATEWAY)
    assert stored is not None
    assert stored.grant.refresh_token == "rt-2"
    assert stored.version == 2


async def test_concurrent_rotations_have_exactly_one_winner():
    await token_vault.save("alice", "corp", GATEWAY, _grant(refresh="rt-1"))
    results = await asyncio.gather(
        *(
            token_vault.replace("alice", "corp", GATEWAY, _grant(refresh=f"rt-{i}"), expected_version=1)
            for i in range(2, 6)
        )
    )
    assert [version for version in results if version is not None] == [2]
    assert results.count(None) == 3


async def test_replace_never_recreates_a_deleted_grant():
    await token_vault.save("alice", "corp", GATEWAY, _grant())
    await token_vault.delete("alice", "corp", GATEWAY)
    assert await token_vault.replace("alice", "corp", GATEWAY, _grant(refresh="rt-2"), expected_version=1) is None
    assert await token_vault.load("alice", "corp", GATEWAY) is None


async def test_save_overwrites_and_advances_the_version():
    await token_vault.save("alice", "corp", GATEWAY, _grant(refresh="rt-1"))
    assert await token_vault.save("alice", "corp", GATEWAY, _grant(refresh="rt-reconnected")) == 2
    stored = await token_vault.load("alice", "corp", GATEWAY)
    assert stored is not None and stored.grant.refresh_token == "rt-reconnected"


async def test_rotation_keeps_old_grants_readable_and_rewrites_with_the_new_key(mocker):
    old = _key_text()
    mocker.patch("reporting.settings.DELEGATED_TOKEN_ENCRYPTION_KEYS", f"old:{old}")
    await token_vault.save("alice", "corp", GATEWAY, _grant(refresh="rt-1"))
    mocker.patch("reporting.settings.DELEGATED_TOKEN_ENCRYPTION_KEYS", f"new:{_key_text()},old:{old}")
    stored = await token_vault.load("alice", "corp", GATEWAY)
    assert stored is not None and stored.key_id == "old"
    await token_vault.replace("alice", "corp", GATEWAY, _grant(refresh="rt-2"), expected_version=stored.version)
    rewritten = await token_vault.load("alice", "corp", GATEWAY)
    assert rewritten is not None and rewritten.key_id == "new"
    assert [summary.key_id for summary in await token_vault.list_grants("alice")] == ["new"]


async def test_a_grant_whose_key_was_removed_is_reported_not_deleted(mocker):
    await token_vault.save("alice", "corp", GATEWAY, _grant())
    mocker.patch("reporting.settings.DELEGATED_TOKEN_ENCRYPTION_KEYS", f"other:{_key_text()}")
    with pytest.raises(VaultError, match="cannot be read"):
        await token_vault.load("alice", "corp", GATEWAY)
    assert len(await token_vault.list_grants("alice")) == 1


async def test_list_grants_returns_metadata_only():
    await token_vault.save("alice", "corp", GATEWAY, _grant())
    await token_vault.save("alice", "corp", "https://whoami-b.gateway.test", _grant())
    await token_vault.save("bob", "corp", GATEWAY, _grant())
    summaries = await token_vault.list_grants("alice")
    assert [(s.proxy_name, s.resource) for s in summaries] == [
        ("corp", GATEWAY),
        ("corp", "https://whoami-b.gateway.test"),
    ]
    assert not hasattr(summaries[0], "grant")


async def test_delete_narrows_by_proxy_and_resource():
    await token_vault.save("alice", "corp", GATEWAY, _grant())
    await token_vault.save("alice", "corp", "https://whoami-b.gateway.test", _grant())
    await token_vault.save("alice", "other", GATEWAY, _grant())
    await token_vault.save("bob", "corp", GATEWAY, _grant())
    assert await token_vault.delete("alice", "corp", GATEWAY) == 1
    assert await token_vault.delete("alice", "corp") == 1
    assert await token_vault.delete("alice") == 1
    assert await token_vault.list_grants("alice") == []
    assert len(await token_vault.list_grants("bob")) == 1


async def test_delete_refuses_a_resource_without_its_proxy():
    with pytest.raises(ValueError):
        await token_vault.delete("alice", resource=GATEWAY)


@pytest.mark.parametrize("missing", ["refresh_token", "token_endpoint", "client_id"])
async def test_save_refuses_an_incomplete_grant(missing):
    grant = DelegatedGrant(**{**_grant().__dict__, missing: ""})
    with pytest.raises(ValueError):
        await token_vault.save("alice", "corp", GATEWAY, grant)


async def test_the_vault_fails_closed_without_its_key(mocker):
    mocker.patch("reporting.settings.DELEGATED_TOKEN_ENCRYPTION_KEYS", "")
    with pytest.raises(RuntimeError, match="DELEGATED_TOKEN_ENCRYPTION_KEYS"):
        await token_vault.save("alice", "corp", GATEWAY, _grant())


# ── client registrations ───────────────────────────────────────────────────

ISSUER = "https://gateway.test"


def _registration(client_id: str = "client-1", redirect: str = "https://seizu.test/callback"):
    return token_vault.ClientRegistration(client_id=client_id, redirect_uri=redirect, client_secret="s3cret")


async def test_client_registrations_round_trip_by_id_and_by_redirect(_store):
    await token_vault.save_client(ISSUER, _registration())
    assert await token_vault.load_client(ISSUER, "client-1") == _registration()
    assert await token_vault.find_client(ISSUER, "https://seizu.test/callback") == _registration()
    assert await token_vault.find_client(ISSUER, "https://elsewhere.test/callback") is None
    assert await token_vault.load_client("https://other-issuer.test", "client-1") is None
    row = await _store.get_oauth_client(ISSUER, "client-1")
    assert b"s3cret" not in base64.b64decode(row["ciphertext"])


async def test_a_registration_moved_to_another_issuer_does_not_decrypt(_store):
    await token_vault.save_client(ISSUER, _registration())
    row = await _store.get_oauth_client(ISSUER, "client-1")
    await _store.put_oauth_client({**row, "issuer": "https://impostor.test"})
    with pytest.raises(VaultError):
        await token_vault.load_client("https://impostor.test", "client-1")


async def test_a_registration_whose_row_was_repointed_is_refused(_store):
    await token_vault.save_client(ISSUER, _registration())
    row = await _store.get_oauth_client(ISSUER, "client-1")
    await _store.put_oauth_client({**row, "redirect_uri": "https://attacker.test/callback"})
    with pytest.raises(VaultError, match="does not match"):
        await token_vault.load_client(ISSUER, "client-1")
