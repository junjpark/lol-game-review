"""A small hand-built match + timeline shared by the replay and extraction tests.

Three one-minute frames, ten players. Events:
  0:30  C1 (blue top) kills C6 (red top), C2 assists
  1:10  blue destroys red's mid outer turret (C1)
  1:20  blue takes a fire dragon (C2)
  1:30  C3 (blue mid) is executed by a turret
  1:40  C5 places a control ward
  1:50  red takes voidgrubs (C7)
"""

from __future__ import annotations

from lolreview.rift import MAX_X, MAX_Y, MIN_X, MIN_Y

ROLE_ORDER = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]


def pframe(pid: int, gold: int = 500, x: int = 1000, y: int = 1000, hp: int = 600) -> dict:
    return {
        "participantId": pid,
        "position": {"x": x, "y": y},
        "totalGold": gold,
        "currentGold": gold,
        "xp": 100 * pid,
        "level": 1,
        "minionsKilled": 3,
        "jungleMinionsKilled": 1,
        "championStats": {"health": hp, "healthMax": 600},
    }


def make_game(champion_override: dict[int, str] | None = None) -> tuple[dict, dict]:
    names = champion_override or {}
    participants = [
        {
            "participantId": pid,
            "puuid": f"puuid-{pid}",
            "championName": names.get(pid, f"C{pid}"),
            "teamId": 100 if pid <= 5 else 200,
            "teamPosition": ROLE_ORDER[(pid - 1) % 5],
            "riotIdGameName": f"p{pid}",
            "riotIdTagline": "NA1",
            "win": pid <= 5,
        }
        for pid in range(10, 0, -1)  # deliberately out of order
    ]
    match = {
        "metadata": {"matchId": "NA1_1"},
        "info": {
            "gameVersion": "16.20.700.1",
            "gameDuration": 1500,
            "gameCreation": 0,
            "teams": [{"teamId": 100, "win": True}, {"teamId": 200, "win": False}],
            "participants": participants,
        },
    }
    frames = [
        {"timestamp": 0, "events": [], "participantFrames": {str(p): pframe(p) for p in range(1, 11)}},
        {
            "timestamp": 60_000,
            "events": [
                {"type": "CHAMPION_KILL", "timestamp": 30_000, "killerId": 1, "victimId": 6,
                 "assistingParticipantIds": [2], "position": {"x": MIN_X, "y": MIN_Y}},
            ],
            "participantFrames": {str(p): pframe(p, gold=900 if p <= 5 else 600) for p in range(1, 11)},
        },
        {
            "timestamp": 120_000,
            "events": [
                {"type": "BUILDING_KILL", "timestamp": 70_000, "teamId": 200, "killerId": 1,
                 "buildingType": "TOWER_BUILDING", "towerType": "OUTER_TURRET", "laneType": "MID_LANE",
                 "position": {"x": 9000, "y": 9000}},
                {"type": "ELITE_MONSTER_KILL", "timestamp": 80_000, "killerId": 2, "killerTeamId": 100,
                 "monsterType": "DRAGON", "monsterSubType": "FIRE_DRAGON", "position": {"x": 9800, "y": 4400}},
                {"type": "CHAMPION_KILL", "timestamp": 90_000, "killerId": 0, "victimId": 3,
                 "position": {"x": MAX_X, "y": MAX_Y},
                 "victimDamageReceived": [{"type": "TOWER", "participantId": 0}]},
                {"type": "WARD_PLACED", "timestamp": 100_000, "creatorId": 5, "wardType": "CONTROL_WARD"},
                {"type": "ELITE_MONSTER_KILL", "timestamp": 110_000, "killerId": 7, "killerTeamId": 200,
                 "monsterType": "HORDE", "position": {"x": 4800, "y": 10200}},
            ],
            "participantFrames": {str(p): pframe(p, hp=150 if p == 4 else 600) for p in range(1, 11)},
        },
    ]
    return match, {"info": {"frames": frames}}


def add_events(timeline: dict, frame: int, *events: dict) -> dict:
    """Append events to a frame (keeps them in timestamp order)."""
    evs = timeline["info"]["frames"][frame]["events"]
    evs.extend(events)
    evs.sort(key=lambda e: e["timestamp"])
    return timeline
