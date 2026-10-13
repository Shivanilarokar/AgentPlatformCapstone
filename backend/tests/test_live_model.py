"""The one test that calls the real model. Opt in, because quota is scarce.

    LIVE_MODEL=1 uv run pytest tests/test_live_model.py -v -s

Gemini's free tier allows 20 requests per day on the newest flash models. A full
run of the two-specialist demo costs about six, so this stays off by default and
the rest of the suite uses a fake. Run it when you want to confirm the wiring,
and before a demo.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.builder.schema import AgentConfig
from app.runtime.compiler import compile_agent
from app.runtime.guarded_tool import RunContext
from app.core.config import settings

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "tests" / "fixtures" / "docs_freshness.json"

pytestmark = pytest.mark.skipif(
    os.environ.get("LIVE_MODEL") != "1" or not settings.google_api_key,
    reason="set LIVE_MODEL=1 and GOOGLE_API_KEY in .env to run against a real model",
)


async def test_a_real_model_drives_the_whole_agent(tmp_path):
    """Supervisor -> reader -> supervisor -> writer -> approval -> file written."""
    from app.mcp_registry.mcp_client import Endpoint

    (tmp_path / "handbook.md").write_text(
        "## Setup\nrun scripts/old_setup.sh\n", encoding="utf-8"
    )
    ep = Endpoint.parse("stdio", f"npx -y @modelcontextprotocol/server-filesystem {tmp_path}")

    async def endpoints(_name):
        return ep

    config = AgentConfig.model_validate_json(CONFIG.read_text(encoding="utf-8"))
    ctx = RunContext(
        tenant_id="alpha",
        thread_id="live-1",
        resolve_token=lambda server_name: None,  # filesystem needs no credential
        resolve_endpoint=endpoints,
    )
    graph = await compile_agent(config, ctx, checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "live-1"}}

    result = await graph.ainvoke(
        {
            "task": "Read handbook.md, find sections that reference scripts or paths "
            "that may be stale, and write a short report to report.md.",
            "transcript": [],
            "finished": [],
            "results": {},
        },
        config=cfg,
    )

    # the model reached the write tool, and the platform stopped it
    assert "__interrupt__" in result, "the run never paused for approval"
    payload = result["__interrupt__"][0].value
    assert payload["tool"] == "filesystem.write_file"
    assert payload["args"].get("content"), "the model called write_file with no content"
    assert not (tmp_path / "report.md").exists(), "something was written before anyone approved"

    print("\n  paused on:", payload["tool"])
    print("  draft    :", " ".join(str(payload["args"]["content"]).split())[:90])

    result = await graph.ainvoke(Command(resume="approve"), config=cfg)

    assert "__interrupt__" not in result
    assert set(result["finished"]) == {"reader", "writer"}

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert report, "approval did not result in a file"
    print("  written  :", " ".join(report.split())[:90])
