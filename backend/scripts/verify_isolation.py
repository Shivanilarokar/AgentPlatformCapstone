"""Prove both isolation layers against the RUNNING platform, over HTTP.

    uv run python scripts/verify_isolation.py

Creates two throwaway companies with two people each, registers a private
server as one person, then asks the API - as every other person - whether they
can see it. Also proves the platform admin has no workspace and that a
cross-company agent id is a plain 404. Leaves two throwaway companies behind
(reset_db.py clears them).

Needs `docker compose up` (API on :8000) and npx in the api container.
"""

from __future__ import annotations

import asyncio
import sys
import uuid

import httpx

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.config import settings  # noqa: E402

API = "http://localhost:8000"
PW = "Passw0rd!"
FS = "npx -y @modelcontextprotocol/server-filesystem /srv/workspace"


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{('  - ' + detail) if detail else ''}")
    return cond


async def person(client: httpx.AsyncClient, company: str, name: str) -> httpx.AsyncClient:
    email = f"{name}.{uuid.uuid4().hex[:6]}@{company.replace(' ', '-')}.example"
    r = await client.post(f"{API}/auth/register",
                          json={"company": company, "name": name, "email": email, "password": PW})
    r.raise_for_status()
    c = httpx.AsyncClient(cookies=r.cookies, timeout=180)
    c.role = r.json()["role"]  # type: ignore[attr-defined]
    return c


async def main() -> int:
    tag = uuid.uuid4().hex[:6]
    co_a, co_b = f"isotest a {tag}", f"isotest b {tag}"
    async with httpx.AsyncClient(timeout=60) as boot:
        anne = await person(boot, co_a, "anne")   # creates company A -> admin
        arun = await person(boot, co_a, "arun")   # joins A -> user
        ben = await person(boot, co_b, "ben")     # creates company B -> admin
        r = await boot.post(f"{API}/auth/login", json={"email": settings.platform_admin_email,
                                                       "password": settings.platform_admin_password})
        r.raise_for_status()
        platform = httpx.AsyncClient(cookies=r.cookies, timeout=60)

    results = []
    print("\nroles")
    results.append(ok("first sign-up for a company is its admin", anne.role == "admin"))
    results.append(ok("second sign-up for the same company is a user", arun.role == "user"))

    print("\nregistry - person layer")
    srv = f"iso_{tag}"
    r = await anne.post(f"{API}/v1/servers", json={"name": srv, "transport": "stdio", "endpoint": FS,
                                                   "auth_type": "none", "visibility": "private"})
    results.append(ok("anne registers a private server", r.status_code == 201, r.text[:80]))
    names = lambda c: {s["name"] for s in c}  # noqa: E731
    results.append(ok("anne sees it", srv in names((await anne.get(f"{API}/v1/servers")).json())))
    results.append(ok("arun (same company) does NOT see it",
                      srv not in names((await arun.get(f"{API}/v1/servers")).json())))
    results.append(ok("ben (other company) does NOT see it",
                      srv not in names((await ben.get(f"{API}/v1/servers")).json())))
    r = await arun.post(f"{API}/v1/servers", json={"name": srv, "transport": "stdio", "endpoint": FS,
                                                   "auth_type": "none", "visibility": "company"})
    results.append(ok("arun (user) may not share company-wide", r.status_code == 403))
    r = await anne.post(f"{API}/v1/servers", json={"name": srv, "transport": "stdio", "endpoint": FS,
                                                   "auth_type": "none", "visibility": "company"})
    results.append(ok("anne (admin) may share company-wide", r.status_code == 201))
    results.append(ok("arun now sees the company server",
                      srv in names((await arun.get(f"{API}/v1/servers")).json())))
    results.append(ok("ben still does not",
                      srv not in names((await ben.get(f"{API}/v1/servers")).json())))
    r = await anne.post(f"{API}/v1/servers", json={"name": srv, "transport": "stdio", "endpoint": FS,
                                                   "auth_type": "none", "visibility": "everyone"})
    results.append(ok("a company admin may not share with every company", r.status_code == 403))

    print("\nagents - company layer (check 9)")
    r = await anne.post(f"{API}/v1/builds/form", json={
        "prompt": "list files", "selected": [f"{srv}.list_directory"], "on_missing": "skip"})
    agent_id = r.json().get("agent_id")
    results.append(ok("anne builds an agent", bool(agent_id), r.text[:80]))
    if agent_id:
        results.append(ok("anne can open it", (await anne.get(f"{API}/v1/agents/{agent_id}")).status_code == 200))
        rb = await ben.get(f"{API}/v1/agents/{agent_id}")
        ru = await arun.get(f"{API}/v1/agents/{agent_id}")
        rx = await ben.get(f"{API}/v1/agents/{uuid.uuid4()}")
        results.append(ok("ben (other company) gets 404, not 403", rb.status_code == 404))
        results.append(ok("arun (colleague) gets 404 too", ru.status_code == 404))
        results.append(ok("404 body is byte-identical to a random id", rb.content == rx.content))

    print("\nplatform admin")
    r = await platform.get(f"{API}/v1/agents")
    results.append(ok("has no workspace: /v1/agents is 404", r.status_code == 404))
    r = await platform.get(f"{API}/v1/servers")
    results.append(ok("sees only servers shared with everyone",
                      all(s["visibility"] == "everyone" for s in r.json())))

    for c in (anne, arun, ben, platform):
        await c.aclose()

    print(f"\n{sum(results)}/{len(results)} passed")
    print("(throwaway companies 'isotest a/b' remain; `uv run python scripts/reset_db.py` clears them)\n")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
