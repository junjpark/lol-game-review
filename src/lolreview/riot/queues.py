"""Queue IDs: which game mode a match was.

The corpus, baselines and win-probability model are ranked solo/duo (420) only. Other
Summoner's Rift queues replay fine, but the model's numbers aren't validated for them.
"""

from __future__ import annotations

RANKED_SOLO = 420

QUEUE_NAMES: dict[int, str] = {
    400: "Normal Draft",
    420: "Ranked Solo/Duo",
    430: "Normal Blind",
    440: "Ranked Flex",
    450: "ARAM",
}

# --queue values accepted by the scripts
QUEUE_ALIASES: dict[str, int | None] = {
    "ranked": 420, "solo": 420, "soloq": 420,
    "draft": 400, "normal": 400,
    "blind": 430,
    "flex": 440,
    "aram": 450,
    "any": None, "all": None,
}

# Queues played on Summoner's Rift (ARAM and others use different maps).
RIFT_QUEUES = frozenset({400, 420, 430, 440})


def parse_queue(value: str | None, default: int | None = RANKED_SOLO) -> int | None:
    """'draft' -> 400, 'any' -> None (no filter), '440' -> 440, None -> default."""
    if value is None or value == "":
        return default
    v = value.strip().lower()
    if v in QUEUE_ALIASES:
        return QUEUE_ALIASES[v]
    if v.isdigit():
        return int(v)
    raise ValueError(f"unknown queue {value!r}: use one of {', '.join(sorted(QUEUE_ALIASES))} or a number")


def queue_name(queue_id: int | None) -> str:
    return "any queue" if queue_id is None else QUEUE_NAMES.get(queue_id, f"queue {queue_id}")
