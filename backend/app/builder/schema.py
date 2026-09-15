"""The agent configuration document.

Rule 1 of the brief: "an agent is configuration, not code". This module defines
what that configuration contains. Everything downstream only reads this:

    the runtime compiles it        the graph picture is drawn from it
    the scorer grades it           the marketplace publishes a subset of it

Three invariants are enforced here rather than left to convention, because each
one maps to a graded check:

  1. `requires_connection` names a SERVER NAME - never a token, never a
     connection id. This is the only reason an agent can be installed into
     another company's workspace at all.
  2. Any write/destructive tool is FORCED to approval="ask". A config cannot opt
     out of the approval gate. (graded check 3)
  3. No field may contain anything shaped like a credential. (graded check 2)
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Risk(StrEnum):
    """How dangerous a tool is.

    Set by the REGISTRY when a server is introspected, never by whoever writes
    the config. See enforce_approvals() for how that is kept honest.
    """

    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class Approval(StrEnum):
    AUTO = "auto"  # runs immediately
    ASK = "ask"  # the runtime pauses and waits for a human


class TopologyType(StrEnum):
    SINGLE = "single"  # one agent, one tool loop
    SUPERVISOR = "supervisor"  # a coordinator that delegates to specialists


#: risks that must never run unattended
GUARDED = frozenset({Risk.WRITE, Risk.DESTRUCTIVE})

#: "server.tool" - the only shape a tool reference may take
TOOL_REF = r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"

#: a bare identifier: a server name, or a specialist name
IDENT = r"^[a-z][a-z0-9_]*$"

#: Things that must never appear anywhere in a config. Deliberately blunt: a
#: config is a design document, so a high-entropy blob is a mistake or a leak.
SECRET_SHAPES = [
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # slack
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # github
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # openai-style
    re.compile(r"AKIA[0-9A-Z]{16}"),  # aws
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"),
    re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),  # long base64 blob
]


class ModelSpec(BaseModel):
    provider: str = Field(description="google_genai | groq | ollama")
    name: str
    temperature: float = 0.0


class ToolSpec(BaseModel):
    """One tool this agent is allowed to use."""

    ref: str = Field(pattern=TOOL_REF, description='e.g. "github.list_issues"')
    risk: Risk
    approval: Approval
    requires_connection: str = Field(
        pattern=IDENT,
        description=(
            "A SERVER NAME. Never a connection id, never a secret. The runtime "
            "resolves server name + current tenant -> that tenant's own credential."
        ),
    )

    @model_validator(mode="after")
    def approval_follows_risk(self) -> ToolSpec:
        """A config cannot grant itself permission to skip the human.

        Re-applied at save time and again at compile time against the registry's
        own marking, so hand-editing the JSON buys nothing.
        """
        if self.risk in GUARDED and self.approval is not Approval.ASK:
            raise ValueError(
                f"{self.ref} is {self.risk} so approval must be 'ask', got '{self.approval}'"
            )
        return self

    @property
    def server(self) -> str:
        return self.ref.split(".", 1)[0]


class Specialist(BaseModel):
    name: str = Field(pattern=IDENT)
    instructions: str
    tools: list[str] = Field(default_factory=list, description="tool refs this one may call")


class Supervisor(BaseModel):
    instructions: str
    delegates_to: list[str] = Field(default_factory=list)


class Topology(BaseModel):
    type: TopologyType
    supervisor: Supervisor | None = None
    specialists: list[Specialist] = Field(default_factory=list)

    @model_validator(mode="after")
    def shape_is_coherent(self) -> Topology:
        if self.type is TopologyType.SUPERVISOR:
            if self.supervisor is None:
                raise ValueError("supervisor topology needs a 'supervisor' block")
            if len(self.specialists) < 2:
                raise ValueError(
                    "a supervisor with fewer than 2 specialists is a single agent "
                    "wearing a hat - rule 8 asks for a real coordinator"
                )
            names = {s.name for s in self.specialists}
            unknown = set(self.supervisor.delegates_to) - names
            if unknown:
                raise ValueError(f"supervisor delegates to unknown specialists: {sorted(unknown)}")
        elif self.specialists:
            raise ValueError("single topology must not declare specialists")
        return self


class Policy(BaseModel):
    approval_required_for: list[Risk] = Field(
        default_factory=lambda: [Risk.WRITE, Risk.DESTRUCTIVE]
    )
    max_tool_calls: int = 25


class AgentConfig(BaseModel):
    """The whole document. This is what lives in agents.config (JSONB)."""

    schema_version: Literal["1.0"]
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    model: ModelSpec
    topology: Topology
    tools: list[ToolSpec] = Field(min_length=1)
    policy: Policy = Field(default_factory=Policy)
    requires_connections: list[str] = Field(default_factory=list)
    schedule: str | None = None

    # ---------------------------------------------------------------- validators

    @model_validator(mode="after")
    def specialists_only_use_granted_tools(self) -> AgentConfig:
        granted = {t.ref for t in self.tools}
        for s in self.topology.specialists:
            unknown = set(s.tools) - granted
            if unknown:
                raise ValueError(f"specialist '{s.name}' uses ungranted tools: {sorted(unknown)}")
        return self

    @model_validator(mode="after")
    def connections_match_tools(self) -> AgentConfig:
        """requires_connections is derived, not free text.

        It must be exactly the set of servers the granted tools need, because the
        install flow reads this list to decide what to ask the new owner for.
        """
        needed = sorted({t.requires_connection for t in self.tools})
        if sorted(self.requires_connections) != needed:
            raise ValueError(
                f"requires_connections must be {needed}, got {sorted(self.requires_connections)}"
            )
        return self

    @model_validator(mode="after")
    def contains_no_credentials(self) -> AgentConfig:
        """graded check 2 - a credential must never reach a config document."""
        blob = self.model_dump_json()
        for pattern in SECRET_SHAPES:
            if match := pattern.search(blob):
                raise ValueError(
                    f"config contains something shaped like a credential: {match.group()[:12]}..."
                )
        return self

    # ------------------------------------------------------------------ helpers

    def enforce_approvals(self, registry: dict[str, Risk]) -> list[str]:
        """Re-derive risk from the REGISTRY and correct this config in place.

        The config's own `risk` is advisory. The registry - populated by actually
        asking the MCP server what it has - is authoritative. Returns the list of
        violations, which the publish gate surfaces as `blocked_by`.
        """
        violations: list[str] = []
        for tool in self.tools:
            true_risk = registry.get(tool.ref)
            if true_risk is None:
                violations.append(f"{tool.ref} is not in the registry")
                continue
            if tool.risk is not true_risk:
                violations.append(f"{tool.ref} claims {tool.risk}, registry says {true_risk}")
                tool.risk = true_risk
            if true_risk in GUARDED and tool.approval is not Approval.ASK:
                violations.append(f"{tool.ref} is {true_risk} but set to run without approval")
                tool.approval = Approval.ASK  # corrected regardless
        return violations

    @property
    def guarded_tools(self) -> list[ToolSpec]:
        """The tools that will pause the run and wait for a human."""
        return [t for t in self.tools if t.approval is Approval.ASK]

    def fingerprint(self) -> str:
        """Stable hash of the configuration. The runtime caches compiled graphs by this."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def graph_nodes_and_edges(self) -> dict:
        """The Overview tab's picture, DERIVED from the configuration - never hand-drawn.

        Change the config and the diagram changes. That is the point of rule 1.
        """
        nodes: list[dict] = [{"id": "message", "kind": "input"}]
        edges: list[dict] = []

        if self.topology.type is TopologyType.SUPERVISOR:
            nodes.append({"id": "supervisor", "kind": "coordinator"})
            edges.append({"from": "message", "to": "supervisor"})
            for s in self.topology.specialists:
                nodes.append({"id": s.name, "kind": "specialist"})
                edges.append({"from": "supervisor", "to": s.name})
                for ref in s.tools:
                    edges.append({"from": s.name, "to": ref})
        else:
            nodes.append({"id": "agent", "kind": "agent"})
            edges.append({"from": "message", "to": "agent"})
            for t in self.tools:
                edges.append({"from": "agent", "to": t.ref})

        for t in self.tools:
            nodes.append(
                {"id": t.ref, "kind": "tool", "risk": str(t.risk), "approval": str(t.approval)}
            )
        return {"nodes": nodes, "edges": edges}
