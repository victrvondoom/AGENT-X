"""
Environment configuration — the file must mean what it looks like it means.

This exists because of a bug that hid for a long time and then broke 80 tests
at once the moment key material started being validated:

    AGENT_X_ROOT_KEY=      # AES-256 root key for per-subject encryption

That looks like an empty value with a helpful note. python-dotenv reads it as
the *string* `"# AES-256 root key for per-subject encryption"`. Fourteen
variables were written that way, so fourteen were silently configured with a
sentence — including the root encryption key and the app secret. Nothing failed,
because every consumer swallowed the malformed value and quietly fell back.

The pattern is easy to reintroduce (it reads perfectly) and impossible to notice
by eye, which is exactly what a test is for.
"""
from __future__ import annotations

import pathlib
import re

import pytest




def _lines(name: str) -> list[tuple[int, str]]:
    path = pathlib.Path(name)
    if not path.exists():
        pytest.skip(f"{name} is not present")
    return list(enumerate(path.read_text(encoding="utf-8").split("\n"), start=1))


# `KEY=` followed by whitespace and then a comment: dotenv takes the comment.
INLINE_COMMENT = re.compile(r"^([A-Z_][A-Z0-9_]*)=[ \t]+#")


@pytest.mark.parametrize("name", (".env.example", ".env"))
def test_no_variable_takes_its_comment_as_a_value(name):
    bad = [(n, ln) for n, ln in _lines(name) if INLINE_COMMENT.match(ln)]
    assert not bad, (
        "these lines set the variable to the comment text, not to empty — "
        "put the note on its own line above the key:\n  "
        + "\n  ".join(f"{name}:{n}: {ln}" for n, ln in bad))


def test_the_example_parses_to_what_it_appears_to_say():
    """Parse it the way the application does and confirm nothing blank-looking
    came back holding prose."""
    dotenv = pytest.importorskip("dotenv")
    path = pathlib.Path(".env.example")
    if not path.exists():
        pytest.skip(".env.example is not present")

    values = dotenv.dotenv_values(str(path))
    prose = {k: v for k, v in values.items()
             if v and (v.lstrip().startswith("#") or len(v.split()) > 6)}
    assert not prose, f"variables holding prose rather than a value: {sorted(prose)}"


def test_no_real_secret_is_committed_to_the_example():
    """The template must never carry a usable credential."""
    dotenv = pytest.importorskip("dotenv")
    path = pathlib.Path(".env.example")
    if not path.exists():
        pytest.skip(".env.example is not present")

    suspicious = []
    for key, value in dotenv.dotenv_values(str(path)).items():
        if not value or not any(t in key for t in ("KEY", "SECRET", "TOKEN",
                                                   "PASSWORD")):
            continue
        # Placeholders and documented demo values are fine; entropy is not.
        if value.startswith(("<", "your", "YOUR", "changeme", "agent-x-judge")):
            continue
        if len(value) >= 24 and not value.startswith("#"):
            suspicious.append(key)
    assert not suspicious, f"possible real credentials in .env.example: {suspicious}"
