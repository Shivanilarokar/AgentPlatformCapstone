"""Risk marking decides whether a human is asked before a tool runs.

Get this wrong in the lenient direction and graded check 3 falls over: an agent
commits, deletes or posts without anyone approving it. So the table is tested
against the tools of the real MCP servers this platform ships with.
"""

import pytest

from app.builder.schema import Risk
from app.runtime.mcp_client import classify_risk

# Names taken verbatim from the real servers' tools/list responses.
CASES = [
    # --- git (mcp-server-git) -------------------------------------------
    ("git_status", Risk.READ),
    ("git_log", Risk.READ),
    ("git_diff", Risk.READ),
    ("git_diff_staged", Risk.READ),
    ("git_show", Risk.READ),
    ("git_branch", Risk.READ),
    ("git_add", Risk.WRITE),
    ("git_commit", Risk.WRITE),        # was READ before tokenised matching
    ("git_create_branch", Risk.WRITE),
    ("git_checkout", Risk.WRITE),
    ("git_reset", Risk.DESTRUCTIVE),   # only matched before via "set" in "reset"
    # --- filesystem -----------------------------------------------------
    ("read_file", Risk.READ),
    ("read_text_file", Risk.READ),
    ("list_directory", Risk.READ),
    ("search_files", Risk.READ),
    ("get_file_info", Risk.READ),
    ("write_file", Risk.WRITE),
    ("edit_file", Risk.WRITE),
    ("create_directory", Risk.WRITE),
    ("move_file", Risk.WRITE),
    # --- memory ---------------------------------------------------------
    ("read_graph", Risk.READ),
    ("search_nodes", Risk.READ),
    ("create_entities", Risk.WRITE),
    ("add_observations", Risk.WRITE),
    ("delete_entities", Risk.DESTRUCTIVE),
    ("delete_relations", Risk.DESTRUCTIVE),
    # --- time / fetch ---------------------------------------------------
    ("get_current_time", Risk.READ),
    ("convert_time", Risk.READ),
    ("fetch", Risk.READ),
    # --- local_slack ----------------------------------------------------
    ("read_channel", Risk.READ),
    ("post_message", Risk.WRITE),
    ("delete_message", Risk.DESTRUCTIVE),
]


@pytest.mark.parametrize("name,expected", CASES)
def test_real_tool_names_are_classified_correctly(name, expected):
    assert classify_risk(name) is expected


def test_substrings_do_not_cause_false_positives():
    """The bug this replaced: "set" inside "asset", "add" inside "address"."""
    assert classify_risk("get_asset") is Risk.READ
    assert classify_risk("lookup_address") is Risk.READ
    assert classify_risk("list_settings") is Risk.READ
    assert classify_risk("get_offset") is Risk.READ


def test_the_name_outweighs_the_description():
    """Descriptions often say what a tool does NOT do."""
    assert classify_risk("list_branches", "Lists branches without creating one.") is Risk.READ


def test_the_description_is_used_when_the_name_says_nothing():
    assert classify_risk("sync", "Writes local changes to the server.") is Risk.WRITE
    assert classify_risk("cleanup", "Removes every stale record.") is Risk.DESTRUCTIVE


def test_an_unknown_tool_is_read_only():
    """Least privilege: a tool we cannot classify gets no write permission."""
    assert classify_risk("frobnicate", "Frobnicates the widget.") is Risk.READ


def test_both_guarded_levels_require_approval():
    """write and destructive differ in wording, not in whether a human is asked.

    That is deliberate - the cost of over-marking is a needless click, and the
    cost of under-marking is a silent write.
    """
    from app.builder.schema import GUARDED

    assert Risk.WRITE in GUARDED
    assert Risk.DESTRUCTIVE in GUARDED
    assert Risk.READ not in GUARDED


# --- the regression that cost three git tools their read-only marking --------

REAL_GIT_DESCRIPTIONS = [
    ("git_log", "Shows the commit logs", Risk.READ),
    ("git_show", "Shows the contents of a commit", Risk.READ),
    ("git_diff", "Shows differences between branches or commits", Risk.READ),
    ("git_status", "Shows the working tree status", Risk.READ),
]


@pytest.mark.parametrize("name,desc,expected", REAL_GIT_DESCRIPTIONS)
def test_a_noun_in_the_description_does_not_promote_a_read_tool(name, desc, expected):
    """"Shows the commit logs" is a READ tool that happens to contain "commit"."""
    assert classify_risk(name, desc) is expected


# --- the two GitHub tools a user caught being wrong ---------------------------

GITHUB_CORRECTIONS = [
    # "commits" is a noun here. The tool LISTS. The stemmer must not promote it.
    ("list_commits", "Get list of commits of a branch in a GitHub repository", Risk.READ),
    # forking creates a copy. It was READ because "fork" was not in the verb table.
    ("fork_repository", "Fork a GitHub repository to your account", Risk.WRITE),
    # and the rest of the GitHub server, so a regression anywhere in it shows up
    ("get_pull_request_reviews", "Get the reviews on a pull request", Risk.READ),
    ("get_pull_request_status", "Get the combined status of all status checks", Risk.READ),
    ("search_issues", "Search for issues and pull requests", Risk.READ),
    ("update_pull_request_branch", "Update a pull request branch", Risk.WRITE),
    ("merge_pull_request", "Merge a pull request", Risk.WRITE),
    ("push_files", "Push multiple files in a single commit", Risk.WRITE),
]


@pytest.mark.parametrize("name,desc,expected", GITHUB_CORRECTIONS)
def test_github_tools_a_user_reviewed_by_hand(name, desc, expected):
    assert classify_risk(name, desc) is expected


def test_a_leading_read_verb_beats_a_trailing_write_noun():
    """`list_X` is a read no matter what X is."""
    for noun in ("commits", "merges", "pushes", "updates", "posts", "sets"):
        assert classify_risk(f"list_{noun}") is Risk.READ, noun
        assert classify_risk(f"get_{noun}") is Risk.READ, noun


def test_a_destructive_verb_still_wins_over_a_leading_read_verb():
    """...except destruction, which is never a read whatever the first word."""
    assert classify_risk("get_and_delete_message") is Risk.DESTRUCTIVE
