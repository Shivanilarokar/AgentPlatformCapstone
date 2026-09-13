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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

#: Filled in by Postgres from the session variable the gate sets, so no handler
#: ever passes an owner - and row-level security refuses a row whose owner is
#: not the signed-in user. See app/tenancy/provision.py for the policies.
CURRENT_USER = text("NULLIF(current_setting('app.user_id', true), '')::uuid")


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), server_default=CURRENT_USER)
    name: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict] = mapped_column(JSONB, default=dict)  # the AgentConfig document (app/builder/schema.py)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    #: set when this agent was installed from the marketplace: the listing it
    #: was copied from. The original is never touched.
    installed_from: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    #: the last computed score, with every check behind it (app/scoring)
    quality_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    safety_grade: Mapped[str | None] = mapped_column(String(1), nullable=True)
    checks: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Run(Base):
    """One execution of one agent, by one person. The playground's history and
    the numbers the score is built from."""

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), server_default=CURRENT_USER)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    #: LangGraph thread. The owner is part of the key, so nobody else can name it.
    thread_id: Mapped[str] = mapped_column(String(120))
    trigger: Mapped[str] = mapped_column(String(20), default="playground")  # playground | api
    #: running | awaiting_approval | ok | rejected | error
    status: Mapped[str] = mapped_column(String(20), default="running")
    input: Mapped[str] = mapped_column(Text, default="")
    #: the agent's final answer - a summary string, never a raw tool response
    output: Mapped[str] = mapped_column(Text, default="")
    #: what happened, one line per step, as shown in the chat
    transcript: Mapped[list] = mapped_column(JSONB, default=list)
    #: the approval the run is parked on, if any (redacted args only)
    pending: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1 | -1
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Submission(Base):
    """One request to publish one agent. The publish graph is parked on an
    admin_review interrupt while status is `pending`; the admin's answer
    resumes it. The sanitized listing is frozen here at submit time so what the
    admin approves is exactly what goes live."""

    __tablename__ = "submissions"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), server_default=CURRENT_USER)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    thread_id: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | changes_requested | rejected
    listing: Mapped[dict] = mapped_column(JSONB, default=dict)  # the sanitized projection
    score: Mapped[dict] = mapped_column(JSONB, default=dict)    # the score at submit time
    notes: Mapped[str] = mapped_column(Text, default="")        # the admin's words, back to the author
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class McpServer(Base):
    """A tool server this workspace has registered.

    `health` is maintained by introspection, never typed in: a server that stops
    answering is marked down, and agents that depend on it show as degraded.
    """

    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_server_per_owner"),)

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), server_default=CURRENT_USER)
    name: Mapped[str] = mapped_column(String(50))
    transport: Mapped[str] = mapped_column(String(20))  # stdio | http | sse
    endpoint: Mapped[str] = mapped_column(String(500))
    auth_type: Mapped[str] = mapped_column(String(20), default="none")  # none | api_key | oauth
    #: stdio only: which env var the subprocess reads its credential from
    credential_env_var: Mapped[str | None] = mapped_column(String(80), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    health: Mapped[str] = mapped_column(String(10), default="ok")  # ok | down
    #: "private" = only the person who registered it. "company" = everyone in
    #: this company (admins only can set it). Row-level security enforces both.
    visibility: Mapped[str] = mapped_column(String(10), default="private")
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
    """One PERSON's credential for one server.

    Nothing readable is stored. See app/vault/envelope.py for the shape - the
    secret is encrypted under a per-connection data key, which is itself
    encrypted under the master key.
    """

    __tablename__ = "connections"
    __table_args__ = (UniqueConstraint("owner_id", "server_name", name="uq_connection_per_owner"),)

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), server_default=CURRENT_USER)
    server_name: Mapped[str] = mapped_column(String(50))

    # --- the sealed secret. Every one of these is encrypted_secret. ----------------
    encrypted_secret: Mapped[bytes] = mapped_column(LargeBinary)
    secret_nonce: Mapped[bytes] = mapped_column(LargeBinary)
    encrypted_data_key: Mapped[bytes] = mapped_column(LargeBinary)
    data_key_nonce: Mapped[bytes] = mapped_column(LargeBinary)
    master_key_version: Mapped[int] = mapped_column(Integer, default=1)

    status: Mapped[str] = mapped_column(String(20), default="active")  # active|revoked
    added_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
