from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.api.routers import agents, auth, builds, connections, servers
from app.core.db import engine
from app.registry import health as health_sweep
from app.tenancy.checkpointers import close_all
from app.tenancy.provision import bootstrap_platform

@asynccontextmanager
async def lifespan(app: FastAPI):
    # The shared schema must exist before anyone can sign up.
    await bootstrap_platform()
    health_sweep.start()
    yield
    await health_sweep.stop()
    await close_all()
    await engine.dispose()


app = FastAPI(title="Agent Platform", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(servers.router)
app.include_router(connections.router)
app.include_router(builds.router)
app.include_router(agents.router)


@app.get("/health")
async def health():
    """Is the app itself alive?"""
    return {"ok": True}


@app.get("/health/db")
async def health_db():
    """Can the app reach Postgres? This is what proves compose is wired correctly."""
    async with engine.connect() as conn:
        version = await conn.scalar(text("SELECT version()"))
    return {"ok": True, "postgres": version.split(",")[0]}
