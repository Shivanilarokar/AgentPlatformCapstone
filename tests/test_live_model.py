"""The one test that calls the real model. Opt in, because quota is scarce.

    LIVE_MODEL=1 uv run pytest tests/test_live_model.py -v -s

Gemini's free tier allows 20 requests per day on the newest flash models. A full
run of the two-specialist demo costs about six, so this stays off by default and
the rest of the suite uses a fake. Run it when you want to confirm the wiring,
and before a demo.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.builder.schema import AgentConfig
from app.runtime.compiler import compile_agent
from app.runtime.guarded_tool import RunContext
from app.vault.resolver import catalogue_endpoints
from app.runtime.models import available

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "tests" / "fixtures" / "standup_digest.json"
CHANNEL = "#eng-standup"

pytestmark = pytest.mark.skipif(
    os.environ.get("LIVE_MODEL") != "1" or not available("google_genai"),
    reason="set LIVE_MODEL=1 and GOOGLE_API_KEY in .env to run against a real model",
)


async def test_a_real_model_drives_the_whole_agent(tmp_path, monkeypatch):
    """Supervisor -> reader -> supervisor -> poster -> approval -> posted."""
    store = tmp_path / "slack.json"
    monkeypatch.setenv("LOCAL_SLACK_STORE", str(store))

    config = AgentConfig.model_validate_json(CONFIG.read_text(encoding="utf-8"))
    ctx = RunContext(
        tenant_id="alpha",
        thread_id="live-1",
        resolve_token=lambda server_name: "xoxb-live-test-token",
        resolve_endpoint=catalogue_endpoints(),
    )
    graph = await compile_agent(config, ctx, checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "live-1"}}

    result = await graph.ainvoke(
        {
            "task": f"Summarise yesterday's standup in {CHANNEL}, blockers first, "
            f"and post the summary back to {CHANNEL}.",
            "transcript": [],
            "finished": [],
            "results": {},
        },
        config=cfg,
    )

    # the model reached the write tool, and the platform stopped it
    assert "__interrupt__" in result, "the run never paused for approval"
    payload = result["__interrupt__"][0].value
    assert payload["tool"] == "local_slack.post_message"
    assert payload["args"].get("text"), "the model called post_message with no text"
    assert not store.exists(), "something was written before anyone approved"

    print("\n  paused on:", payload["tool"])
    print("  draft    :", " ".join(str(payload["args"]["text"]).split())[:90])

    result = await graph.ainvoke(Command(resume="approve"), config=cfg)

    assert "__interrupt__" not in result
    assert set(result["finished"]) == {"reader", "poster"}

    posted = json.loads(store.read_text())[CHANNEL]
    assert posted, "approval did not result in a post"
    print("  posted   :", " ".join(posted[-1]["text"].split())[:90])
