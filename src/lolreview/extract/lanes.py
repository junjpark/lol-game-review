"""Lane opponent pairing.

Opponent = the enemy with the same `teamPosition`. Ambiguous or unknown roles get no
opponent rather than a guess: in the Emerald corpus 3 of 5,000 games have an empty
`teamPosition` and none has a duplicated role, so this costs almost nothing.
"""

from __future__ import annotations

from collections import Counter


def lane_opponents(participants: list[dict]) -> dict[int, int | None]:
    roles = {p["participantId"]: (p.get("teamId"), p.get("teamPosition") or None) for p in participants}
    counts = Counter(roles.values())
    seat = {tr: pid for pid, tr in roles.items() if tr[1] and counts[tr] == 1}
    out: dict[int, int | None] = {}
    for pid, (team, role) in roles.items():
        enemy = {100: 200, 200: 100}.get(team)
        out[pid] = seat.get((enemy, role)) if role and counts[(team, role)] == 1 else None
    return out
