"""The approval gate.

EVERY tool call in this platform goes through this wrapper. That is the whole
point: a write tool physically cannot execute before a human has answered.

Rule 4 of the brief:

    "This has to be enforced by the platform. Putting 'always ask before
     posting' in the agent's instructions is not a control - it is a
     suggestion, and models do not always follow suggestions."

The model never calls a tool. It calls this.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langgraph.types import interrupt

from app.builder.schema import Approval, ToolSpec
from app.mcp_registry.mcp_client import Endpoint, call_tool

#: Argument names whose values must never be logged or put into graph state.
_SENSITIVE = re.compile(r"token|secret|password|key|authorization", re.I)


def redact(args: dict[str, Any]) -> dict[str, Any]:
    """What the human sees in the approval prompt, and what we may persist.

    Graph state is written to the database so a run can resume days later, so
    anything that goes into an interrupt payload is on disk permanently.
    """
    return {k: ("***" if _SENSITIVE.search(k) else v) for k, v in args.items()}


@dataclass
class RunContext:
    """Who this run belongs to, and how it gets its credentials.

    `resolve_token` takes a SERVER NAME and returns this person's own plaintext
    token, or None if they have not connected it. The server name is what travels in a
    configuration; the token never does.

    It is ASYNC and called per tool call rather than resolved once up front -
    holding a decrypted token for the length of a run would put it in memory
    across every checkpoint write in between.
    """

    tenant_id: str
    thread_id: str
    resolve_token: Callable[[str], Awaitable[str | None]]
    #: SERVER NAME -> how to reach it. In the app this reads the registry
    #: (private, then shared); scripts use the catalogue.
    resolve_endpoint: Callable[[str], Awaitable[Endpoint | None]]
    #: TOOL REFS ("server.tool") the admin switched off in the registry. The
    #: compiler removes them from the agent, so the model is never offered
    #: one - however old the agent's configuration is. None = nothing is off
    #: (scripts and tests that have no registry).
    resolve_disabled: Callable[[], Awaitable[set[str]]] | None = None


def guarded_tool(spec: ToolSpec, ctx: RunContext) -> Callable[..., Awaitable[str]]:
    async def _run(**kwargs: Any) -> str:
        # ---- 1. risk gate ------------------------------------------------
        if spec.approval is Approval.ASK:
            decision = interrupt(
                {
                    "type": "tool_approval",
                    "tool": spec.ref,
                    "risk": str(spec.risk),
                    "args": redact(kwargs),
                }
            )
            if decision != "approve":
                # A refusal is a normal result, not an error. The agent sees it
                # and carries on with whatever else it can do.
                return f"Rejected by the user. {spec.ref} was not executed."

        # ---- 2. borrow the credential ------------------------------------
        ep = await ctx.resolve_endpoint(spec.requires_connection)
        if ep is None:
            return f"{spec.requires_connection} is not in this workspace's registry."

        token = await ctx.resolve_token(spec.requires_connection)
        if token is None and ep.needs_token:
            # DEGRADED, not crashed: the agent keeps working and reports the
            # broken tool, exactly as the Connections screen describes.
            return (
                f"{spec.requires_connection} is not connected in this workspace, "
                f"so {spec.ref} could not run."
            )

        # ---- 3. use it for exactly one call, then drop it -----------------
        try:
            return await call_tool(ep, spec.ref.split(".", 1)[1], kwargs, token)
        finally:
            del token

    _run.__name__ = spec.ref.replace(".", "_")
    return _run
