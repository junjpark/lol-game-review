"""Phase 3 — timelines -> tidy tables.

`extract_game` turns one match + timeline into rows for all three tables. It is pure:
no files, no network, no pyarrow — so it runs the same in tests, in `inspect_game`, and
over the whole corpus in `build`.
"""

from __future__ import annotations

from .events import extract_events
from .frames import extract_frames
from .teams import extract_teams


def extract_game(match: dict, timeline: dict, tier: str) -> dict[str, list[dict]]:
    frames = extract_frames(match, timeline, tier)
    return {
        "frames": frames,
        "events": extract_events(match, timeline, tier),
        "teams": extract_teams(match, timeline, frames, tier),
    }


__all__ = ["extract_game", "extract_frames", "extract_events", "extract_teams"]
