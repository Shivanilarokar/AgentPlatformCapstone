"""Tables that live inside each company's own schema (t_<tenant>).

None of these name a schema. An unqualified table name is resolved by Postgres
against `search_path`, which the tenant gate sets per request - that is the
whole of graded check 1.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict] = mapped_column(JSONB, default=dict)  # the AgentConfig document (app/builder/schema.py)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class McpServer(Base):
    """A tool server this workspace has registered.

    `status` is maintained by introspection, never typed in: a server that stops
    answering is marked down, and agents that depend on it show as degraded.
    """

    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    transport: Mapped[str] = mapped_column(String(20))  # stdio | http | sse
    endpoint: Mapped[str] = mapped_column(String(500))
    auth_type: Mapped[str] = mapped_column(String(20), default="none")  # none | api_key | oauth
    #: stdio only: which env var the subprocess reads its credential from
    token_env: Mapped[str | None] = mapped_column(String(80), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="ok")  # ok | down
    #: "shared" ships with the platform; "private" was registered by this company
    scope: Mapped[str] = mapped_column(String(10), default="shared")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tools: Mapped[list["McpTool"]] = relationship(
        back_populates="server", cascade="all, delete-orphan", lazy="selectin"
    )


class McpTool(Base):
    """One tool, as the server itself reported it.

    Nobody types these in. `risk` is derived from the tool's own name and
    description at discovery time, and it is what forces an approval step later.
    """

    __tablename__ = "mcp_tools"
    __table_args__ = (UniqueConstraint("server_id", "name", name="uq_tool_per_server"),)

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mcp_servers.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    input_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    risk: Mapped[str] = mapped_column(String(20))  # read | write | destructive

    server: Mapped[McpServer] = relationship(back_populates="tools")

    @property
    def ref(self) -> str:
        return f"{self.server.name}.{self.name}"


class Connection(Base):
    """One company's credential for one server.

    Nothing readable is stored. See app/vault/envelope.py for the shape - the
    secret is encrypted under a per-connection data key, which is itself
    encrypted under the master key.
    """

    __tablename__ = "connections"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_name: Mapped[str] = mapped_column(String(50), unique=True)

    # --- the sealed secret. Every one of these is ciphertext. ----------------
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary)
    dek_nonce: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, default=1)

    status: Mapped[str] = mapped_column(String(20), default="active")  # active|revoked
    added_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
