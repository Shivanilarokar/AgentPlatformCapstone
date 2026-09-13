"""Creating a company: one CREATE SCHEMA, that company's tables inside it, the
row-level security that keeps each PERSON's rows their own, and a database ROLE
that can reach this schema and no other.

Three separate jobs, deliberately:

  bootstrap_platform()    the shared schema - runs once, at startup
  ensure_platform_admin() the one platform admin, from .env - runs at startup
  create_tenant()         one company's private schema - runs on sign-up
"""

from sqlalchemy import select, text

from app.core.config import settings
from app.core.db import engine, platform_session
from app.core.security import hash_password
from app.models import tenant as _tenant_models  # noqa: F401 - registers the tables on Base
from app.models.base import Base
from app.models.platform_ import PLATFORM_SCHEMA, PlatformBase, User
from app.tenancy.schema_names import schema_for

#: Who may see a row: its owner, or the system role the health sweep runs as.
_ME = "NULLIF(current_setting('app.user_id', true), '')::uuid"
_SYSTEM = "current_setting('app.role', true) = 'system'"

#: Row-level security, per table. Applied inside the company's schema, so the
#: unqualified names resolve there. FORCE makes it apply to the table owner too
#: (the app connects as the owner), otherwise Postgres would skip it for us.
_POLICIES = {
    # what I built is mine
    "agents": (f"owner_id = {_ME} OR {_SYSTEM}", f"owner_id = {_ME} OR {_SYSTEM}"),
    # what I ran is mine
    "runs": (f"owner_id = {_ME} OR {_SYSTEM}", f"owner_id = {_ME} OR {_SYSTEM}"),
    # my credentials are mine
    "connections": (f"owner_id = {_ME} OR {_SYSTEM}", f"owner_id = {_ME} OR {_SYSTEM}"),
    # a server I registered is mine, unless the company admin opened it to the company
    "mcp_servers": (
        f"owner_id = {_ME} OR visibility = 'company' OR {_SYSTEM}",
        f"owner_id = {_ME} OR {_SYSTEM}",
    ),
    # a tool is visible exactly when its server is (the subquery is itself RLS-filtered)
    "mcp_tools": (
        "EXISTS (SELECT 1 FROM mcp_servers s WHERE s.id = mcp_tools.server_id)",
        "EXISTS (SELECT 1 FROM mcp_servers s WHERE s.id = mcp_tools.server_id)",
    ),
}


async def bootstrap_platform() -> None:
    """The shared schema: tenants, users, shared servers, later the marketplace."""
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{PLATFORM_SCHEMA}"'))
        await conn.run_sync(PlatformBase.metadata.create_all)


async def ensure_platform_admin() -> None:
    """Exactly one platform admin, from .env. Sign-up can never create one."""
    async with platform_session() as s:
        existing = await s.scalar(select(User).where(User.role == "platform_admin"))
        if existing is None:
            s.add(User(
                tenant_id=None,
                email=settings.platform_admin_email.lower(),
                password_hash=hash_password(settings.platform_admin_password),
                name="Platform admin",
                role="platform_admin",
            ))


async def create_tenant(tenant_key: str) -> str:
    """CREATE SCHEMA t_<key>, build this company's tables inside it, lock rows to
    their owners, and create the role a request will run as.

    The role has the same name as the schema. It can USE this schema and read the
    shared platform tables - nothing else. So even a request whose search_path
    somehow named another company would get "permission denied for schema", not
    another company's rows.
    """
    schema = schema_for(tenant_key)
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        # create_all issues unqualified CREATE TABLE, so search_path decides
        # which schema they land in. Same trick as the request-time gate.
        await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        await conn.run_sync(Base.metadata.create_all)
        for table, (using, check) in _POLICIES.items():
            await conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
            await conn.execute(text(f"DROP POLICY IF EXISTS owner_only ON {table}"))
            await conn.execute(text(
                f"CREATE POLICY owner_only ON {table} USING ({using}) WITH CHECK ({check})"
            ))

        # --- the company's role ------------------------------------------
        await conn.execute(text(f"""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{schema}') THEN
                    CREATE ROLE "{schema}" NOLOGIN NOBYPASSRLS;
                END IF;
            END $$
        """))
        await conn.execute(text(f'GRANT "{schema}" TO CURRENT_USER'))  # so the app may SET ROLE to it
        await conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{schema}"'))
        await conn.execute(text(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{schema}"'
        ))
        await conn.execute(text(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{schema}"'
        ))
        await conn.execute(text(f'GRANT USAGE ON SCHEMA "{PLATFORM_SCHEMA}" TO "{schema}"'))
        await conn.execute(text(
            f'GRANT SELECT ON "{PLATFORM_SCHEMA}".tenants, "{PLATFORM_SCHEMA}".users, '
            f'"{PLATFORM_SCHEMA}".mcp_servers, "{PLATFORM_SCHEMA}".mcp_tools TO "{schema}"'
        ))
    return schema


async def drop_tenant(tenant_key: str) -> None:
    """Used by tests to start from a known-empty state."""
    schema = schema_for(tenant_key)
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await conn.execute(text(f"""
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{schema}') THEN
                    EXECUTE 'DROP OWNED BY "{schema}"';
                    EXECUTE 'DROP ROLE "{schema}"';
                END IF;
            END $$
        """))
