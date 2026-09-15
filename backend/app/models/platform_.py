"""Tables in the SHARED `platform` schema.

Everything here is deliberately visible across companies, and there is very
little of it: who exists, what the platform admin shared, the review index, and
the marketplace. The brief calls the marketplace
"the single deliberate exception - which is exactly why an admin guards it".

These models DO name their schema, and that is correct: `platform` is the shared
one. No model may ever name a TENANT schema (t_...) - see
tests/graded/test_no_schema_qualified_queries.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

PLATFORM_SCHEMA = "platform"


class PlatformBase(DeclarativeBase):
    """Separate base so platform tables are created once, not per tenant."""


class Tenant(PlatformBase):
    __tablename__ = "tenants"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    schema_key: Mapped[str] = mapped_column(String(50), unique=True)  # "northwind_labs" -> schema t_northwind_labs
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(PlatformBase):
    __tablename__ = "users"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: NULL for the platform admin, who belongs to no company.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{PLATFORM_SCHEMA}.tenants.id"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="user")  # platform_admin | admin | user
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SharedServer(PlatformBase):
    """An MCP server shared with EVERY company. Only the platform admin puts one here.

    Same shape as the per-tenant McpServer; the difference is purely where it
    lives. A tenant's registry is the union of its own servers and these.
    """

    __tablename__ = "mcp_servers"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    transport: Mapped[str] = mapped_column(String(20))
    endpoint: Mapped[str] = mapped_column(String(500))
    auth_type: Mapped[str] = mapped_column(String(20), default="none")
    credential_env_var: Mapped[str | None] = mapped_column(String(80), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    health: Mapped[str] = mapped_column(String(10), default="ok")
    shared_by: Mapped[str] = mapped_column(String(120), default="platform")  # only the platform admin writes here
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SharedTool(PlatformBase):
    __tablename__ = "mcp_tools"
    __table_args__ = (
        UniqueConstraint("server_id", "name", name="uq_shared_tool_per_server"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{PLATFORM_SCHEMA}.mcp_servers.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    input_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    risk: Mapped[str] = mapped_column(String(20))


class SubmissionIndex(PlatformBase):
    """The ONE deliberate cross-company path. The platform admin has no company,
    so they cannot see t_<company>.submissions; this row tells them a submission
    exists, whose it is, and which parked thread to resume. Nothing sensitive:
    the listing here is already sanitized."""

    __tablename__ = "submission_index"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    submission_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_key: Mapped[str] = mapped_column(String(50))
    company: Mapped[str] = mapped_column(String(120))
    owner_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    agent_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    thread_id: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    listing: Mapped[dict] = mapped_column(JSONB, default=dict)
    quality: Mapped[int] = mapped_column(Integer, default=0)
    grade: Mapped[str] = mapped_column(String(1), default="D")
    checks: Mapped[dict] = mapped_column(JSONB, default=dict)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Listing(PlatformBase):
    """The marketplace. Global on purpose - the brief's single exception - and
    populated by exactly one code path: an approved admin_review interrupt."""

    __tablename__ = "listings"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    config: Mapped[dict] = mapped_column(JSONB, default=dict)  # the sanitized design; install copies this
    publisher: Mapped[str] = mapped_column(String(120))        # company name only
    quality: Mapped[int] = mapped_column(Integer, default=0)
    grade: Mapped[str] = mapped_column(String(1), default="D")
    installs: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiToken(PlatformBase):
    """A long-lived credential for calling agents from OUTSIDE the UI (Rule 7).

    Only a hash is stored; the plaintext is shown once, at creation. The token
    resolves to a person, so a call with it is that person's call: their
    company's schema, their rows, their connections. Another company's token
    asking for this agent finds nothing - 404, never 403."""

    __tablename__ = "api_tokens"
    __table_args__ = {"schema": PLATFORM_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{PLATFORM_SCHEMA}.users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 hex
    prefix: Mapped[str] = mapped_column(String(12))  # "forge_ab12" - what the list shows
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
