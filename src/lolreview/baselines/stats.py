"""The stats that get baselines, and how the report should treat each one."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Stat:
    name: str                 # column in the frames table
    label: str                # how the report names it
    group: str                # Economy / Lane matchup / Fighting / Vision / Objectives
    better: str               # "higher", "lower", or "neither" (depends on champion)
    count: bool = False       # small whole numbers: many players tie, so report ties
    hide_for: frozenset[str] = field(default_factory=frozenset)  # roles where it's meaningless


SUPPORT = frozenset({"UTILITY"})

STATS: tuple[Stat, ...] = (
    Stat("gold", "gold earned", "Economy", "higher"),
    Stat("cs", "CS", "Economy", "higher", hide_for=SUPPORT),
    Stat("cs_per_min", "CS per minute", "Economy", "higher", hide_for=SUPPORT),
    Stat("xp", "XP", "Economy", "higher"),
    Stat("level", "level", "Economy", "higher", count=True),
    Stat("gold_diff", "gold vs opponent", "Lane matchup", "higher"),
    Stat("cs_diff", "CS vs opponent", "Lane matchup", "higher", hide_for=SUPPORT),
    Stat("xp_diff", "XP vs opponent", "Lane matchup", "higher"),
    Stat("level_diff", "level vs opponent", "Lane matchup", "higher", count=True),
    Stat("kills", "kills", "Fighting", "higher", count=True),
    Stat("deaths", "deaths", "Fighting", "lower", count=True),
    Stat("assists", "assists", "Fighting", "higher", count=True),
    Stat("kill_participation", "kill participation", "Fighting", "higher"),
    Stat("dmg_to_champs", "damage to champions", "Fighting", "higher"),
    Stat("dmg_taken", "damage taken", "Fighting", "neither"),
    # timeEnemySpentControlled is MILLISECONDS of CC dealt (summed over enemies); it
    # matches the match summary's totalTimeCCDealt (seconds) x 1000. Shown in seconds.
    Stat("cc_time", "crowd control dealt", "Fighting", "neither"),
    Stat("wards_placed", "wards placed", "Vision", "higher", count=True),
    Stat("wards_killed", "wards cleared", "Vision", "higher", count=True),
    Stat("control_wards", "control wards placed", "Vision", "higher", count=True),
    Stat("epic_participation", "epic monsters (took part)", "Objectives", "higher", count=True),
    Stat("structure_participation", "towers/inhibs (took part)", "Objectives", "higher", count=True),
)

BY_NAME = {s.name: s for s in STATS}
ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
ROLE_NAMES = {"TOP": "top laners", "JUNGLE": "junglers", "MIDDLE": "mid laners",
              "BOTTOM": "bot laners", "UTILITY": "supports"}


def stats_for(role: str) -> list[Stat]:
    return [s for s in STATS if role not in s.hide_for]
