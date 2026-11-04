"""Golden review fixtures: frozen inputs and expected outputs for a few real games.

Each fixture in tests/golden/ holds one game's match + timeline (identities removed), the reviewed
participant, and the win-probability curve frozen at the time it was made. The swing table is
frozen once in tests/golden/swing_table.json. So a golden test exercises the review logic
(clustering, parts, involvement, death cost, wording) and doesn't move when the model is retrained;
the model has its own gate.

Make or refresh fixtures with scripts/make_golden.py. Re-freeze expected outputs after an
intended change with:  UPDATE_GOLDEN=1 pytest tests/test_golden.py   (then read the git diff).
"""

from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

GOLDEN_DIR = Path(__file__).parent / "golden"
SWING_TABLE = GOLDEN_DIR / "swing_table.json"
IDENTITY_FIELDS = ("summonerName", "summonerId", "profileIcon", "summonerLevel")


def anonymize(match: dict, timeline: dict) -> tuple[dict, dict]:
    """Remove player identities. Riot IDs become Player<pid>#GOLD, PUUIDs become puuid-<pid>,
    consistently in the match and the timeline, so joins still work."""
    match, timeline = copy.deepcopy(match), copy.deepcopy(timeline)
    puuid_map = {}
    for p in match["info"]["participants"]:
        pid = p["participantId"]
        puuid_map[p.get("puuid")] = f"puuid-{pid}"
        p["puuid"] = f"puuid-{pid}"
        p["riotIdGameName"], p["riotIdTagline"] = f"Player{pid}", "GOLD"
        for f in IDENTITY_FIELDS:
            if f in p:
                p[f] = "" if isinstance(p[f], str) else 0
    for doc in (match, timeline):
        meta = doc.get("metadata") or {}
        if "participants" in meta:
            meta["participants"] = [puuid_map.get(x, "puuid-unknown") for x in meta["participants"]]
    for p in (timeline.get("info") or {}).get("participants") or []:
        p["puuid"] = f"puuid-{p['participantId']}"
    return match, timeline


def fixture_path(match_id: str) -> Path:
    return GOLDEN_DIR / f"{match_id}.json.gz"


def expected_path(match_id: str) -> Path:
    return GOLDEN_DIR / f"{match_id}.expected.json"


def write_fixture(match: dict, timeline: dict, participant_id: int, curve, note: str = "") -> Path:
    """curve: anything with t_ms and p_blue columns (WinProb.for_game output)."""
    match, timeline = anonymize(match, timeline)
    mid = match["metadata"]["matchId"]
    doc = {"match_id": mid, "participant_id": int(participant_id), "note": note,
           "curve": {"t_ms": [int(t) for t in curve["t_ms"]],
                     "p_blue": [round(float(p), 6) for p in curve["p_blue"]]},
           "match": match, "timeline": timeline}
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    path = fixture_path(mid)
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(doc, fh, separators=(",", ":"))
    return path


def load_fixture(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def all_fixtures() -> list[Path]:
    return sorted(GOLDEN_DIR.glob("*.json.gz"))


def run_review(fx: dict):
    from lolreview.review.moments import review_game
    table = json.loads(SWING_TABLE.read_text())
    return review_game(fx["match"], fx["timeline"], fx["participant_id"],
                       curve=fx["curve"], swing_table=table)


def normalize(obj):
    """Round floats so tiny platform differences in the last bits don't fail the test."""
    if isinstance(obj, float):
        return round(obj, 4)
    if isinstance(obj, dict):
        return {k: normalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [normalize(v) for v in obj]
    return obj


def review_output(fx: dict) -> dict:
    """What the golden test compares: the review payload plus the terminal text."""
    from lolreview.review.moments import format_table
    review = run_review(fx)
    return normalize({"review": json.loads(json.dumps(review.to_dict())),
                      "text": format_table(review).split("\n")})
