"""A reply in the playground must be read as a reply.

Found in use: "read GitHub issues and write to Slack" - with no repository and
no channel. The agent asked for the repository; the answer went in as a brand
new run with nothing but "acme/webapp" as its task, so the agent had no idea
what to do with it. When it did get that far and asked for the Slack channel,
the next answer started from nothing again and it asked for the repository
again - round and round.

Every run still starts from nothing (nothing is held on the server between
runs). The playground now sends the earlier turns with each message, and the
run's task is built from them, so a value given earlier still counts as given.
"""

from __future__ import annotations

from app.api.routers.public_api import StreamIn
from app.api.routers.runs import MAX_HISTORY, InvokeIn, Turn, _state, _task
from app.models.tenant import Run


def test_without_history_the_task_is_exactly_what_was_typed():
    assert _task("read github issues and write to slack") == "read github issues and write to slack"


def test_a_reply_carries_the_conversation_it_belongs_to():
    history = [Turn(input="read github issues and write to slack",
                    output="Please provide the repository name in owner/repo format.")]
    task = _task("acme/webapp", history)

    assert "User: read github issues and write to slack" in task          # what the job is
    assert "Agent: Please provide the repository name in owner/repo format." in task
    assert "The user's new message: acme/webapp" in task
    assert task.index("read github issues") < task.index("acme/webapp")   # oldest first
    assert "counts as given" in task


def test_the_third_message_still_sees_the_repository_from_the_second():
    """The loop: repository -> channel -> repository again. Both answers must be visible."""
    history = [
        Turn(input="read github issues and write to slack", output="Which repository?"),
        Turn(input="acme/webapp", output="Which Slack channel should I post to?"),
    ]
    task = _task("#engineering", history)
    assert "acme/webapp" in task and "#engineering" in task and "Which Slack channel" in task


def test_only_the_most_recent_turns_are_carried():
    history = [Turn(input=f"message {i}", output=f"answer {i}") for i in range(MAX_HISTORY + 4)]
    task = _task("now", history)
    assert "message 0" not in task and "message 3" not in task
    assert f"message {MAX_HISTORY + 3}" in task


def test_state_uses_the_conversation_but_the_run_keeps_only_what_was_typed():
    run = Run(input="acme/webapp")
    state = _state(run, [Turn(input="read issues", output="Which repository?")])
    assert "Earlier in this conversation" in state["task"] and "acme/webapp" in state["task"]
    assert run.input == "acme/webapp"                                        # the history shows what the person typed


def test_both_entry_points_accept_history_and_default_to_none():
    assert InvokeIn(input="hi").history == [] and StreamIn(input="hi").history == []
    body = StreamIn(input="acme/webapp", history=[{"input": "read issues", "output": "Which repository?"}])
    assert body.history[0].output == "Which repository?"
