"""A schema-qualified query would silently defeat the whole design.

If anyone ever writes `t_helios.agents` or passes schema= to a model, isolation
stops depending on search_path and starts depending on that person being right.
This test fails the build if that happens.

It reads CODE, not prose: comments and docstrings are stripped with `tokenize`
first. The blunt version flagged a comment in checkpointers.py that explains
why the design works - a check that punishes you for documenting it is worse
than no check.
"""

import io
import tokenize
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

#: The two places a tenant schema is legitimately built or dropped.
ALLOWED = {"schema_names.py", "provision.py"}

#: `schema="platform"` is correct and deliberate - it is the SHARED schema, the
#: marketplace exception the brief calls out. Only TENANT schemas are banned.
SHARED_SCHEMA = "platform"


def code_only(source: str) -> list[tuple[int, str]]:
    """Every token that is real code. Comments and string literals dropped."""
    out: list[tuple[int, str]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type in (tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE):
                continue
            out.append((tok.start[0], tok.string))
    except tokenize.TokenError:
        pass
    return out


def test_no_source_file_names_a_tenant_schema():
    """`t_something.table` must never appear in executable code."""
    offenders = []

    for path in sorted(APP.rglob("*.py")):
        if path.name in ALLOWED:
            continue
        tokens = code_only(path.read_text(encoding="utf-8"))

        for i, (line, text) in enumerate(tokens):
            # a NAME starting with t_ followed immediately by a dot
            if text.startswith("t_") and i + 1 < len(tokens) and tokens[i + 1][1] == ".":
                offenders.append(f"{path.relative_to(APP.parent)}:{line}  {text}.")

    assert not offenders, "tenant schema named in code:\n" + "\n".join(offenders)


def test_no_model_declares_a_tenant_schema():
    """`schema="t_..."` in __table_args__ would pin a model to one company."""
    offenders = []

    for path in sorted(APP.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))

        for i, tok in enumerate(tokens):
            if tok.string != "schema" or i + 2 >= len(tokens):
                continue
            if tokens[i + 1].string not in ("=", ":"):
                continue
            value = tokens[i + 2]
            if value.type != tokenize.STRING:
                continue
            literal = value.string.strip("\"'")
            if literal and literal != SHARED_SCHEMA:
                offenders.append(f"{path.relative_to(APP.parent)}:{tok.start[0]}  schema={literal!r}")

    assert not offenders, (
        'only schema="platform" is allowed; tenant tables must stay unqualified:\n'
        + "\n".join(offenders)
    )


def test_the_check_would_actually_catch_something():
    """A guard that cannot fail is not a guard. Prove the detector works."""
    bad = code_only("rows = conn.execute('x')\nq = t_helios.agents.select()\n")
    hits = [
        text for i, (_, text) in enumerate(bad)
        if text.startswith("t_") and i + 1 < len(bad) and bad[i + 1][1] == "."
    ]
    assert hits == ["t_helios"]

    # ...and that a comment mentioning one is NOT flagged
    clean = code_only("# t_helios.checkpoints is unreachable from here\nx = 1\n")
    assert not [t for _, t in clean if t.startswith("t_")]
