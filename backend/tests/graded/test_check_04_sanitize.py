"""GRADED CHECK 4 - planted confidential material does not survive publication.

    "we hand you an agent with company-confidential material planted inside
     it, and check what comes out the other side."

So: plant it in EVERY free-text field the configuration has, project the
listing, and search the whole listing for every planted string.
"""

from __future__ import annotations

import json

from app.builder.schema import AgentConfig
from app.publishing.sanitize import listing_from, scrub

COMPANY = "Northwind Labs"

PLANTED = {
    "email": "priya.raman@northwind-labs.com",
    "host": "jira.northwind.internal",
    "url": "https://wiki.northwind.internal/runbooks/auth",
    "ip": "10.42.0.17",
    "path": "/srv/finance/q3-forecast.xlsx",
    "ticket": "NWL-4821",
    "company": COMPANY,
}
#: A token cannot be planted in a config at all - AgentConfig refuses to
#: validate one (graded check 2, at the door). It is tested on the scrubber.
TOKEN = "ghp_" + "Q" * 36


def planted_config() -> AgentConfig:
    blob = " ".join(PLANTED.values())
    return AgentConfig.model_validate({
        "schema_version": "1.0",
        "name": f"Issue Digest for {COMPANY}",
        "description": f"Triages issues. Internal: {blob}",
        "model": {"provider": "google_genai", "name": "gemini-flash-lite-latest", "temperature": 0},
        "topology": {
            "type": "supervisor",
            "supervisor": {"instructions": f"Route work. Escalate to {PLANTED['email']} via {PLANTED['host']}.",
                           "delegates_to": ["triager", "reporter"]},
            "specialists": [
                {"name": "triager", "instructions": f"Classify. Board: {PLANTED['url']} ticket {PLANTED['ticket']}",
                 "tools": ["github.list_issues"]},
                {"name": "reporter", "instructions": f"Post to {PLANTED['ip']} file {PLANTED['path']}",
                 "tools": ["github.create_issue"]},
            ],
        },
        "tools": [
            {"ref": "github.list_issues", "risk": "read", "approval": "auto", "requires_connection": "github"},
            {"ref": "github.create_issue", "risk": "write", "approval": "ask", "requires_connection": "github"},
        ],
        "requires_connections": ["github"],
    })


def test_nothing_planted_survives_projection():
    listing = listing_from(planted_config(), company=COMPANY)
    blob = json.dumps(listing)
    for label, secret in PLANTED.items():
        assert secret not in blob, f"planted {label} reached the listing: {secret}"
    assert "northwind" not in blob.lower()


def test_the_listing_is_an_allowlist_not_a_scrubbed_copy():
    """Fields that are not named in listing_from() simply do not exist here."""
    listing = listing_from(planted_config(), company=COMPANY)
    assert set(listing) == {"schema_version", "name", "description", "model", "topology",
                            "tools", "policy", "requires_connections", "schedule"}
    assert set(listing["tools"][0]) == {"ref", "risk", "approval", "requires_connection"}


def test_the_design_itself_survives():
    """Strip the company, keep the agent: it must still be installable."""
    listing = listing_from(planted_config(), company=COMPANY)
    assert listing["topology"]["type"] == "supervisor"
    assert [s["name"] for s in listing["topology"]["specialists"]] == ["triager", "reporter"]
    assert listing["requires_connections"] == ["github"]
    assert listing["tools"][1]["approval"] == "ask"
    assert "Triages issues" in listing["description"]
    # and it is still a valid configuration document
    AgentConfig.model_validate(listing)


def test_a_token_cannot_even_enter_a_configuration():
    import pytest

    with pytest.raises(ValueError, match="credential"):
        AgentConfig.model_validate({**planted_config().model_dump(mode="json"),
                                    "description": f"key {TOKEN}"})


def test_the_scrubber_removes_tokens_from_free_text_anyway():
    assert TOKEN not in scrub(f"use {TOKEN} to call it", COMPANY)


def test_scrub_is_conservative_about_ordinary_text():
    assert scrub("Summarise the open issues, P0s first.", COMPANY) == "Summarise the open issues, P0s first."
