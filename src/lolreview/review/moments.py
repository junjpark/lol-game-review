"""Phase 6 — the moments worth reviewing. Implements docs/phase6-spec.md; read that first.

    review = review_game(match, timeline, participant_id=4)
    review.moments    # your moments, in game-time order (up to 8)
    review.context    # game swings you weren't part of (up to 3)

Everything here is deterministic code over: the extracted tables, the win-probability model's
curve, the swing table (`models/wpa_distribution.json`) and the measured death timers. No new
model. Spec numbers live in SPEC below so tests and the spec can't silently drift apart.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from ..rift import STRUCTURES, region_of
from .death_timer import death_seconds

# ---- spec constants (docs/phase6-spec.md; fixed Nov 2 before seeing the owner's games) ----
SPEC = {
    "fight_gap_s": 15,         # kills chain if ≤ 15 s apart …
    "link_units": 3000,        # … and ≤ 3,000 units from a kill already in the cluster …
    "close_units": 1500,       # … and either share a player or are ≤ 1,500 units apart
    "objective_lead_s": 30,    # kills ≤ 30 s before an objective/structure join it
    "nearby_units": 2500,      # measured: catches 74% of credited participants, 25% of others
    "max_span_min": 3,
    "show_pct": 75, "notable_pct": 90, "major_pct": 99,
    "max_moments": 8, "max_context": 3, "context_pct": 90, "quiet_pct": 90,
    "min_spacing_min": 2,
    "shop_visit_gap_s": 30,
    # amendment (Nov 2, before any owner game was reviewed): percentiles alone let tiny early
    # swings rank high (2 pts at minute 0 was "top 10%"). A shown moment must also move win
    # probability by at least a typical one-minute swing.
    "min_abs_swing": 0.05,
}

ANCHOR_TYPES = ("CHAMPION_KILL", "ELITE_MONSTER_KILL", "BUILDING_KILL")

# Riot's championName is an internal id ("MonkeyKing", "MissFortune"). Display names: these
# exceptions, otherwise split CamelCase.
CHAMPION_DISPLAY = {
    "MonkeyKing": "Wukong", "FiddleSticks": "Fiddlesticks", "KogMaw": "Kog'Maw", "KSante": "K'Sante",
    "Chogath": "Cho'Gath", "Khazix": "Kha'Zix", "Velkoz": "Vel'Koz", "Kaisa": "Kai'Sa", "Leblanc": "LeBlanc",
    "BelVeth": "Bel'Veth", "Belveth": "Bel'Veth", "RekSai": "Rek'Sai", "JarvanIV": "Jarvan IV", "DrMundo": "Dr. Mundo",
    "Nunu": "Nunu & Willump", "Renata": "Renata Glasc",
}


def display_name(champion: str | None) -> str:
    if not champion:
        return "?"
    if champion in CHAMPION_DISPLAY:
        return CHAMPION_DISPLAY[champion]
    out = champion[0]
    for prev, ch in zip(champion, champion[1:]):
        out += (" " if ch.isupper() and prev.islower() else "") + ch
    return out
MONSTER_NAMES = {"FIRE_DRAGON": "Infernal Drake", "WATER_DRAGON": "Ocean Drake", "EARTH_DRAGON": "Mountain Drake",
                 "AIR_DRAGON": "Cloud Drake", "HEXTECH_DRAGON": "Hextech Drake", "CHEMTECH_DRAGON": "Chemtech Drake",
                 "ELDER_DRAGON": "Elder Dragon", "BARON_NASHOR": "Baron", "RIFTHERALD": "Rift Herald",
                 "HORDE": "Voidgrub", "ATAKHAN": "Atakhan", "DRAGON": "Dragon"}


# ---- swing table / percentiles -----------------------------------------------------------

def load_swing_table(model_dir: Path | None = None) -> dict:
    from ..config import MODEL_DIR
    from ..model.predict import WPA_FILE
    path = Path(model_dir or MODEL_DIR) / WPA_FILE
    if not path.exists():
        raise FileNotFoundError(f"No swing table at {path}. Run: python scripts/train_winprob.py")
    return json.loads(path.read_text())


def swing_percentile(table: dict, k: int, start_minute: int, value: float) -> tuple[float, str | None]:
    """Percentile (0–100) of |swing| among corpus k-minute swings starting at `start_minute`.
    Returns (percentile, note). Older tables without k-minute windows fall back to 1-minute
    swings (note says so: rerun scripts/train_winprob.py to get them)."""
    note = None
    windows = table.get("windows")
    if windows and str(k) in windows:
        rows = windows[str(k)]
        qs = table["window_quantiles"]
        key = str(start_minute) if str(start_minute) in rows else _nearest_key(rows, start_minute)
    else:
        rows, qs = table["by_minute"], table["quantiles"]
        key = str(start_minute + 1) if str(start_minute + 1) in rows else _nearest_key(rows, start_minute + 1)
        note = "swing table has no multi-minute windows (rerun train_winprob.py); compared with 1-minute swings"
    cell = rows[key]
    xs = [0.0] + [cell[f"q{q * 100:g}"] if f"q{q * 100:g}" in cell else cell[f"q{int(q * 100)}"] for q in qs]
    ps = [0.0] + [q * 100 for q in qs]
    xs = np.maximum.accumulate(np.asarray(xs, float) + np.arange(len(xs)) * 1e-9)
    v = abs(value)
    if v >= xs[-1]:
        return min(99.9, ps[-1] + (100 - ps[-1]) * min(1.0, (v - xs[-1]) / max(xs[-1], 1e-9))), note
    return float(np.interp(v, xs, ps)), note


def _nearest_key(rows: dict, minute: int) -> str:
    keys = sorted(int(k) for k in rows)
    below = [k for k in keys if k <= minute]
    return str(below[-1] if below else keys[0])


def label_for(pct: float) -> str:
    return "major" if pct >= SPEC["major_pct"] else "notable" if pct >= SPEC["notable_pct"] else "moderate"


# ---- clusters --------------------------------------------------------------------------

def _dist(a: dict, b: dict) -> float:
    if a.get("x") is None or b.get("x") is None:
        return math.inf
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def _people(e: dict) -> set[int]:
    return {p for p in (e.get("actor_id"), e.get("victim_id"), *(e.get("assists") or [])) if p}


def cluster_events(events: list[dict]) -> list[list[dict]]:
    """Group kills, objectives and structures into clusters (spec §3). Union-find over pairs:
    any two events ≤ fight_gap_s and ≤ link_units apart; a kill ≤ objective_lead_s before an
    objective/structure and ≤ link_units from it. Clusters longer than max_span_min are split at
    their largest time gap."""
    ev = sorted((e for e in events if e["type"] in ANCHOR_TYPES), key=lambda e: e["t_ms"])
    parent = list(range(len(ev)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    gap, lead, link = SPEC["fight_gap_s"] * 1000, SPEC["objective_lead_s"] * 1000, SPEC["link_units"]
    for i in range(len(ev)):
        for j in range(i + 1, len(ev)):
            dt = ev[j]["t_ms"] - ev[i]["t_ms"]
            if dt > lead:
                break
            d = _dist(ev[i], ev[j])
            # same fight = close in time and place AND (a player in common, or very close).
            # Measured Nov 2 (800 games): 92% of kill–kill links share a player; the rule splits
            # 4% of them and 10% of structure/objective links: separate pushes inside a base,
            # where everything sits within 3,000 units (owner's validation game).
            close = d <= link and (bool(_people(ev[i]) & _people(ev[j])) or d <= SPEC["close_units"])
            if not close:
                continue
            leads_objective = ev[i]["type"] == "CHAMPION_KILL" and ev[j]["type"] != "CHAMPION_KILL"
            if dt <= gap or (leads_objective and dt <= lead):
                parent[find(j)] = find(i)
    groups: dict[int, list[dict]] = {}
    for i, e in enumerate(ev):
        groups.setdefault(find(i), []).append(e)
    out = []
    for g in groups.values():
        out.extend(_split_long(sorted(g, key=lambda e: e["t_ms"])))
    return sorted(out, key=lambda g: g[0]["t_ms"])


def merge_by_snapshot(clusters: list[list[dict]], snaps_t: list[int]) -> list[list[list[dict]]]:
    """Spec D3: the model only sees minute snapshots, so separate fights between the same
    snapshots share ONE swing. Group clusters whose snapshot spans overlap into one moment, as
    long as the group's span stays ≤ max_span_min. Otherwise each would claim the whole swing
    (found Nov 2: a bot-lane kill credited with a 4–0 minute).

    Returns groups of PARTS. Each part stays a separate cluster with its own place, fight size
    and involvement; only the swing is shared. (Owner's validation, Nov 2: merging the parts
    themselves put a top-lane 1v1 'in' the dragon fight, so he was told he fought there.)"""
    def span(cl):
        return (_snap_at_or_before(snaps_t, cl[0]["t_ms"]), _snap_at_or_after(snaps_t, cl[-1]["t_ms"]))
    out: list[list[list[dict]]] = []
    for cl in sorted(clusters, key=lambda c: c[0]["t_ms"]):
        if out:
            a0 = min(span(p)[0] for p in out[-1])
            b0 = max(span(p)[1] for p in out[-1])
            a1, b1 = span(cl)
            if a1 < b0 and max(b0, b1) - min(a0, a1) <= SPEC["max_span_min"]:
                out[-1].append(cl)
                continue
        out.append([cl])
    return out


def _split_long(g: list[dict]) -> list[list[dict]]:
    if len(g) < 2 or g[-1]["t_ms"] - g[0]["t_ms"] <= (SPEC["max_span_min"] - 1) * 60_000:
        return [g]
    gaps = [g[i + 1]["t_ms"] - g[i]["t_ms"] for i in range(len(g) - 1)]
    cut = int(np.argmax(gaps)) + 1
    return _split_long(g[:cut]) + _split_long(g[cut:])


# ---- the review ------------------------------------------------------------------------

@dataclass
class Moment:
    rank: int | None
    kind: str                      # fight / objective / structure / quiet
    minute_start: int              # snapshot before the first event
    minute_end: int                # snapshot after the last event
    t_first_ms: int | None
    t_last_ms: int | None
    swing: float                   # your team's win probability, end − start (−1…1)
    p_start: float
    p_end: float
    percentile: float
    label: str                     # moderate / notable / major
    direction: str                 # good / bad
    involvement: str               # direct / dead / nearby / none (best over the parts)
    involvement_detail: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    summary: str = ""
    # separate things that happened between the same snapshots: each with its own place, fight
    # size and YOUR involvement in that part only. The swing above is shared (spec D3).
    parts: list[dict] = field(default_factory=list)
    direct_cost: dict | None = None
    shop_visits: list[list[int]] = field(default_factory=list)
    confidence_notes: list[str] = field(default_factory=list)


@dataclass
class Review:
    match_id: str
    player: dict
    team_id: int
    series: list[dict]
    moments: list[Moment]
    context: list[Moment]
    notes: list[str]
    spec: dict

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _base_geometry(team: int) -> tuple[float, float, float]:
    """Own base: centre between the nexus turrets, radius to just past the base turrets."""
    nex = [s for s in STRUCTURES if s["team"] == team and s["tier"] == "nexus"]
    cx, cy = sum(s["x"] for s in nex) / len(nex), sum(s["y"] for s in nex) / len(nex)
    base = [s for s in STRUCTURES if s["team"] == team and s["tier"] == "base"]
    r = max(math.hypot(s["x"] - cx, s["y"] - cy) for s in base) + 300
    return cx, cy, r


def review_game(match: dict, timeline: dict, participant_id: int, extracted: dict | None = None,
                curve=None, swing_table: dict | None = None, tier: str = "emerald") -> Review:
    from ..extract import extract_game

    extracted = extracted or extract_game(match, timeline, tier)
    if curve is None:
        from ..model.predict import WinProb
        curve = WinProb.load().for_game(extracted)
    swing_table = swing_table or load_swing_table()

    parts = {p["participantId"]: p for p in match["info"]["participants"]}
    me = parts[participant_id]
    team = me["teamId"]
    enemy = 300 - team
    names = {pid: display_name(p.get("championName")) for pid, p in parts.items()}

    snaps_t = [int(t) for t in curve["t_ms"]]
    p_blue = np.asarray(curve["p_blue"], float)
    p_team = p_blue if team == 100 else 1 - p_blue
    last = len(snaps_t) - 1
    # the final snapshot is after the nexus falls and the model can't see the nexus: don't let a
    # moment end there (spec: series is the model's estimate, not the result)
    usable_last = max(last - 1, 1)

    frames = {(r["minute"], r["participant_id"]): r for r in extracted["frames"]}
    events = extracted["events"]
    notes: list[str] = []

    # deaths of the reviewed player -> dead intervals (measured timers)
    deaths = []
    for e in events:
        if e["type"] == "CHAMPION_KILL" and e.get("victim_id") == participant_id:
            m_before = max(0, _snap_at_or_before(snaps_t, e["t_ms"]))
            lvl = (frames.get((m_before, participant_id)) or {}).get("level") or 1
            secs = death_seconds(int(lvl), e["t_ms"] / 60000)
            deaths.append((e["t_ms"], e["t_ms"] + int(secs * 1000), int(lvl), secs))

    def dead_at(t: int) -> bool:
        return any(start <= t < respawn for start, respawn, *_ in deaths)

    shop = _shop_visits(events, participant_id)

    candidates: list[Moment] = []
    covered = np.zeros(last + 1, bool)
    team_of = {pid: p["teamId"] for pid, p in parts.items()}
    for group in merge_by_snapshot(cluster_events(events), snaps_t):
        cl = sorted((e for c in group for e in c), key=lambda e: e["t_ms"])
        a = _snap_at_or_before(snaps_t, cl[0]["t_ms"])
        b = min(_snap_at_or_after(snaps_t, cl[-1]["t_ms"]), usable_last)
        if b <= a:
            continue
        covered[a:b] = True
        k = min(b - a, SPEC["max_span_min"])
        swing = float(p_team[b] - p_team[a])
        pct, note = swing_percentile(swing_table, k, a, swing)
        if note and note not in notes:
            notes.append(note)
        part_views = [_part(c, participant_id, team, enemy, frames, snaps_t, dead_at, names, team_of)
                      for c in sorted(group, key=lambda c: c[0]["t_ms"])]
        inv = min((p["involvement"] for p in part_views), key=INVOLVEMENT_ORDER.index)
        detail = [f"{p['where']}: {d}" for p in part_views for d in p["involvement_detail"]]
        mom = Moment(
            rank=None, kind=_kind(cl), minute_start=a, minute_end=b,
            t_first_ms=cl[0]["t_ms"], t_last_ms=cl[-1]["t_ms"], swing=round(swing, 4),
            p_start=round(float(p_team[a]), 4), p_end=round(float(p_team[b]), 4),
            percentile=round(pct, 1), label=label_for(pct), direction="good" if swing >= 0 else "bad",
            involvement=inv, involvement_detail=detail,
            events=[_event_view(e, names, team) for e in cl],
            summary=" · ".join(p["text"] for p in part_views),
            parts=part_views,
            shop_visits=[list(v) for v in shop if v[1] >= snaps_t[a] and v[0] <= snaps_t[b]],
        )
        if len(part_views) > 1:
            mom.confidence_notes.append(f"{len(part_views)} separate things happened between the same minute "
                                        "snapshots; the swing is their combined effect and can't be split")
        if any(p["involvement"] == "nearby" for p in part_views):
            mom.confidence_notes.append("nearby, not credited: you were close (minute snapshots) but not "
                                        "listed in the kill/objective")
        my_deaths = [d for d in deaths if cl[0]["t_ms"] - 1 <= d[0] <= cl[-1]["t_ms"] + 1]
        if my_deaths:
            mom.direct_cost = _direct_cost(my_deaths[0], participant_id, team, enemy, frames, snaps_t, events,
                                           names, extracted["frames"])
            mom.confidence_notes.append("time back in play is to the minute (snapshots are 1 minute apart)")
        candidates.append(mom)

    # quiet swings: big 1-minute moves with no cluster (farm, plates, slow pressure)
    for m in range(0, usable_last):
        if covered[m]:
            continue
        swing = float(p_team[m + 1] - p_team[m])
        pct, _ = swing_percentile(swing_table, 1, m, swing)
        if pct >= SPEC["quiet_pct"]:
            candidates.append(Moment(rank=None, kind="quiet", minute_start=m, minute_end=m + 1, t_first_ms=None,
                                     t_last_ms=None, swing=round(swing, 4), p_start=round(float(p_team[m]), 4),
                                     p_end=round(float(p_team[m + 1]), 4), percentile=round(pct, 1),
                                     label=label_for(pct), direction="good" if swing >= 0 else "bad",
                                     involvement="none", summary="no fight or objective: gold/XP/pressure moved",
                                     confidence_notes=["no single event explains this swing"]))

    big = [c for c in candidates if abs(c.swing) >= SPEC["min_abs_swing"]]
    mine = [c for c in big if c.involvement != "none" and c.percentile >= SPEC["show_pct"]]
    chosen = _select(mine, SPEC["max_moments"], [])
    others = [c for c in big if c.involvement == "none" and c.percentile >= SPEC["context_pct"]]
    context = _select(others, SPEC["max_context"], chosen)
    for i, m in enumerate(sorted(chosen, key=lambda m: (-m.percentile, -abs(m.swing))), 1):
        m.rank = i
    for i, m in enumerate(sorted(context, key=lambda m: (-m.percentile, -abs(m.swing))), 1):
        m.rank = i

    series = [{"minute": i, "t_ms": snaps_t[i], "p_team": round(float(p_team[i]), 4)} for i in range(last + 1)]
    player = {"participant_id": participant_id, "champion": me.get("championName"), "role": me.get("teamPosition"),
              "riot_id": f"{me.get('riotIdGameName', '')}#{me.get('riotIdTagline', '')}", "team": team,
              "win": bool(me.get("win"))}
    return Review(match["metadata"]["matchId"], player, team, series,
                  sorted(chosen, key=lambda m: m.minute_start), sorted(context, key=lambda m: m.minute_start),
                  notes, dict(SPEC))


# ---- presentation ----------------------------------------------------------------------

def _mmss(ms: int | None) -> str:
    if ms is None:
        return "?"
    s = int(ms // 1000)
    return f"{s // 60}:{s % 60:02d}"


INVOLVEMENT_TEXT = {"direct": "you were in it", "dead": "happened while you were dead",
                    "nearby": "you were nearby (not credited)", "none": "you weren't part of it"}


def describe(m: Moment, review: Review) -> tuple[str, str, str]:
    """(heading, detail, tooltip) in plain words. Comparative, no verdict words (spec D4)."""
    t0 = review.series[m.minute_start]["t_ms"]
    t1 = review.series[m.minute_end]["t_ms"]
    who = "your team's" if m.involvement != "none" else "your team's"
    heading = (f"{'#' + str(m.rank) if m.involvement != 'none' else 'context'} · {_mmss(t0)}–{_mmss(t1)} · "
               f"{m.label} · {100 * m.swing:+.0f} pts" + (f" · {len(m.parts)} things at once" if len(m.parts) > 1 else ""))
    # one line per part (each says your involvement in it); quiet swings have no parts
    lines = [p["text"] for p in m.parts] or [m.summary or m.kind, INVOLVEMENT_TEXT.get(m.involvement, "")]
    parts = []
    if m.direct_cost:
        dc = m.direct_cost
        bits = [f"dead {dc['seconds_dead']:.0f}s"]
        if dc.get("back_in_play_minute") is not None:
            bits.append(f"back in play by {dc['back_in_play_minute']}:00")
        vs = []   # negative = you fell behind your lane opponent over the lockout
        if dc.get("cs_forgone") is not None:
            vs.append(f"CS {-dc['cs_forgone']:+d}")
        if dc.get("xp_forgone") is not None:
            vs.append(f"XP {-dc['xp_forgone']:+,d}")
        if vs:
            bits.append(f"vs {dc.get('lane_opponent') or 'lane opponent'} meanwhile: " + ", ".join(vs))
        if dc.get("objectives_lost"):
            bits.append("lost meanwhile: " + ", ".join(o["what"] for o in dc["objectives_lost"]))
        parts.append("; ".join(bits))
    if m.shop_visits:
        parts.append("you were in base buying at " + ", ".join(_mmss(v[0]) for v in m.shop_visits))
    detail = "\n".join([ln for ln in lines if ln] + ([" · ".join(p for p in parts if p)] if parts else []))
    tooltip = (f"{heading}\n{who} win probability {100 * m.p_start:.0f}% → {100 * m.p_end:.0f}% "
               f"(bigger than {m.percentile:.0f}% of swings at this point in Emerald games)\n{detail}"
               + ("\n" + "\n".join(m.confidence_notes) if m.confidence_notes else ""))
    return heading, detail, tooltip


def moments_markers(review: Review) -> list[dict]:
    out = []
    for m, mine in [(m, True) for m in review.moments] + [(m, False) for m in review.context]:
        heading, detail, tip = describe(m, review)
        out.append({"t0": review.series[m.minute_start]["t_ms"], "t1": review.series[m.minute_end]["t_ms"],
                    "frame": min(m.minute_start + 1, m.minute_end), "rank": m.rank, "mine": mine,
                    "direction": m.direction, "heading": heading, "detail": detail, "title": tip})
    return sorted(out, key=lambda d: d["t0"])


def format_table(review: Review) -> str:
    p = review.player
    lines = [f"{p['champion']} ({p['role']}, {p['riot_id']}) — {'win' if p['win'] else 'loss'}",
             f"Moments to review: {len(review.moments)} (shown: ≥ {SPEC['show_pct']}th percentile swing for that "
             f"point in the game, ≥ {100 * SPEC['min_abs_swing']:.0f} pts)"]
    for m in review.moments:
        h, d, _ = describe(m, review)
        lines += [f"  {h}"] + [f"      {ln}" for ln in d.split("\n")]
        for n in m.confidence_notes:
            lines.append(f"      note: {n}")
    if review.context:
        lines.append("Game swings you weren't part of (context, not about you):")
        for m in review.context:
            h, d, _ = describe(m, review)
            lines += [f"  {h}"] + [f"      {ln}" for ln in d.split("\n")]
    for n in review.notes:
        lines.append(f"note: {n}")
    return "\n".join(lines)


# ---- helpers ---------------------------------------------------------------------------

def _select(cands: list[Moment], limit: int, taken: list[Moment]) -> list[Moment]:
    """Highest percentile first (ties: bigger swing). Starts at least min_spacing_min apart;
    never overlapping a moment already in `taken` (your moments, when picking context)."""
    out: list[Moment] = []
    for c in sorted(cands, key=lambda m: (-m.percentile, -abs(m.swing))):
        if len(out) >= limit:
            break
        if any(abs(c.minute_start - o.minute_start) < SPEC["min_spacing_min"] for o in out):
            continue
        if any(c.minute_start < t.minute_end and t.minute_start < c.minute_end for t in taken):
            continue
        out.append(c)
    return out


def _snap_at_or_before(snaps_t: list[int], t: int) -> int:
    i = int(np.searchsorted(snaps_t, t, side="right")) - 1
    return max(i, 0)


def _snap_at_or_after(snaps_t: list[int], t: int) -> int:
    i = int(np.searchsorted(snaps_t, t, side="left"))
    return min(i, len(snaps_t) - 1)


def _kind(cl: list[dict]) -> str:
    types = {e["type"] for e in cl}
    return "objective" if "ELITE_MONSTER_KILL" in types else "structure" if "BUILDING_KILL" in types else "fight"


def _involvement(cl, pid, team, enemy, frames, snaps_t, dead_at, names=None) -> tuple[str, list[str]]:
    """direct / dead / nearby / none, with details that name exactly what the game credited, so
    the player can check it ("credited with an assist on Miss Fortune")."""
    names = names or {}
    detail = []
    for e in cl:
        helpers = set(e.get("assists") or [])
        victim = names.get(e.get("victim_id"), "a champion")
        if e["type"] == "CHAMPION_KILL":
            if e.get("actor_id") == pid:
                detail.append(f"killed {victim}")
            if e.get("victim_id") == pid:
                detail.append(f"died to {names.get(e.get('actor_id'), 'turret/minions')}")
            if pid in helpers:
                detail.append(f"credited with an assist on {victim}")
        elif e.get("actor_id") == pid:
            detail.append(f"took {_obj_name(e, team)}")
        elif pid in helpers:
            detail.append(f"credited with helping take {_obj_name(e, team)}")
    if detail:
        return "direct", list(dict.fromkeys(detail))
    for e in cl:
        if e["type"] != "CHAMPION_KILL" and e.get("team_id") == enemy and dead_at(e["t_ms"]):
            return "dead", [f"dead when the enemy took {_obj_name(e, team)}"]
    for e in cl:
        if dead_at(e["t_ms"]) or e.get("x") is None:
            continue
        for m in {_snap_at_or_before(snaps_t, e["t_ms"]), _snap_at_or_after(snaps_t, e["t_ms"])}:
            f = frames.get((m, pid))
            if f and f.get("x") is not None and math.hypot(f["x"] - e["x"], f["y"] - e["y"]) <= SPEC["nearby_units"]:
                return "nearby", [f"within {SPEC['nearby_units']:,} units at minute {m}"]
    return "none", []


def _obj_name(e: dict, team: int | None = None) -> str:
    """'Baron', 'your mid outer tower', 'their top inhibitor' (structure ids are owner_lane_tier)."""
    if e["type"] == "ELITE_MONSTER_KILL":
        return MONSTER_NAMES.get(e.get("monster") or "", e.get("monster") or "an objective")
    sid = e.get("structure_id") or ""
    parts = sid.split("_")
    if len(parts) == 3 and parts[0].isdigit():
        owner = int(parts[0])
        whose = "your" if team is not None and owner == team else "their" if team is not None else (
            "Blue's" if owner == 100 else "Red's")
        if parts[1] == "nexus":
            return f"{whose} nexus tower"
        what = "inhibitor" if parts[2] == "inhib" else f"{parts[2]} tower"
        return f"{whose} {parts[1]} {what}"
    return "an inhibitor" if "inhib" in sid else "a tower"


def _event_view(e: dict, names: dict, team: int) -> dict:
    side = "you" if e.get("team_id") == team else "enemy" if e.get("team_id") in (100, 200) else "?"
    v = {"t_ms": e["t_ms"], "type": e["type"], "by": side, "where": region_of(e.get("x"), e.get("y"), team)}
    if e["type"] == "CHAMPION_KILL":
        v.update(killer=names.get(e.get("actor_id"), "execution"), victim=names.get(e.get("victim_id")),
                 assists=[names.get(a) for a in e.get("assists") or []])
    else:
        v.update(what=_obj_name(e, team))
    return v


INVOLVEMENT_ORDER = ["direct", "dead", "nearby", "none"]
INVOLVEMENT_SUFFIX = {"dead": "you were dead at the time", "nearby": "you were nearby, not credited",
                      "none": "you weren't there"}


def _part(cl, pid, team, enemy, frames, snaps_t, dead_at, names, team_of) -> dict:
    """One spatial cluster as a readable part: where it happened, what size of fight, what was
    taken, and the reviewed player's involvement in THIS part only."""
    inv, detail = _involvement(cl, pid, team, enemy, frames, snaps_t, dead_at, names)
    kills = [e for e in cl if e["type"] == "CHAMPION_KILL"]
    objs = [e for e in cl if e["type"] != "CHAMPION_KILL"]
    if objs:
        where = region_of(objs[-1].get("x"), objs[-1].get("y"), team)
    else:
        where = Counter(region_of(e.get("x"), e.get("y"), team) for e in kills).most_common(1)[0][0]
    people = {p for e in kills for p in [e.get("actor_id"), e.get("victim_id"), *(e.get("assists") or [])]
              if p in team_of}
    n_us = sum(1 for p in people if team_of[p] == team)
    n_them = len(people) - n_us
    k_us = sum(1 for e in kills if e.get("team_id") == team)
    k_them = len(kills) - k_us
    pieces, says_you = [], False

    def who(p, killer=False):
        if p == pid:
            return "you"
        if p in names:
            return names[p]
        return "turret/minions" if killer else "?"

    if len(kills) == 1 and len(people) <= 2:
        e = kills[0]
        killer, victim = e.get("actor_id"), e.get("victim_id")
        if victim == pid:
            pieces.append(f"you died to {who(killer, True)}")
        else:
            pieces.append(f"{who(killer, True)} killed {who(victim)}")
        says_you = pid in (killer, victim)
    elif kills:
        size = "teamfight" if max(n_us, n_them) >= 3 else "skirmish"
        result = "won" if k_us > k_them else "lost" if k_us < k_them else "traded"
        pieces.append(f"{n_us}v{n_them} {size}, your team {result} {k_us}–{k_them}")
    for e in objs:
        pieces.append(("your team took " if e.get("team_id") == team else "enemy took ") + _obj_name(e, team))
    if inv == "direct":
        suffix = "" if says_you else "you: " + "; ".join(detail)
    else:
        suffix = INVOLVEMENT_SUFFIX[inv]
    text = f"{where[:1].upper()}{where[1:]}: " + "; ".join(pieces) + (f" ({suffix})" if suffix else "")
    return {"where": where, "t_first_ms": cl[0]["t_ms"], "t_last_ms": cl[-1]["t_ms"],
            "fight": {"us": n_us, "them": n_them, "kills_us": k_us, "kills_them": k_them} if kills else None,
            "involvement": inv, "involvement_detail": detail, "text": text}


def _shop_visits(events, pid) -> list[tuple[int, int]]:
    ts = sorted(e["t_ms"] for e in events if e["type"] == "ITEM_PURCHASED" and e.get("actor_id") == pid)
    visits = []
    for t in ts:
        if visits and t - visits[-1][1] <= SPEC["shop_visit_gap_s"] * 1000:
            visits[-1][1] = t
        else:
            visits.append([t, t])
    return [tuple(v) for v in visits]


def _direct_cost(death, pid, team, enemy, frames, snaps_t, events, names, frame_rows) -> dict:
    t_death, t_respawn, level, secs = death
    cx, cy, r = _base_geometry(team)
    s0 = _snap_at_or_before(snaps_t, t_death)
    s1 = None
    for j in range(_snap_at_or_after(snaps_t, t_respawn), len(snaps_t)):
        f = frames.get((j, pid))
        if f and f.get("x") is not None and math.hypot(f["x"] - cx, f["y"] - cy) > r:
            s1 = j
            break
    end = s1 if s1 is not None else len(snaps_t) - 1
    me0, me1 = frames.get((s0, pid)) or {}, frames.get((end, pid)) or {}
    opp = me0.get("opp_id")
    o0, o1 = (frames.get((s0, opp)) or {}, frames.get((end, opp)) or {}) if opp else ({}, {})
    role = me0.get("role")

    def delta(a, b, k):
        return (b.get(k) or 0) - (a.get(k) or 0)

    cost = {
        "level_at_death": level, "seconds_dead": round(secs, 1),
        "respawn_ms": t_respawn,
        "back_in_play_minute": s1,              # None = didn't leave base again before the game ended
        "lockout_minutes": [s0, end],
        "lane_opponent": names.get(opp) if opp else None,
    }
    if opp:
        if role != "UTILITY":
            cost["cs_forgone"] = delta(o0, o1, "cs") - delta(me0, me1, "cs")
        cost["xp_forgone"] = delta(o0, o1, "xp") - delta(me0, me1, "xp")
    t_end = snaps_t[end]
    cost["objectives_lost"] = [
        {"t_ms": e["t_ms"], "what": _obj_name(e, team)} for e in events
        if e["type"] in ("ELITE_MONSTER_KILL", "BUILDING_KILL") and e.get("team_id") == enemy
        and t_death <= e["t_ms"] <= t_end]
    return cost
