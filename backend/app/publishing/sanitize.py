"""Rule 5 - publishing strips the company out.

    "What reaches the marketplace is the agent's design: what it does and what
     kinds of access it needs. Not who built it, not their credentials, not
     anything internal to their company.
     How this is tested: we hand you an agent with company-confidential material
     planted inside it, and check what comes out the other side."

Two layers, in this order:

  1. ALLOWLIST PROJECTION. The listing is built by copying ONLY the fields named
     in `listing_from()`. Nothing else in the agent row - owner, ids, run traces,
     score details, the company - has a path into the listing. Planted material
     in any unnamed field cannot escape, by construction.

  2. SCRUBBER, for the free-text fields that must survive (description and
     instructions - the agent is useless without them): emails, URLs, IPs,
     anything credential-shaped, and the publishing company's own name are
     replaced with a neutral marker. The author is shown the result before
     submitting, so nothing leaves that they have not seen.
"""

from __future__ import annotations

import re

from app.builder.schema import SECRET_SHAPES, AgentConfig

#: What the marketplace may know about a tool. Nothing about how it is reached.
_TOOL_FIELDS = ("ref", "risk", "approval", "requires_connection")

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"\b(?:https?://|www\.)[^\s)>\]\"']+", re.I)
_HOST = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:internal|local|corp|lan|intranet|test)\b", re.I)
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_PATH = re.compile(r"(?<![\w/])(?:/(?:srv|home|users|var|etc|opt)/[^\s\"']+|[A-Z]:\\[^\s\"']+)")
_TICKET = re.compile(r"\b[A-Z]{2,10}-\d{2,6}\b")  # JIRA-1234 style internal references


def scrub(text: str, company: str = "") -> str:
    """Replace anything that names the company, its people or its machines."""
    if not text:
        return text
    out = text
    for pattern in SECRET_SHAPES:
        out = pattern.sub("[redacted]", out)
    out = _EMAIL.sub("[email removed]", out)
    out = _URL.sub("[link removed]", out)
    out = _HOST.sub("[internal host removed]", out)
    out = _IP.sub("[address removed]", out)
    out = _PATH.sub("[path removed]", out)
    out = _TICKET.sub("[reference removed]", out)
    if company:
        out = re.sub(re.escape(company), "the publisher", out, flags=re.I)
        # also the schema-key form and each distinctive word of the name
        for word in {w for w in re.split(r"\W+", company) if len(w) > 3}:
            out = re.sub(rf"\b{re.escape(word)}\b", "the publisher", out, flags=re.I)
    return out


def listing_from(cfg: AgentConfig, *, company: str) -> dict:
    """Project the configuration into what the marketplace shows. ALLOWLIST.

    Anything not written here does not exist on the other side.
    """
    topo = cfg.topology
    return {
        "schema_version": cfg.schema_version,
        "name": scrub(cfg.name, company),
        "description": scrub(cfg.description, company),
        "model": {"provider": cfg.model.provider, "name": cfg.model.name,
                  "temperature": cfg.model.temperature},
        "topology": {
            "type": str(topo.type),
            "supervisor": {
                "instructions": scrub(topo.supervisor.instructions, company),
                "delegates_to": list(topo.supervisor.delegates_to),
            } if topo.supervisor else None,
            "specialists": [
                {"name": s.name, "instructions": scrub(s.instructions, company),
                 "tools": list(s.tools)}
                for s in topo.specialists
            ],
        },
        "tools": [{k: str(getattr(t, k)) for k in _TOOL_FIELDS} for t in cfg.tools],
        "policy": {"approval_required_for": [str(r) for r in cfg.policy.approval_required_for],
                   "max_tool_calls": cfg.policy.max_tool_calls},
        "requires_connections": list(cfg.requires_connections),
        "schedule": cfg.schedule,
    }
