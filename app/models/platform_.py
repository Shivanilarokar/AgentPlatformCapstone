"""Tables in the SHARED `platform` schema.

Everything here is deliberately visible across companies, and there is very
little of it: who exists, and the marketplace. The brief calls the marketplace
"the single deliberate exception - which is exactly why an admin guards it".

These models DO name their schema, and that is correct: `platform` is the shared
one. No model may ever name a TENANT schema (t_...) - see
tests/graded/test_no_schema_qualified_queries.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
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
    shared_by: Mapped[str] = mapped_column(String(120), default="")  # company name
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
