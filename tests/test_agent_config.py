"""The agent configuration must refuse to be wrong.

Each test below is a rule from the brief expressed as a failing config.
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.builder.schema import AgentConfig, Approval, Risk, TopologyType

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "issue_digest.json"
RAW = json.loads(FIXTURE.read_text(encoding="utf-8"))


def load(**overrides) -> dict:
    """A copy of the demo configuration, optionally broken in one specific way."""
    data = deepcopy(RAW)
    data.update(overrides)
    return data


# --------------------------------------------------------------- the happy path


def test_the_demo_card_is_valid():
    cfg = AgentConfig.model_validate(RAW)
    assert cfg.name == "Issue Digest"
    assert cfg.topology.type is TopologyType.SUPERVISOR
    assert len(cfg.topology.specialists) == 2


def test_it_is_a_real_multi_agent_system():
    """Rule 8: a coordinator delegating to at least two specialists."""
    cfg = AgentConfig.model_validate(RAW)
    assert cfg.topology.supervisor is not None
    assert set(cfg.topology.supervisor.delegates_to) == {"triager", "reporter"}


def test_the_write_tool_is_behind_an_approval():
    cfg = AgentConfig.model_validate(RAW)
    guarded = [t.ref for t in cfg.guarded_tools]
    assert guarded == ["local_slack.post_message"]


def test_fingerprint_is_stable_and_content_addressed():
    a = AgentConfig.model_validate(RAW)
    b = AgentConfig.model_validate(deepcopy(RAW))
    assert a.fingerprint() == b.fingerprint()

    changed = load(name="Issue Digest v2")
    assert AgentConfig.model_validate(changed).fingerprint() != a.fingerprint()


def test_the_graph_picture_is_derived_from_the_card():
    """Rule: the diagram is drawn from the config, never hand-maintained."""
    cfg = AgentConfig.model_validate(RAW)
    graph = cfg.graph_nodes_and_edges()
    ids = {n["id"] for n in graph["nodes"]}
    assert {"supervisor", "triager", "reporter"} <= ids
    assert {"from": "supervisor", "to": "triager"} in graph["edges"]

    slack = next(n for n in graph["nodes"] if n["id"] == "local_slack.post_message")
    assert slack["risk"] == "write" and slack["approval"] == "ask"


# ------------------------------------------------------- graded check 3: approval


def test_a_write_tool_cannot_declare_itself_auto():
    broken = load()
    broken["tools"][2]["approval"] = "auto"  # local_slack.post_message
    with pytest.raises(ValidationError, match="approval must be 'ask'"):
        AgentConfig.model_validate(broken)


def test_a_destructive_tool_cannot_declare_itself_auto():
    broken = load()
    broken["tools"][2]["risk"] = "destructive"
    broken["tools"][2]["approval"] = "auto"
    with pytest.raises(ValidationError, match="approval must be 'ask'"):
        AgentConfig.model_validate(broken)


def test_the_registry_overrules_the_config():
    """A configuration claiming a write tool is 'read' is corrected, and the lie reported.

    This is what the publish gate turns into `blocked_by`.
    """
    lying = load()
    lying["tools"][2]["risk"] = "read"
    lying["tools"][2]["approval"] = "auto"
    cfg = AgentConfig.model_validate(lying)  # passes: it claims to be read-only

    registry = {  # what the MCP server actually reported
        "github.list_issues": Risk.READ,
        "github.get_issue": Risk.READ,
        "local_slack.post_message": Risk.WRITE,
    }
    violations = cfg.enforce_approvals(registry)

    assert any("registry says" in v for v in violations)
    assert any("without approval" in v for v in violations)
    posted = next(t for t in cfg.tools if t.ref == "local_slack.post_message")
    assert posted.risk is Risk.WRITE
    assert posted.approval is Approval.ASK  # corrected regardless


# -------------------------------------------------------- graded check 2: secrets


@pytest.mark.parametrize(
    "planted",
    [
        # Assembled at runtime so no secret scanner mistakes these for real ones.
        "xoxb-" + "0" * 12 + "-" + "FAKEFAKEFAKEFAK",
        "ghp_" + "FAKE" * 9,
        "sk-" + "FAKE" * 8,
        "AKIA" + "FAKEFAKEFAKEFAKE",
    ],
)
def test_a_credential_cannot_be_stored_in_a_config(planted):
    broken = load()
    broken["topology"]["specialists"][1]["instructions"] += f" Use token {planted}."
    with pytest.raises(ValidationError, match="shaped like a credential"):
        AgentConfig.model_validate(broken)


# ------------------------------------------- the invariant the marketplace needs


def test_requires_connection_is_a_slug_not_a_secret():
    """The configuration names a KIND of connection, never a specific one.

    This is why another company can install it and have it use their token.
    """
    cfg = AgentConfig.model_validate(RAW)
    for tool in cfg.tools:
        assert tool.requires_connection in {"github", "local_slack"}
        assert "://" not in tool.requires_connection
        assert "-" not in tool.requires_connection  # not a uuid


def test_requires_connections_must_match_the_granted_tools():
    broken = load(requires_connections=["github"])  # forgot local_slack
    with pytest.raises(ValidationError, match="requires_connections must be"):
        AgentConfig.model_validate(broken)


def test_a_connection_cannot_be_requested_that_no_tool_needs():
    broken = load(requires_connections=["github", "local_slack", "jira"])
    with pytest.raises(ValidationError, match="requires_connections must be"):
        AgentConfig.model_validate(broken)


# ----------------------------------------------------------- structural sanity


def test_a_specialist_cannot_use_a_tool_it_was_not_granted():
    broken = load()
    broken["topology"]["specialists"][0]["tools"].append("github.create_issue")
    with pytest.raises(ValidationError, match="ungranted tools"):
        AgentConfig.model_validate(broken)


def test_a_supervisor_needs_at_least_two_specialists():
    broken = load()
    broken["topology"]["specialists"] = broken["topology"]["specialists"][:1]
    broken["topology"]["supervisor"]["delegates_to"] = ["triager"]
    with pytest.raises(ValidationError, match="at least|fewer than 2"):
        AgentConfig.model_validate(broken)


def test_a_supervisor_cannot_delegate_to_someone_who_does_not_exist():
    broken = load()
    broken["topology"]["supervisor"]["delegates_to"].append("ghost")
    with pytest.raises(ValidationError, match="unknown specialists"):
        AgentConfig.model_validate(broken)


def test_tool_refs_must_be_server_dot_tool():
    broken = load()
    broken["tools"][0]["ref"] = "list_issues"  # no server prefix
    with pytest.raises(ValidationError):
        AgentConfig.model_validate(broken)
