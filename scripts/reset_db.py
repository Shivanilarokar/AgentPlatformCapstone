"""Drop every schema and rebuild them from the models.

    uv run python scripts/reset_db.py

WHY THIS EXISTS, AND WHY IT SHOULD NOT
--------------------------------------
There is no migration tool in this project yet. SQLAlchemy's create_all() adds
missing TABLES but never missing COLUMNS, so adding a field to a model leaves
every existing workspace with the old shape and a 500 at runtime.

Until Alembic is wired in, this is the escape hatch: destroy and rebuild. It is
fine in development and completely unacceptable afterwards - the first time a
grader has data in there, this becomes unusable and the missing migrations
become a real problem.

Treat this file as a reminder, not a solution.
"""

from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text  # noqa: E402

from app.core.db import engine  # noqa: E402
from app.models.platform_ import PLATFORM_SCHEMA  # noqa: E402
from app.tenancy.provision import bootstrap_platform, ensure_platform_admin  # noqa: E402


async def main() -> int:
    async with engine.begin() as conn:
        rows = await conn.execute(text("""
            SELECT nspname FROM pg_namespace
            WHERE nspname LIKE 't\\_%' OR nspname = :platform
        """), {"platform": PLATFORM_SCHEMA})
        schemas = [r[0] for r in rows]

        for schema in schemas:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            if schema != PLATFORM_SCHEMA:  # the company's database role goes with it
                await conn.execute(text(f"""
                    DO $$ BEGIN
                        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{schema}') THEN
                            EXECUTE 'DROP OWNED BY "{schema}"'; EXECUTE 'DROP ROLE "{schema}"';
                        END IF;
                    END $$"""))
            print(f"  dropped {schema}")

    await bootstrap_platform()
    await ensure_platform_admin()
    print(f"\n  rebuilt {PLATFORM_SCHEMA}; platform admin re-seeded from .env")
    print("  every workspace is gone - sign up again at http://localhost:5173\n")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
