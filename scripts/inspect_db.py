"""Print what is actually in Postgres right now.

    uv run python scripts/inspect_db.py

Requires `docker compose up` to be running.
"""

import asyncio

from sqlalchemy import text

from app.core.db import engine


def table(rows, headers):
    if not rows:
        print("   (no rows)")
        return
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
    print("   " + " | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("   " + "-+-".join("-" * w for w in widths))
    for r in rows:
        print("   " + " | ".join(str(c).ljust(w) for c, w in zip(r, widths)))


async def main() -> None:
    async with engine.connect() as c:
        print("\n=== SCHEMAS (one per company, plus the shared 'platform') ===")
        rows = (await c.execute(text("""
            SELECT nspname,
                   (SELECT count(*) FROM information_schema.tables t
                     WHERE t.table_schema = n.nspname)
            FROM pg_namespace n
            WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'
            ORDER BY 1"""))).all()
        table([tuple(r) for r in rows], ["schema", "tables"])

        tenants = [r[0] for r in rows if str(r[0]).startswith("t_")]

        print("\n=== ROWS ===")
        for schema in tenants:
            print(f"\n   {schema}.agents")
            got = (await c.execute(text(
                f'SELECT id, name, config FROM "{schema}".agents ORDER BY name'))).all()
            table([(str(r[0])[:8] + "...", r[1], str(r[2])) for r in got],
                  ["id", "name", "config"])

        print("\n=== SAME QUERY, DIFFERENT ANSWER, DECIDED BY search_path ===")
        for schema in tenants:
            await c.execute(text(f'SET search_path TO "{schema}", platform'))
            got = (await c.execute(text("SELECT name FROM agents"))).all()
            print(f"   search_path={schema:10} SELECT name FROM agents -> {[r[0] for r in got]}")
        print("   (no WHERE clause anywhere)\n")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
