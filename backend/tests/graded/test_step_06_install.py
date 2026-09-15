"""Step 6 - someone in a different company installs a published agent.

    "They get their own copy, and they are asked to supply their own
     credentials. They never touch the original. The original owner's tokens
     never go anywhere."
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import delete, select

from app.builder.schema import AgentConfig
from app.core.db import platform_session, tenant_session
from app.models.platform_ import Listing
from app.models.tenant import Agent, Connection
from app.publishing.sanitize import listing_from
from app.vault import connections
from tests.conftest import ALPHA, ALPHA_ANNE, BETA, BETA_BEN
from tests.graded.test_check_04_sanitize import COMPANY, planted_config

SENTINEL = "ghp_ANNES_TOKEN_MUST_NOT_TRAVEL_0000000000"


async def _published_by_anne() -> Listing:
    """Anne has an agent AND a github credential. Only the design is listed."""
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        s.add(Agent(name="original", config=planted_config().model_dump(mode="json"), status="live"))
        await connections.add(s, tenant=ALPHA, user_id=ALPHA_ANNE, server_name="github", secret=SENTINEL)
    async with platform_session() as p:
        row = Listing(submission_id=uuid.uuid4(), name="Issue Digest", description="triage",
                      config=listing_from(planted_config(), company=COMPANY), publisher=COMPANY,
                      quality=90, grade="A")
        p.add(row)
        await p.flush()
        await p.refresh(row)
        return row


async def _install_as_ben(listing: Listing) -> Agent:
    """What POST /v1/listings/{id}/install does, minus HTTP."""
    async with tenant_session(BETA, BETA_BEN) as s:
        cfg = AgentConfig.model_validate(listing.config)
        agent = Agent(name=listing.name, config=listing.config, status="draft", installed_from=listing.id)
        s.add(agent)
        await s.flush()
        await s.refresh(agent)
        assert cfg.requires_connections == ["github"]
        return agent


async def _cleanup(listing: Listing) -> None:
    async with platform_session() as p:
        await p.execute(delete(Listing).where(Listing.id == listing.id))
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        await s.execute(delete(Connection).where(Connection.server_name == "github"))
        await s.execute(delete(Agent).where(Agent.name == "original"))
    async with tenant_session(BETA, BETA_BEN) as s:
        await s.execute(delete(Agent).where(Agent.installed_from == listing.id))


async def test_the_installer_gets_their_own_copy_in_their_own_schema():
    listing = await _published_by_anne()
    copy = await _install_as_ben(listing)
    assert str(copy.owner_id) == BETA_BEN
    async with tenant_session(BETA, BETA_BEN) as s:
        assert await s.scalar(select(Agent).where(Agent.id == copy.id)) is not None
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:  # not visible to the publisher
        assert await s.scalar(select(Agent).where(Agent.id == copy.id)) is None
    await _cleanup(listing)


async def test_the_copy_names_servers_so_it_resolves_to_the_installers_credentials():
    listing = await _published_by_anne()
    copy = await _install_as_ben(listing)
    for t in copy.config["tools"]:
        assert t["requires_connection"] == "github"  # a KIND of connection, never a row id
    async with tenant_session(BETA, BETA_BEN) as s:
        # Ben has not connected github: the runtime degrades; it cannot borrow Anne's
        assert await connections.use(s, tenant=BETA, user_id=BETA_BEN, server_name="github") is None
        assert await s.scalar(select(Connection)) is None
    await _cleanup(listing)


async def test_the_publishers_token_never_travels():
    listing = await _published_by_anne()
    copy = await _install_as_ben(listing)
    assert SENTINEL not in json.dumps(listing.config)
    assert SENTINEL not in json.dumps(copy.config)
    async with tenant_session(BETA, BETA_BEN) as s:
        assert list(await s.scalars(select(Connection))) == []
    await _cleanup(listing)
