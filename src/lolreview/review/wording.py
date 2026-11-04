"""Wording rules for anything shown to the player (docs/decisions.md, hard constraints).

The tool makes comparative, statistical claims. It never says a play was objectively wrong, and
Phase 6 has no "blunder"/"mistake" labels. These rules used to live only in CLAUDE.md; this module
makes them checkable, so tests (and later the Phase 9 coach's verifier) can enforce them.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Whole words/phrases, case-insensitive. Add to this list rather than to a prompt.
BANNED = (
    "blunder", "blunders", "blundered",
    "mistake", "mistakes",
    "misplay", "misplays", "misplayed",
    "objectively",
    "should have", "shouldn't have", "should not have",
    "correct play", "wrong play",
)

_PATTERN = re.compile(r"\b(" + "|".join(re.escape(w) for w in BANNED) + r")\b", re.IGNORECASE)


def find_banned(text: str) -> list[str]:
    """Banned words/phrases found in `text` (lower-cased, in order of appearance)."""
    return [m.group(0).lower() for m in _PATTERN.finditer(text or "")]


def strings_in(obj: Any) -> Iterable[str]:
    """Every string inside a nested dict/list structure (e.g. Review.to_dict())."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings_in(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from strings_in(v)


def check(obj: Any) -> list[tuple[str, str]]:
    """(banned word, the string it appeared in) for every violation in a nested structure."""
    return [(w, s) for s in strings_in(obj) for w in find_banned(s)]
