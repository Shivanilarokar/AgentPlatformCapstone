"""Run one agent configuration from the terminal, driven by a real model.

    uv run python scripts/run_agent.py                  # approve the write
    uv run python scripts/run_agent.py --reject         # refuse it
    uv run python scripts/run_agent.py --no-connection  # prove it degrades
    uv run python scripts/run_agent.py --ask            # you type the decision
    uv run python scripts/run_agent.py --tenant=northwind_labs   # token from the VAULT

What this proves, all of it real:

    * a config document compiles into a working LangGraph
    * a MODEL supervisor delegates to two MODEL specialists
    * tools are discovered and called over the real MCP protocol
    * a WRITE tool stops the run dead and waits for a human
    * the credential is fetched for one call and dropped
    * nothing decrypted is ever in graph state
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

if sys.platform == "win32":  # psycopg / anyio need the selector loop on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from app.builder.schema import AgentConfig  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.runtime.compiler import compile_agent  # noqa: E402
from app.runtime.guarded_tool import RunContext  # noqa: E402
from app.vault.resolver import (
    catalogue_endpoints,
    registry_endpoints,
    static_resolver,
    vault_resolver,
)  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "tests" / "fixtures" / "standup_digest.json"
CHANNEL = "#eng-standup"

#: Stands in for the vault (step 6). Slug -> that tenant's own token.
CONNECTIONS = {"local_slack": settings.local_slack_token or "xoxb-demo-token"}


def rule(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


async def main() -> int:
    reject = "--reject" in sys.argv
    no_conn = "--no-connection" in sys.argv
    ask = "--ask" in sys.argv
    tenant = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--tenant=")), None)

    cfg = AgentConfig.model_validate_json(CONFIG.read_text(encoding="utf-8"))

    # --tenant runs against a real workspace, so the token comes out of the
    # vault exactly as it does in the product. Without it, the script uses .env
    # so it still works with no database.
    if no_conn:
        resolve = static_resolver({})              # models a revoked connection
    elif tenant:
        resolve = vault_resolver(tenant)
    else:
        resolve = static_resolver(CONNECTIONS)

    ctx = RunContext(
        tenant_id=tenant or "local",
        thread_id="run-1",
        resolve_token=resolve,
        resolve_endpoint=registry_endpoints(tenant) if tenant else catalogue_endpoints(),
    )

    rule(f"COMPILING  {cfg.name}   fingerprint {cfg.fingerprint()}")
    print(f"  model       {cfg.model.provider} / {cfg.model.name}")
    print(f"  shape       {cfg.topology.type} -> {cfg.topology.supervisor.delegates_to}")
    print(f"  tools       {', '.join(t.ref for t in cfg.tools)}")
    print(f"  guarded     {', '.join(t.ref for t in cfg.guarded_tools) or 'none'}")
    print(f"  credential  {'vault (' + tenant + ')' if tenant else '.env'}")
    print("\n  (asking each MCP server for its real tool schemas...)")

    graph = await compile_agent(cfg, ctx, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": ctx.thread_id}}

    rule("RUNNING  (every line below is a real model call)")
    state: dict | Command = {
        "task": f"Summarise yesterday's standup in {CHANNEL}, blockers first, and post it back to {CHANNEL}.",
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
        print("\n  Nothing has been sent. The run is parked on an interrupt.")
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

    store = ROOT / "var" / "local_slack.json"
    if store.exists() and not no_conn and not reject:
        posted = json.loads(store.read_text(encoding="utf-8")).get(CHANNEL, [])
        bot = [m for m in posted if m.get("user") == "issue-digest-bot"]
        if bot:
            rule(f"THE MESSAGE REALLY LANDED  ({store.relative_to(ROOT)})")
            print("  " + bot[-1]["text"].replace("\n", "\n  "))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
