"""The checks. Deterministic, from rows in the database, nothing else.

Thresholds are module constants so they can be quoted in the design document
and changed in one place.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.builder.schema import GUARDED, SECRET_SHAPES, AgentConfig, Approval, Risk
from app.mcp_registry import registry
from app.models.tenant import Agent, Run

MIN_RUNS = 1            # "tested at all" - one real run with a verdict is the bar to publish
RECENT_RUNS = 20        # success rate window
LATENCY_LIMIT_MS = 90_000
PUBLISH_MIN_QUALITY = 70
PUBLISH_MIN_GRADE = "B"
GRADES = {5: "A", 4: "B", 3: "C"}  # fewer passes -> D


@dataclass
class Check:
    key: str
    label: str
    passed: bool
    detail: str
    points: int = 0     # quality only
    max_points: int = 0


@dataclass
class Score:
    quality: int
    grade: str
    quality_checks: list[Check] = field(default_factory=list)
    safety_checks: list[Check] = field(default_factory=list)
    runs: int = 0
    can_publish: bool = False
    blocked_by: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ quality


def quality_checks(cfg: AgentConfig, runs: list[Run]) -> list[Check]:
    """0-100. Every point is earned by a check a person can read."""
    out: list[Check] = []
    finished = [r for r in runs if r.status in ("ok", "rejected", "error")]
    recent = finished[:RECENT_RUNS]

    n = len(finished)
    out.append(Check("tested", f"Run at least {MIN_RUNS} times", n >= MIN_RUNS,
                     f"{n} finished run{'s' if n != 1 else ''}",
                     20 if n >= MIN_RUNS else round(20 * n / MIN_RUNS), 20))

    ok = sum(1 for r in recent if r.status == "ok")
    rate = ok / len(recent) if recent else 0.0
    out.append(Check("success", f"Success rate over the last {RECENT_RUNS} runs", rate >= 0.8,
                     f"{ok} of {len(recent)} finished ok" if recent else "no runs yet",
                     round(30 * rate), 30))

    rated = [r for r in finished if r.feedback in (1, -1)]
    ups = sum(1 for r in rated if r.feedback == 1)
    ratio = ups / len(rated) if rated else 0.0
    out.append(Check("feedback", "Thumbs-up ratio", bool(rated) and ratio >= 0.7,
                     f"{ups} of {len(rated)} rated runs" if rated else "no run rated yet",
                     round(20 * ratio), 20))

    used = _tools_used(runs)
    granted = [t.ref for t in cfg.tools]
    unused = [ref for ref in granted if ref not in used]
    out.append(Check("coverage", "Every granted tool used at least once", not unused and n > 0,
                     "all used" if not unused and n > 0 else
                     ("never run" if n == 0 else f"never used: {', '.join(unused)}"),
                     15 if not unused and n > 0 else round(15 * (len(granted) - len(unused)) / max(len(granted), 1)) if n else 0,
                     15))

    latencies = [r.latency_ms for r in finished if r.latency_ms is not None]
    median = statistics.median(latencies) if latencies else None
    fast = median is not None and median <= LATENCY_LIMIT_MS
    out.append(Check("latency", f"Median latency under {LATENCY_LIMIT_MS // 1000}s", fast,
                     f"median {median / 1000:.1f}s" if median is not None else "no timed runs",
                     10 if fast else 0, 10))

    last5 = finished[:5]
    clean = bool(last5) and not any(r.status == "error" for r in last5)
    out.append(Check("stable", "No unhandled error in the last 5 runs", clean,
                     "clean" if clean else ("no runs yet" if not last5 else
                     f"{sum(1 for r in last5 if r.status == 'error')} errored"),
                     5 if clean else 0, 5))
    return out


def _tools_used(runs: list[Run]) -> set[str]:
    """Tool refs that actually executed, read from the transcripts."""
    used: set[str] = set()
    for r in runs:
        for line in r.transcript or []:
            # "[worker] server.tool -> result"
            parts = line.split("] ", 1)
            if len(parts) == 2 and " -> " in parts[1]:
                ref = parts[1].split(" -> ", 1)[0].strip()
                if "." in ref and "Rejected by the user" not in parts[1]:
                    used.add(ref)
    return used


# ------------------------------------------------------------------- safety


async def safety_checks(session: AsyncSession, cfg: AgentConfig, runs: list[Run]) -> list[Check]:
    """Five booleans. 5/5 = A, 4 = B, 3 = C, else D."""
    out: list[Check] = []

    # 1. every write/destructive tool asks first - from the REGISTRY's marking,
    #    not the config's word for it
    views = await registry.list_servers(session)
    live: dict[str, tuple[Risk, str]] = {
        f"{v.name}.{t.name}": (Risk(t.risk), v.health) for v in views for t in v.tools
    }
    unguarded = [
        t.ref for t in cfg.tools
        if (live.get(t.ref, (t.risk, ""))[0] in GUARDED) and t.approval is not Approval.ASK
    ]
    out.append(Check("approvals", "Write and destructive tools require approval", not unguarded,
                     "all guarded" if not unguarded else f"runs unattended: {', '.join(unguarded)}"))

    # 2. every referenced server is registered, healthy, and still has the tool
    missing = [t.ref for t in cfg.tools if t.ref not in live]
    down = sorted({t.server for t in cfg.tools if t.ref in live and live[t.ref][1] != "ok"})
    ok = not missing and not down
    out.append(Check("servers", "All servers healthy and tools present", ok,
                     "all reachable" if ok else
                     "; ".join(filter(None, [f"not in registry: {', '.join(missing)}" if missing else "",
                                             f"down: {', '.join(down)}" if down else ""]))))

    # 3. nothing credential-shaped in the config or in anything a run stored
    blob = json.dumps(cfg.model_dump(mode="json")) + json.dumps(
        [[r.transcript, r.output, r.pending] for r in runs], default=str
    )
    leaked = next((p.pattern for p in SECRET_SHAPES if p.search(blob)), None)
    out.append(Check("secrets", "No credentials in config or traces", leaked is None,
                     "clean" if leaked is None else "something token-shaped was stored"))

    # 4. no tool granted but never used (once it has run at all)
    finished = [r for r in runs if r.status in ("ok", "rejected", "error")]
    used = _tools_used(runs)
    unused = [t.ref for t in cfg.tools if t.ref not in used]
    out.append(Check("least_privilege", "No granted tool left unused", bool(finished) and not unused,
                     "every tool earns its place" if finished and not unused else
                     ("never run" if not finished else
                      f"{len(unused)} granted tool{'s' if len(unused) != 1 else ''} never used in {len(finished)} runs")))

    # 5. tested
    out.append(Check("tested", f"Run at least {MIN_RUNS} times", len(finished) >= MIN_RUNS,
                     f"run {len(finished)} time{'s' if len(finished) != 1 else ''}"))
    return out


# ------------------------------------------------------------------- score


async def score_agent(session: AsyncSession, agent: Agent) -> Score:
    """Compute both numbers for one agent and remember them on the row."""
    cfg = AgentConfig.model_validate(agent.config)
    runs = list(await session.scalars(
        select(Run).where(Run.agent_id == agent.id).order_by(Run.started_at.desc())
    ))

    q = quality_checks(cfg, runs)
    s = await safety_checks(session, cfg, runs)
    quality = sum(c.points for c in q)
    grade = GRADES.get(sum(1 for c in s if c.passed), "D")

    blocked: list[str] = []
    if quality < PUBLISH_MIN_QUALITY:
        blocked.append(f"quality {quality} is below {PUBLISH_MIN_QUALITY}")
    if grade > PUBLISH_MIN_GRADE:  # "C" > "B" in string order - D worst
        failing = [c.label for c in s if not c.passed]
        blocked.append(f"safety {grade} is below {PUBLISH_MIN_GRADE}: {failing[0]}"
                       + (f" (+{len(failing) - 1} more)" if len(failing) > 1 else ""))

    score = Score(quality=quality, grade=grade, quality_checks=q, safety_checks=s,
                  runs=len(runs), can_publish=not blocked, blocked_by=blocked)

    agent.quality_score = quality
    agent.safety_grade = grade
    agent.checks = score.as_dict()
    return score
