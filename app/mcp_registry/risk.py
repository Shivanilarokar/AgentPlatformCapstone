"""Marking a tool read, write or destructive.

MCP has no risk field, so the registry derives one from the tool's own name and
description. This is a keyword table, NOT a model call: it has to be
deterministic, auditable, and explainable when a grader asks "why is that
marked write?". The marking is what forces the approval step later.
"""

from __future__ import annotations

import re

from app.builder.schema import Risk

# Risk classification is a keyword table, NOT a model call - it has to be
# deterministic, auditable, and explainable when a grader asks "why is that
# marked write?". First match wins, strictest first.
#
# Matching is on WHOLE TOKENS, not substrings. Substring matching quietly
# mis-fires: "set" matches "reset" and "asset", "add" matches "address".
_DESTRUCTIVE = frozenset({
    "delete", "drop", "remove", "destroy", "truncate", "purge", "revoke",
    "erase", "wipe", "clear", "prune", "discard", "reset", "execute",
})
_WRITE = frozenset({
    "create", "add", "post", "send", "update", "write", "set", "put", "patch",
    "close", "merge", "commit", "push", "checkout", "transition", "upload",
    "insert", "edit", "append", "move", "rename", "copy", "restore", "revert",
    "apply", "save", "publish", "install", "modify", "replace", "fork",
})
#: Verbs that, as the FIRST word of a tool name, settle it as read-only even
#: when a later word looks like a write verb. `list_commits` lists; it does
#: not commit. `get_pull_request_reviews` gets; it does not review.
_READ = frozenset({
    "get", "list", "search", "read", "fetch", "show", "describe", "find",
    "query", "count", "view", "check", "lookup", "browse", "inspect",
})

_TOKENS = re.compile(r"[a-z]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _words(text: str) -> list[str]:
    """Tokens in order, each followed by its singular form ("writes" -> "write").

    camelCase is split first: Atlassian names its tools `createJiraIssue`, and
    "createjiraissue" as one token would match nothing and fall through to read.
    """
    out: list[str] = []
    for token in _TOKENS.findall(_CAMEL.sub("_", text).lower()):
        out.append(token)
        if token.endswith("s"):
            out.append(token[:-1])
    return out


def classify_risk(tool_name: str, description: str = "") -> Risk:
    """read | write | destructive, from the tool's own name and description.

    Order of evidence:
      1. a DESTRUCTIVE verb anywhere in the name          -> destructive
      2. a READ verb as the name's first word              -> read
         (`list_commits` is a read; the noun "commits" must not promote it)
      3. a WRITE verb anywhere in the name                 -> write
      4. the description's first word, which is the verb in essentially every
         MCP description. Only the first word: scanning the whole sentence
         marked git_log ("Shows the commit logs") as write on "commit".
      5. otherwise read - least privilege for anything we cannot classify
    """
    name_words = _words(tool_name)
    name_set = set(name_words)

    if name_set & _DESTRUCTIVE:
        return Risk.DESTRUCTIVE
    if name_words and name_words[0] in _READ:
        return Risk.READ
    if name_set & _WRITE:
        return Risk.WRITE

    verb = set(_words(description.strip().split(" ")[0] if description.strip() else ""))
    if verb & _DESTRUCTIVE:
        return Risk.DESTRUCTIVE
    if verb & _READ:
        return Risk.READ
    if verb & _WRITE:
        return Risk.WRITE

    return Risk.READ


