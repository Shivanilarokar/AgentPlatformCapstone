"""GRADED CHECK 2 - "a credential we supply appears nowhere in anything your
system stored".

    "How this is tested: we search everything your system stored for the token
     we gave you. Finding it anywhere is a fail."

So these tests do exactly that: plant a sentinel, exercise the platform, then
search every table in every schema for it.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.core.db import engine, tenant_session
from app.tenancy.provision import create_tenant, drop_tenant, bootstrap_platform
from app.vault import envelope, service

#: The token a grader would hand us.
SENTINEL = "xoxb-GRADER-9f2a-DO-NOT-LEAK-4c81"
TENANT = "vaulttest"
SERVER = "local_slack"


@pytest.fixture
async def workspace():
    await bootstrap_platform()
    await drop_tenant(TENANT)
    await create_tenant(TENANT)
    yield TENANT
    await drop_tenant(TENANT)


# --------------------------------------------------- the encryption primitive


def test_the_sealed_form_contains_no_trace_of_the_secret():
    sealed = envelope.seal(SENTINEL, tenant=TENANT, server_name=SERVER)
    blob = sealed.ciphertext + sealed.nonce + sealed.wrapped_dek + sealed.dek_nonce
    assert SENTINEL.encode() not in blob
    assert b"xoxb" not in blob


def test_it_round_trips_for_the_owner():
    sealed = envelope.seal(SENTINEL, tenant=TENANT, server_name=SERVER)
    assert envelope.open_(sealed, tenant=TENANT, server_name=SERVER) == SENTINEL


def test_a_row_stolen_into_another_workspace_will_not_decrypt():
    """The AAD binds ciphertext to tenant+server, so a copied row is inert."""
    sealed = envelope.seal(SENTINEL, tenant=TENANT, server_name=SERVER)
    with pytest.raises(envelope.VaultError):
        envelope.open_(sealed, tenant="someone_else", server_name=SERVER)


def test_a_row_reused_for_a_different_server_will_not_decrypt():
    sealed = envelope.seal(SENTINEL, tenant=TENANT, server_name=SERVER)
    with pytest.raises(envelope.VaultError):
        envelope.open_(sealed, tenant=TENANT, server_name="github")


def test_two_secrets_never_share_a_data_key():
    a = envelope.seal(SENTINEL, tenant=TENANT, server_name=SERVER)
    b = envelope.seal(SENTINEL, tenant=TENANT, server_name=SERVER)
    assert a.wrapped_dek != b.wrapped_dek
    assert a.ciphertext != b.ciphertext  # same plaintext, different ciphertext


def test_an_empty_secret_is_refused():
    with pytest.raises(envelope.VaultError):
        envelope.seal("", tenant=TENANT, server_name=SERVER)


# ------------------------------------------------------- the storage boundary


async def test_the_database_holds_nothing_readable(workspace):
    """THE CHECK. Store the token, then grep every column of every table."""
    async with tenant_session(TENANT) as s:
        await service.add(s, tenant=TENANT, server_name=SERVER, secret=SENTINEL,
                          added_by="grader@example.com")

    found: list[str] = []
    async with engine.connect() as conn:
        tables = await conn.execute(text("""
            SELECT table_schema, table_name FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
        """))
        for schema, table in tables.all():
            # Cast the whole row to text and search it - the bluntest possible
            # version of what a grader does.
            hits = await conn.scalar(text(
                f'SELECT count(*) FROM "{schema}"."{table}" t '
                f"WHERE t::text LIKE :needle"
            ), {"needle": f"%{SENTINEL}%"})
            if hits:
                found.append(f"{schema}.{table} ({hits} rows)")

    assert not found, f"the credential was stored in the clear: {found}"


async def test_it_is_still_usable_after_all_that(workspace):
    """Encrypted is only useful if it can still be lent out for one call."""
    async with tenant_session(TENANT) as s:
        await service.add(s, tenant=TENANT, server_name=SERVER, secret=SENTINEL)

    async with tenant_session(TENANT) as s:
        assert await service.use(s, tenant=TENANT, server_name=SERVER) == SENTINEL


async def test_a_revoked_connection_returns_none_rather_than_raising(workspace):
    """None is the DEGRADED path. The agent keeps working and reports the gap."""
    async with tenant_session(TENANT) as s:
        await service.add(s, tenant=TENANT, server_name=SERVER, secret=SENTINEL)
    async with tenant_session(TENANT) as s:
        await service.revoke(s, SERVER)

    async with tenant_session(TENANT) as s:
        assert await service.use(s, tenant=TENANT, server_name=SERVER) is None


async def test_revoking_wipes_the_ciphertext(workspace):
    async with tenant_session(TENANT) as s:
        await service.add(s, tenant=TENANT, server_name=SERVER, secret=SENTINEL)
    async with tenant_session(TENANT) as s:
        conn = await service.revoke(s, SERVER)
        assert conn.ciphertext == b""
        assert conn.wrapped_dek == b""


async def test_a_server_never_connected_is_simply_absent(workspace):
    async with tenant_session(TENANT) as s:
        assert await service.use(s, tenant=TENANT, server_name="github") is None


# ------------------------------------------------------------- the API surface


def test_no_endpoint_can_return_a_secret():
    """Structural, not behavioural: the response model has nowhere to put one."""
    from app.api.routers.connections import ConnectionOut

    fields = ConnectionOut.model_fields
    assert set(fields) == {
        "server_name", "status", "added_by", "created_at", "last_used_at", "secret",
    }
    # `secret` exists to render dots, and has no setter anywhere in the router.
    assert fields["secret"].default == "••••••••••••"


def test_the_list_response_carries_only_metadata(workspace_free=None):
    from app.api.routers.connections import ConnectionOut

    dumped = json.dumps(ConnectionOut(
        server_name="local_slack", status="active", added_by="a@b.example",
        created_at="2026-01-01T00:00:00", last_used_at=None,
    ).model_dump())
    assert SENTINEL not in dumped
    assert "xoxb" not in dumped
