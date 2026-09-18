import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.db import engine
from app.api.routers import agents, auth, builds, connections, public_api, publishing, runs, servers
from app.mcp_registry import health as health_sweep
from app.tenancy.checkpointers import close_all
from app.tenancy.provision import bootstrap_platform, ensure_platform_admin

@asynccontextmanager
async def lifespan(app: FastAPI):
    # The shared schema must exist before anyone can sign up.
    await bootstrap_platform()
    await ensure_platform_admin()
    health_sweep.start()
    yield
    await health_sweep.stop()
    await close_all()
    await engine.dispose()


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Agent Platform", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(servers.router)
app.include_router(connections.router)
app.include_router(builds.router)
app.include_router(agents.router)
app.include_router(runs.router)
app.include_router(publishing.router)
app.include_router(public_api.router)


@app.get("/health")
async def health():
    """Is the app itself alive?"""
    return {"ok": True}

