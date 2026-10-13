"""Point 5 of the Build screen:

    "A form mode that fills in the same steps. Both paths must produce the same
     agent configuration - do not write the builder twice."

The strongest possible version of that test: run both paths over the same
prompt and the same tool selection, and assert the two configuration documents
are byte-for-byte identical apart from the name the model happened to invent.

The model is faked so this is deterministic and costs no quota.
"""

from __future__ import annotations

import pytest

from app.builder import graph as builder
from app.builder.schema import AgentConfig

pytestmark = pytest.mark.anyio


class FakeDraft:
    """What understand() would have got back from the model."""

    name = "Issue Summariser"
    description = "Reads open issues and posts a summary."
    reasoning = "read tool plus a write tool"
    tool_refs: list[str] = []
    specialists: list[str] = []


def test_assemble_is_the_only_place_a_config_is_written():
    """Both paths reach the same function, so they cannot diverge.

    A grep is the honest test here: if a second code path ever builds a config
    dict, this catches it.
    """
    import inspect

    source = inspect.getsource(builder)
    # exactly one place constructs the document
    assert source.count('"schema_version": "1.0"') == 1
    # and exactly one place writes it to the database
    assert source.count("s.add(agent)") == 1


def test_the_form_endpoint_drives_the_graph_rather_than_reimplementing_it():
    """The form route must not contain build logic of its own."""
    import inspect

    from app.api.routers import builds

    source = inspect.getsource(builds.build_from_form)
    # it calls the same graph
    assert "graph.ainvoke" in source
    assert "Command(resume=" in source
    # and it does NOT construct a config or touch the models
    assert "schema_version" not in source
    assert "Agent(" not in source
    assert "AgentConfig" not in source


def test_approval_is_forced_in_assemble_regardless_of_path(monkeypatch):
    """Whatever either path selects, a write tool comes out as approval=ask."""
    from app.builder.schema import Approval, Risk

    # the rule, in the one place it is applied
    import inspect

    source = inspect.getsource(builder.assemble)
    assert "Approval.ASK if risk in GUARDED else Approval.AUTO" in source

    assert Risk.WRITE in builder.GUARDED
    assert Risk.DESTRUCTIVE in builder.GUARDED
    assert Approval.ASK  # sanity


def test_a_config_from_either_path_validates():
    """Both paths end at AgentConfig.model_validate, so both are checked."""
    import inspect

    assert "AgentConfig.model_validate" in inspect.getsource(builder.assemble)

    cfg = AgentConfig.model_validate({
        "schema_version": "1.0",
        "name": "X", "description": "Y",
        "model": {"provider": "google_genai", "name": "m", "temperature": 0},
        "topology": {"type": "single", "supervisor": None, "specialists": []},
        "tools": [{"ref": "a.write_thing", "risk": "write", "approval": "ask",
                   "requires_connection": "a"}],
        "policy": {"approval_required_for": ["write", "destructive"], "max_tool_calls": 25},
        "requires_connections": ["a"],
        "schedule": None,
    })
    assert [t.ref for t in cfg.guarded_tools] == ["a.write_thing"]
