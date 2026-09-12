"""Run one agent configuration from the terminal, driven by a real model.

    uv run python scripts/run_agent.py                  # approve the write
    uv run python scripts/run_agent.py --reject         # refuse it
    uv run python scripts/run_agent.py --no-connection  # prove it degrades
    uv run python scripts/run_agent.py --ask            # you type the decision
    uv run python scripts/run_agent.py --tenant=northwind_labs   # endpoints + token from the workspace

What this proves, all of it real:

    * a config document compiles into a working LangGraph
    * a MODEL supervisor delegates to two MODEL specialists
    * tools are discovered and called over the real MCP protocol
      (the reference filesystem server, confined to a scratch directory)
    * a WRITE tool stops the run dead and waits for a human
    * nothing decrypted is ever in graph state

Needs GOOGLE_API_KEY (or GROQ_API_KEY) in .env, and npx on PATH.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

if sys.platform == "win32":  # psycopg / anyio need the selector loop on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from app.builder.schema import AgentConfig  # noqa: E402
from app.runtime.compiler import compile_agent  # noqa: E402
from app.runtime.guarded_tool import RunContext  # noqa: E402
from app.mcp_registry.mcp_client import Endpoint  # noqa: E402
from app.vault.resolver import registry_endpoints, static_resolver, vault_resolver  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "tests" / "fixtures" / "docs_freshness.json"
HANDBOOK = "## Setup\nRun scripts/old_setup.sh, then open http://wiki.internal/onboarding.\n"


def rule(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


async def main() -> int:
    reject = "--reject" in sys.argv
    no_conn = "--no-connection" in sys.argv
    ask = "--ask" in sys.argv
    tenant = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--tenant=")), None)

    cfg = AgentConfig.model_validate_json(CONFIG.read_text(encoding="utf-8"))

    # Without --tenant there is no registry, so launch the filesystem server on
    # a scratch directory here. Declared api_key purely so --no-connection has
    # a credential to withhold; the server itself needs none.
    scratch = Path(tempfile.mkdtemp(prefix="forge-"))
    (scratch / "handbook.md").write_text(HANDBOOK, encoding="utf-8")
    local = Endpoint.parse(
        "stdio", f"npx -y @modelcontextprotocol/server-filesystem {scratch}",
        credential_env_var="FORGE_DEMO_TOKEN", auth_type="api_key",
    )

    async def local_endpoints(_name: str) -> Endpoint | None:
        return local

    if no_conn:
        resolve = static_resolver({})  # models a revoked connection
    elif tenant:
        resolve = vault_resolver(tenant)
    else:
        resolve = static_resolver({"filesystem": "demo-token"})

    ctx = RunContext(
        tenant_id=tenant or "local",
        thread_id="run-1",
        resolve_token=resolve,
        resolve_endpoint=registry_endpoints(tenant) if tenant else local_endpoints,
    )

    rule(f"COMPILING  {cfg.name}   fingerprint {cfg.fingerprint()}")
    print(f"  model       {cfg.model.provider} / {cfg.model.name}")
    print(f"  shape       {cfg.topology.type} -> {cfg.topology.supervisor.delegates_to}")
    print(f"  tools       {', '.join(t.ref for t in cfg.tools)}")
    print(f"  guarded     {', '.join(t.ref for t in cfg.guarded_tools) or 'none'}")
    print(f"  workspace   {'registry of ' + tenant if tenant else scratch}")
    print("\n  (asking the MCP server for its real tool schemas...)")

    graph = await compile_agent(cfg, ctx, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": ctx.thread_id}}

    rule("RUNNING  (every line below is a real model call)")
    state: dict | Command = {
        "task": "Read handbook.md, find every section that references a script, path or "
        "URL that may be stale, and write a short report to report.md.",
        "transcript": [],
        "finished": [],
        "results": {},
    }
    shown = 0  # transcript accumulates across resumes; only print what is new

    while True:
        result = await graph.ainvoke(state, config=config)

        for line in result.get("transcript", [])[shown:]:
            print("  " + line)
        shown = len(result.get("transcript", []))

        if "__interrupt__" not in result:
            break

        payload = result["__interrupt__"][0].value
        rule("PAUSED - WAITING FOR A HUMAN")
        print(f"  tool   {payload['tool']}")
        print(f"  risk   {payload['risk']}")
        for k, v in payload["args"].items():
            preview = " ".join(str(v).split())
            print(f"  arg    {k} = {preview[:60]}{'...' if len(preview) > 60 else ''}")
        print("\n  Nothing has been written. The run is parked on an interrupt.")
        print("  Restart the process here and it would still be waiting.")

        if ask:
            answer = "approve" if input("\n  approve? [y/N] ").strip().lower() == "y" else "reject"
        else:
            answer = "reject" if reject else "approve"
        print(f"\n  -> answering: {answer.upper()}")
        state = Command(resume=answer)

    rule("FINISHED")
    for ref, out in result["results"].items():
        head = " ".join(str(out).split())
        print(f"  {ref:28} -> {head[:60]}")

    report = scratch / "report.md"
    if report.exists():
        rule(f"THE FILE REALLY LANDED  ({report})")
        print("  " + report.read_text(encoding="utf-8").replace("\n", "\n  "))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
