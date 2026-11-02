"""Phase 2 — match replay visualizer.

Turns one match + timeline into a self-contained HTML file: the map with all ten players
at each one-minute snapshot, a scrubber, the event list, gold/XP difference curves, and a
per-player table. It's a debugging tool, not product UI: every later rule that says "this
player was in the river" or "took 38s to get back to lane" gets checked against it.

Positions are only known once per minute, so the replay steps frame to frame rather than
animating between them. Interpolating would draw positions the data doesn't contain,
which is exactly the kind of thing a debugging view must not do.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from ..extract.events import CATEGORY, category  # noqa: F401  (shared with the tables)
from ..rift import (  # noqa: F401  (bounds re-exported for callers/tests)
    CAMPS,
    INHIBITOR_RESPAWN_MS,
    MAX_X,
    MAX_Y,
    MIN_X,
    MIN_Y,
    PITS,
    STRUCTURES,
    normalize,
    structure_for_event,
)
from ..riot.queues import RANKED_SOLO, queue_name

log = logging.getLogger(__name__)

_STRUCTURE_BY_ID = {s["id"]: s for s in STRUCTURES}

TEAM_NAME = {100: "Blue", 200: "Red"}
ROLE_ORDER = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]

MONSTER_NAMES = {
    "HORDE": "Voidgrubs",
    "RIFTHERALD": "Rift Herald",
    "BARON_NASHOR": "Baron",
    "ATAKHAN": "Atakhan",
}

WARD_NAMES = {
    "YELLOW_TRINKET": "a trinket ward",
    "CONTROL_WARD": "a control ward",
    "SIGHT_WARD": "a ward",
    "BLUE_TRINKET": "a farsight ward",
    # Not wards: Riot logs champion ability objects as ward events (Zyra seeds, Jhin
    # traps, Yorick graves, Illaoi tentacles, ...) and Teemo's mushrooms.
    "UNDEFINED": "an ability object (trap, plant, etc.)",
    "TEEMO_MUSHROOM": "a mushroom",
}

MULTI = {2: "double kill", 3: "triple kill", 4: "quadra kill", 5: "penta kill"}
SKILL_KEYS = {1: "Q", 2: "W", 3: "E", 4: "R"}


def _other(team: int) -> int:
    return 200 if team == 100 else 100


def _title(s: str) -> str:
    return s.replace("_", " ").title()


def _norm(pos: dict) -> tuple[float, float]:
    """Game coordinates -> [0, 1] with y pointing up (blue base bottom-left)."""
    return normalize(pos["x"], pos["y"])


def _landmarks(events: list[dict]) -> dict:
    """Static map features, plus when each structure was down during this game.

    `down` is a list of [from_ms, until_ms] intervals; until is None for turrets (they
    never come back) and destruction + 5 min for inhibitors.
    """
    down: dict[str, list] = {s["id"]: [] for s in STRUCTURES}
    for e in events:
        if e["type"] != "BUILDING_KILL":
            continue
        s = structure_for_event(e)
        if s is None:
            continue
        until = e["timestamp"] + INHIBITOR_RESPAWN_MS if s["kind"] == "inhibitor" else None
        down[s["id"]].append([e["timestamp"], until])

    def placed(d: dict, **extra) -> dict:
        x, y = normalize(d["x"], d["y"])
        return {**{k: v for k, v in d.items() if k not in ("x", "y")}, "x": x, "y": y, **extra}

    return {
        "structures": [placed(s, down=down[s["id"]]) for s in STRUCTURES],
        "pits": [placed(p) for p in PITS],
        "camps": [placed(c) for c in CAMPS],
    }


def describe(
    e: dict, players: dict[int, dict], items: dict[int, str] | None = None
) -> tuple[str, int | None]:
    """Human-readable label for an event, and the team that acted (if any).

    `items` maps item IDs to names (Data Dragon, per patch); unknown IDs show as "item N".
    """
    items = items or {}

    def item(iid: int | None) -> str:
        return items.get(iid) or f"item {iid}"

    def name(pid: int | None) -> str:
        return players[pid]["champion"] if pid in players else "Minions/turret"

    def team(pid: int | None) -> int | None:
        return players[pid]["team"] if pid in players else None

    t = e["type"]

    if t == "CHAMPION_KILL":
        killer, victim = e.get("killerId"), e.get("victimId")
        assists = e.get("assistingParticipantIds") or []
        if killer in players:
            extra = f" (+{len(assists)} assist{'s' if len(assists) != 1 else ''})" if assists else ""
            return f"{name(killer)} killed {name(victim)}{extra}", team(killer)
        vt = team(victim)
        return f"{name(victim)} was executed", _other(vt) if vt else None

    if t == "CHAMPION_SPECIAL_KILL":
        killer, kind = e.get("killerId"), e.get("killType")
        if kind == "KILL_FIRST_BLOOD":
            return f"First blood: {name(killer)}", team(killer)
        if kind == "KILL_MULTI":
            return f"{name(killer)}: {MULTI.get(e.get('multiKillLength'), 'multikill')}", team(killer)
        if kind == "KILL_ACE":
            return f"Ace by {TEAM_NAME.get(team(killer), '?')}", team(killer)
        return f"{name(killer)}: {_title(kind or 'special kill')}", team(killer)

    if t == "ELITE_MONSTER_KILL":
        sub, typ = e.get("monsterSubType"), e.get("monsterType", "")
        monster = _title(sub) if sub else MONSTER_NAMES.get(typ, _title(typ))
        tm = e.get("killerTeamId")
        who = f" ({name(e.get('killerId'))})" if e.get("killerId") in players else ""
        return f"{TEAM_NAME.get(tm, '?')} took {monster}{who}", tm

    if t == "DRAGON_SOUL_GIVEN":
        tm = e.get("teamId")
        if tm not in TEAM_NAME:  # teamId 0: fires after the 2nd dragon to reveal the soul type
            return f"Dragon soul revealed: {_title(e.get('name', ''))}", None
        return f"{TEAM_NAME[tm]} secured {_title(e.get('name', ''))} Soul", tm

    if t == "BUILDING_KILL":
        owner = e.get("teamId")
        destroyer = _other(owner) if owner in TEAM_NAME else None
        lane = (e.get("laneType") or "").split("_")[0].lower()
        if e.get("buildingType") == "INHIBITOR_BUILDING":
            what = f"{lane} inhibitor"
        elif e.get("towerType") == "NEXUS_TURRET":
            what = "nexus turret"
        else:
            what = f"{lane} {_title(e.get('towerType') or 'turret').lower()}"
        return f"{TEAM_NAME.get(destroyer, '?')} destroyed {TEAM_NAME.get(owner, '?')}'s {what}", destroyer

    if t == "TURRET_PLATE_DESTROYED":
        owner = e.get("teamId")
        taker = _other(owner) if owner in TEAM_NAME else None
        lane = (e.get("laneType") or "").split("_")[0].lower()
        by = " (minions)" if e.get("killerId") == 0 else ""
        return f"{TEAM_NAME.get(taker, '?')} took a plate from {TEAM_NAME.get(owner, '?')} {lane}{by}", taker

    if t == "WARD_PLACED":
        c = e.get("creatorId")
        return f"{name(c)} placed {WARD_NAMES.get(e.get('wardType'), 'a ward')}", team(c)

    if t == "WARD_KILL":
        k = e.get("killerId")
        return f"{name(k)} cleared {WARD_NAMES.get(e.get('wardType'), 'a ward')}", team(k)

    if t == "ITEM_PURCHASED":
        p = e.get("participantId")
        return f"{name(p)} bought {item(e.get('itemId'))}", team(p)

    if t == "ITEM_SOLD":
        p = e.get("participantId")
        return f"{name(p)} sold {item(e.get('itemId'))}", team(p)

    if t == "ITEM_DESTROYED":
        # Fires for consumables used up AND for components absorbed into a finished item.
        p = e.get("participantId")
        return f"{name(p)}'s {item(e.get('itemId'))} was used up or combined", team(p)

    if t == "ITEM_UNDO":
        p, before, after = e.get("participantId"), e.get("beforeId"), e.get("afterId")
        if before:
            return f"{name(p)} undid buying {item(before)}", team(p)
        if after:
            return f"{name(p)} undid selling {item(after)}", team(p)
        return f"{name(p)} undid an item action", team(p)

    if t == "LEVEL_UP":
        p = e.get("participantId")
        return f"{name(p)} reached level {e.get('level')}", team(p)

    if t == "SKILL_LEVEL_UP":
        p = e.get("participantId")
        return f"{name(p)} leveled {SKILL_KEYS.get(e.get('skillSlot'), 'a skill')}", team(p)

    if t == "GAME_END":
        tm = e.get("winningTeam")
        return f"{TEAM_NAME.get(tm, '?')} won", tm

    return _title(t), None


def build_replay_data(
    match: dict,
    timeline: dict,
    winprob: list[float] | None = None,
    item_names: dict[int, str] | None = None,
    moments: list[dict] | None = None,
    moments_note: str = "",
) -> dict:
    """Compact, render-ready view of one game.

    `winprob`, if given, is P(blue wins) per frame — the Phase 5 model's output, drawn as
    its own chart under the gold/XP chart. `item_names` (from `riot.ddragon.item_names`) turns item IDs into names.
    """
    info = match["info"]
    players: dict[int, dict] = {}
    for p in info["participants"]:
        pid = p["participantId"]
        name = p.get("riotIdGameName") or p.get("summonerName") or ""
        tag = p.get("riotIdTagline")
        players[pid] = {
            "id": pid,
            "champion": p.get("championName", f"P{pid}"),
            "team": p.get("teamId"),
            "role": p.get("teamPosition") or "?",
            "name": f"{name}#{tag}" if name and tag else name,
            "win": bool(p.get("win")),
            "kda": [p.get("kills", 0), p.get("deaths", 0), p.get("assists", 0)],
        }

    kda = {pid: [0, 0, 0] for pid in players}
    frames, events, gold_diff, xp_diff, raw_events = [], [], [], [], []

    # First blood / multikill / ace events are recorded ~350 units (median) away from the
    # kill they belong to, so the "!" is anchored to its kill instead of its own position.
    # Every special kill in the corpus matches a kill by the same killer within 50 ms.
    kills_by_killer: dict[int, list[tuple[int, dict]]] = {}
    for fr in timeline["info"]["frames"]:
        for e in fr.get("events", []):
            if e["type"] == "CHAMPION_KILL" and "position" in e:
                kills_by_killer.setdefault(e.get("killerId"), []).append((e["timestamp"], e["position"]))

    def kill_position(e: dict) -> dict | None:
        near = [(abs(t - e["timestamp"]), p) for t, p in kills_by_killer.get(e.get("killerId"), [])]
        near = [x for x in near if x[0] <= 50]
        return min(near, key=lambda x: x[0])[1] if near else None

    for i, fr in enumerate(timeline["info"]["frames"]):
        died: list[int] = []
        for e in fr.get("events", []):
            raw_events.append(e)
            if e["type"] == "CHAMPION_KILL":
                k, v = e.get("killerId"), e.get("victimId")
                if k in kda:
                    kda[k][0] += 1
                if v in kda:
                    kda[v][1] += 1
                    died.append(v)
                for a in e.get("assistingParticipantIds") or []:
                    if a in kda:
                        kda[a][2] += 1

            label, acting = describe(e, players, item_names)
            ev: dict[str, Any] = {
                "t": e["timestamp"],
                "f": i,
                "type": e["type"],
                "cat": category(e),
                "label": label,
                "team": acting,
            }
            # Kill markers on the map are coloured by the team of the player who died.
            if e["type"] == "CHAMPION_KILL" and e.get("victimId") in players:
                ev["victimTeam"] = players[e["victimId"]]["team"]
            elif e["type"] == "CHAMPION_SPECIAL_KILL" and e.get("killerId") in players:
                ev["victimTeam"] = _other(players[e["killerId"]]["team"])

            if e["type"] == "TURRET_PLATE_DESTROYED":
                # Plate events aren't recorded at their turret (positions scatter by
                # thousands of units), so draw them on the outer turret they came off.
                lane = (e.get("laneType") or "").split("_")[0].lower()
                turret = _STRUCTURE_BY_ID.get(f"{e.get('teamId')}_{lane}_outer")
                if turret:
                    ev["x"], ev["y"] = normalize(turret["x"], turret["y"])
            elif e["type"] == "CHAMPION_SPECIAL_KILL" and kill_position(e):
                ev["x"], ev["y"] = _norm(kill_position(e))
            elif "position" in e:
                ev["x"], ev["y"] = _norm(e["position"])
            events.append(ev)

        snap: dict[str, dict] = {}
        team_gold = {100: 0, 200: 0}
        team_xp = {100: 0, 200: 0}
        for key, pf in fr["participantFrames"].items():
            pid = int(key)
            stats = pf.get("championStats", {})
            x, y = _norm(pf["position"])
            snap[key] = {
                "x": x,
                "y": y,
                "gold": pf.get("totalGold", 0),
                "xp": pf.get("xp", 0),
                "lvl": pf.get("level", 1),
                "cs": pf.get("minionsKilled", 0) + pf.get("jungleMinionsKilled", 0),
                "hp": stats.get("health"),
                "hpMax": stats.get("healthMax"),
                "k": kda.get(pid, [0, 0, 0])[0],
                "d": kda.get(pid, [0, 0, 0])[1],
                "a": kda.get(pid, [0, 0, 0])[2],
            }
            tm = players.get(pid, {}).get("team")
            if tm in team_gold:
                team_gold[tm] += pf.get("totalGold", 0)
                team_xp[tm] += pf.get("xp", 0)

        frames.append({"t": fr["timestamp"], "p": snap, "died": died})
        gold_diff.append(team_gold[100] - team_gold[200])
        xp_diff.append(team_xp[100] - team_xp[200])

    winner = next((t["teamId"] for t in info.get("teams", []) if t.get("win")), None)
    ordered = sorted(
        players.values(),
        key=lambda p: (p["team"], ROLE_ORDER.index(p["role"]) if p["role"] in ROLE_ORDER else 9),
    )

    return {
        "matchId": match["metadata"]["matchId"],
        "patch": ".".join(info.get("gameVersion", "").split(".")[:2]),
        "durationS": info.get("gameDuration"),
        "queueId": info.get("queueId"),
        "queueName": queue_name(info.get("queueId")),
        "ranked": info.get("queueId") == RANKED_SOLO,   # the model is only validated on these
        "created": info.get("gameCreation"),
        "winner": winner,
        "participants": ordered,
        "frames": frames,
        "events": events,
        "curves": {"gold": gold_diff, "xp": xp_diff},
        "winprob": winprob,
        # Phase 6: [{t0, t1, frame, rank, mine, direction, heading, detail, title}], from
        # lolreview.review.moments via moments_markers(); drawn on the win-prob chart
        "moments": moments or [],
        "momentsNote": moments_note,
        **_landmarks(raw_events),
    }


def fetch_map_image(dest_dir: Path) -> bytes | None:
    """Summoner's Rift minimap from Data Dragon, cached locally. None if unavailable."""
    from ..riot.ddragon import map_image

    return map_image(dest_dir)


def render_html(data: dict, map_png: bytes | None = None) -> str:
    """Self-contained HTML: no network, no dependencies, opens in any browser."""
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    map_uri = (
        json.dumps("data:image/png;base64," + base64.b64encode(map_png).decode())
        if map_png
        else "null"
    )
    title = f"{data['matchId']} · patch {data['patch']}"
    return (
        _TEMPLATE.replace("__TITLE__", title)
        .replace("__MAP__", map_uri)
        .replace("__DATA__", payload)
    )


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Replay · __TITLE__</title>
<style>
  :root { --bg:#0f1115; --panel:#161a21; --line:#262a33; --text:#e5e7eb; --muted:#9ca3af;
          --blue:#3b82f6; --red:#ef4444; }
  * { box-sizing:border-box }
  body { margin:0; background:var(--bg); color:var(--text);
         font:13px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
  header { padding:10px 16px; border-bottom:1px solid var(--line); display:flex; gap:14px;
           align-items:baseline; flex-wrap:wrap }
  header h1 { font-size:15px; margin:0; font-weight:600 }
  .muted { color:var(--muted) }
  main { display:grid; grid-template-columns:536px minmax(380px, 1fr); gap:14px; padding:12px 16px }
  section { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:10px }
  h2 { font-size:12px; font-weight:600; margin:0 0 6px; color:var(--muted);
       text-transform:uppercase; letter-spacing:.04em }
  svg { display:block }
  #map { width:512px; height:512px; border-radius:4px; background:#18241b }
  .controls { display:flex; align-items:center; gap:8px; margin-top:8px }
  .controls input[type=range] { flex:1 }
  button { background:#262a33; color:var(--text); border:1px solid #374151; border-radius:4px;
           padding:3px 10px; cursor:pointer; font:inherit }
  button:hover { background:#2f3542 }
  #clock { font-variant-numeric:tabular-nums; min-width:92px }
  #chart { width:512px; height:170px; margin-top:10px; cursor:pointer }
  #wpchart { width:512px; height:150px; margin-top:6px; cursor:pointer; display:block }
  #wpchart[hidden] { display:none }
  #moments-sec[hidden] { display:none }
  .mo { padding:4px 6px 4px 8px; border-radius:3px; cursor:pointer; border-left:3px solid var(--line); margin:2px 0 }
  .mo:hover { background:#1d2430 }
  .mo.good { border-left-color:#22c55e } .mo.bad { border-left-color:#ef4444 } .mo.ctx { border-left-color:#6b7280; opacity:.85 }
  .mo .h { font-weight:600 } .mo .s { color:var(--muted); font-size:11px; white-space:pre-line }
  .opts { margin-top:6px; color:var(--muted) }
  .opts label, .filters label { margin-right:12px; white-space:nowrap; cursor:pointer }
  #events { position:relative; height:300px; overflow:auto; margin-top:6px;
            font-variant-numeric:tabular-nums }
  .ev { padding:2px 6px 2px 8px; border-radius:3px; cursor:pointer; display:flex; gap:8px;
        border-left:3px solid transparent }
  .ev:hover { background:#20252e }
  .ev.now { background:#1d2430 }
  .ev.sel { outline:1px solid #fbbf24 }
  .ev .t { color:var(--muted); min-width:38px }
  .bang { color:#facc15; font-weight:800; margin-right:6px }
  .ev.b { border-left-color:var(--blue) } .ev.r { border-left-color:var(--red) }
  table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums }
  th, td { padding:3px 6px; text-align:right; border-bottom:1px solid var(--line) }
  th { color:var(--muted); font-weight:500 }
  td.l, th.l { text-align:left }
  tr.b td:first-child { box-shadow:inset 3px 0 var(--blue) }
  tr.r td:first-child { box-shadow:inset 3px 0 var(--red) }
  tr.died td { opacity:.6 }
  .dead { color:var(--muted); font-size:11px; margin-left:5px; border:1px solid #374151;
          border-radius:3px; padding:0 4px }
  .key { display:grid; grid-template-columns:1fr 1fr; gap:3px 14px }
  .key h3 { grid-column:1 / -1; margin:6px 0 1px; font-size:11px; font-weight:600; color:var(--muted) }
  .key h3:first-child { margin-top:0 }
  .key .row { display:flex; align-items:center; gap:7px; min-height:22px }
  .key svg { flex:none }
  .key .foot { grid-column:1 / -1; color:var(--muted); font-size:11px; margin-top:6px }
  @media (max-width: 1000px) { main { grid-template-columns:1fr } }
  .hp { display:inline-block; width:46px; height:6px; background:#374151; border-radius:3px;
        vertical-align:middle; margin-right:5px; overflow:hidden }
  .hp i { display:block; height:100% }
  .right { display:flex; flex-direction:column; gap:14px }
  .note { color:var(--muted); font-size:12px; margin-top:6px }
</style></head>
<body>
<header>
  <h1 id="title"></h1><span id="meta" class="muted"></span>
</header>
<main>
  <div>
    <section>
      <svg id="map" viewBox="0 0 512 512"></svg>
      <div class="controls">
        <button id="prev" title="Previous minute (←)">◀</button>
        <button id="play" title="Play / pause (space)">▶</button>
        <button id="next" title="Next minute (→)">▶|</button>
        <input id="slider" type="range" min="0" value="0" step="1">
        <span id="clock"></span>
      </div>
      <div class="opts">
        <label><input type="checkbox" id="trails" checked> movement since last minute</label>
        <label><input type="checkbox" id="names"> champion names</label>
        <label><input type="checkbox" id="landmarks" checked> towers, pits &amp; camps</label>
      </div>
      <div class="note">Positions are one snapshot per minute. Markers show events since the
        previous snapshot; dashed lines connect each player's last two snapshots (not their path).</div>
      <svg id="chart" viewBox="0 0 512 170"></svg>
      <svg id="wpchart" viewBox="0 0 512 150" hidden></svg>
    </section>
  </div>
  <div class="right">
    <section id="moments-sec" hidden>
      <h2>Moments to review</h2>
      <div class="note" id="moments-note"></div>
      <div id="moments"></div>
    </section>
    <section>
      <h2>Key</h2>
      <div class="key" id="key"></div>
    </section>
    <section>
      <h2>Players at this minute</h2>
      <table id="players"></table>
    </section>
    <section>
      <h2>Events</h2>
      <div class="filters" id="filters"></div>
      <div id="events"></div>
    </section>
  </div>
</main>
<script>
const D = __DATA__;
const MAP = __MAP__;
const W = 512, H = 512;
const TEAM = { 100: { name: "Blue", color: "#3b82f6", cls: "b" }, 200: { name: "Red", color: "#ef4444", cls: "r" } };
const ROLE_LETTER = { TOP: "T", JUNGLE: "J", MIDDLE: "M", BOTTOM: "B", UTILITY: "S" };
const CATS = [["kill","Kills",true],["special","Special",true],["plate","Plates",true],
              ["ward","Wards",false],["item","Items",false],["level","Levels",false],["other","Other",false]];
const NS = "http://www.w3.org/2000/svg";
const P = Object.fromEntries(D.participants.map(p => [p.id, p]));
const LAST_T = D.frames[D.frames.length - 1].t || 1;

const svg = (tag, attrs, parent) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
};
const px = x => x * W, py = y => (1 - y) * H;
const mmss = ms => { const s = Math.round(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const fmtK = v => (v >= 0 ? "+" : "−") + (Math.abs(v) / 1000).toFixed(1) + "k";

// ---- header ------------------------------------------------------------------------
document.getElementById("title").textContent = `${D.matchId}`;
const created = D.created ? new Date(D.created).toLocaleString() : "";
document.getElementById("meta").textContent =
  `${D.queueName || ""} · patch ${D.patch} · ${mmss((D.durationS || 0) * 1000)} · ${TEAM[D.winner] ? TEAM[D.winner].name + " won" : ""} · ${created}`;

// ---- map background ----------------------------------------------------------------
const map = document.getElementById("map");
if (MAP) {
  svg("image", { href: MAP, x: 0, y: 0, width: W, height: H }, map);
} else {
  const g = svg("g", { stroke: "#3f5a45", "stroke-width": 10, fill: "none", "stroke-linecap": "round" }, map);
  const line = pts => svg("polyline", { points: pts.map(([x, y]) => `${px(x)},${py(y)}`).join(" ") }, g);
  line([[0.08, 0.14], [0.08, 0.92], [0.86, 0.92]]);               // top lane
  line([[0.12, 0.12], [0.88, 0.88]]);                              // mid lane
  line([[0.14, 0.08], [0.92, 0.08], [0.92, 0.86]]);               // bot lane
  svg("polyline", { points: `${px(0.12)},${py(0.88)} ${px(0.88)},${py(0.12)}`,
                    stroke: "#2d4a63", "stroke-width": 16, fill: "none", "stroke-linecap": "round" }, map); // river
  svg("circle", { cx: px(0.07), cy: py(0.07), r: 30, fill: "#1e3a5f" }, map);
  svg("circle", { cx: px(0.93), cy: py(0.93), r: 30, fill: "#5f1e1e" }, map);
}
const landmarkLayer = svg("g", {}, map);
const trailLayer = svg("g", {}, map), evLayer = svg("g", {}, map), playerLayer = svg("g", {}, map);

// ---- state -------------------------------------------------------------------------
let frame = 0, timer = null, selected = null;
const slider = document.getElementById("slider");
slider.max = D.frames.length - 1;
const enabled = new Set(CATS.filter(c => c[2]).map(c => c[0]));
const windowOf = f => [f > 0 ? D.frames[f - 1].t : -1, D.frames[f].t];

// ---- map drawing -------------------------------------------------------------------
// ---- shapes: each draws at the origin of `g`, shared by the map and the key --------------
const GRAY = "#9ca3af";
const PIT_COLOR = { dragon: "#f59e0b", baron: "#a855f7" };
const CAMP_STYLE = {
  blue_buff: { r: 5, fill: "#67e8f9" }, red_buff: { r: 5, fill: "#fb7185" },
  scuttle: { r: 4, fill: "none", stroke: "#5eead4" },
};
const shape = {
  player(g, color, letter, died) {
    svg("circle", { r: 9.5, fill: color, stroke: died ? "#fca5a5" : "#0b0d10", "stroke-width": died ? 2.5 : 1.5,
                    "fill-opacity": died ? 0.55 : 1 }, g);
    const t = svg("text", { "text-anchor": "middle", dy: "0.35em", "font-size": 10, "font-weight": 700, fill: "#fff" }, g);
    t.textContent = letter;
  },
  kill(g, color) {
    svg("path", { d: "M-6,-6L6,6M6,-6L-6,6", stroke: "#111", "stroke-width": 5, "stroke-linecap": "round" }, g);
    svg("path", { d: "M-6,-6L6,6M6,-6L-6,6", stroke: color, "stroke-width": 3, "stroke-linecap": "round" }, g);
  },
  special(g, color) {
    // Yellow "!" drawn from shapes (no font dependence), in a ring of the acting team's colour.
    svg("circle", { r: 8.5, fill: "#111827", stroke: color, "stroke-width": 2.5 }, g);
    svg("rect", { x: -1.7, y: -5.6, width: 3.4, height: 7.2, rx: 1.3, fill: "#facc15" }, g);
    svg("circle", { cx: 0, cy: 4.4, r: 1.8, fill: "#facc15" }, g);
  },
  structureEvent(g, color) {
    svg("rect", { x: -6, y: -6, width: 12, height: 12, fill: "#111", stroke: color, "stroke-width": 2.5 }, g);
  },
  turret(g, color, alive) {
    svg("polygon", alive
      ? { points: "0,-7 6.5,5 -6.5,5", fill: color, stroke: "#0b0d10", "stroke-width": 1.5 }
      : { points: "0,-7 6.5,5 -6.5,5", fill: "none", stroke: GRAY, "stroke-width": 1.5, "stroke-dasharray": "2.5 1.5" }, g);
  },
  inhibitor(g, color, alive) {
    const pts = [...Array(6)].map((_, i) => { const a = Math.PI / 3 * i + Math.PI / 6; return `${6.5 * Math.cos(a)},${6.5 * Math.sin(a)}`; }).join(" ");
    svg("polygon", alive
      ? { points: pts, fill: color, stroke: "#0b0d10", "stroke-width": 1.5 }
      : { points: pts, fill: "none", stroke: GRAY, "stroke-width": 1.5, "stroke-dasharray": "2.5 1.5" }, g);
  },
  pit(g, id, r) {
    svg("circle", { r: r || 17, fill: PIT_COLOR[id], "fill-opacity": 0.12, stroke: PIT_COLOR[id],
                    "stroke-width": 2, "stroke-dasharray": "4 3" }, g);
  },
  camp(g, kind) {
    const s = CAMP_STYLE[kind] || { r: 3, fill: "#d1d5db" };
    svg("circle", { r: s.r, fill: s.fill, stroke: s.stroke || "#0b0d10", "stroke-width": s.stroke ? 1.8 : 1 }, g);
  },
};

function marker(e, parent) {
  const team = e.cat === "kill" ? e.victimTeam : e.team;  // kills: team of the player who died
  const color = team ? TEAM[team].color : GRAY;
  // First blood / multikill / ace sit on top of the kill they belong to: nudge the "!"
  // up-right so the death marker underneath stays visible.
  const nudge = e.cat === "special" && e.type === "CHAMPION_SPECIAL_KILL" ? [10, -10] : [0, 0];
  const g = svg("g", { transform: `translate(${px(e.x) + nudge[0]},${py(e.y) + nudge[1]})` }, parent);
  if (e.cat === "kill") shape.kill(g, color);
  else if (e.cat === "special") shape.special(g, color);
  else shape.structureEvent(g, color);
  if (selected === e) svg("circle", { r: 15, fill: "none", stroke: "#fbbf24", "stroke-width": 2 }, g);
  svg("title", {}, g).textContent = `${mmss(e.t)}  ${e.label}`;
}

function drawLandmarks(t) {
  landmarkLayer.replaceChildren();
  if (!document.getElementById("landmarks").checked) return;
  const at = (d) => svg("g", { transform: `translate(${px(d.x)},${py(d.y)})` }, landmarkLayer);
  for (const p of D.pits) {
    const g = at(p); shape.pit(g, p.id);
    svg("title", {}, g).textContent = p.name;
  }
  for (const c of D.camps) {
    const g = at(c); shape.camp(g, c.kind);
    svg("title", {}, g).textContent = c.name + (c.side ? ` (${TEAM[c.side].name} side)` : "") + " — approximate position";
  }
  for (const s of D.structures) {
    const iv = s.down.find(([a, b]) => t >= a && (b == null || t < b));
    const g = at(s); shape[s.kind](g, TEAM[s.team].color, !iv);
    const what = s.tier === "nexus" ? "nexus turret" : `${s.lane} ${s.kind === "inhibitor" ? "inhibitor" : s.tier + " turret"}`;
    const state = !iv ? "standing" : iv[1] == null ? `destroyed at ${mmss(iv[0])}` : `down ${mmss(iv[0])}–${mmss(iv[1])} (respawns)`;
    svg("title", {}, g).textContent = `${TEAM[s.team].name} ${what} — ${state}`;
  }
}

function buildKey() {
  const box = document.getElementById("key");
  const head = text => { const h = document.createElement("h3"); h.textContent = text; box.appendChild(h); };
  const row = (draw, label) => {
    const r = document.createElement("div"); r.className = "row";
    const s = svg("svg", { width: 26, height: 22, viewBox: "-13 -11 26 22" });
    draw(svg("g", {}, s)); r.appendChild(s);
    const span = document.createElement("span"); span.textContent = label; r.appendChild(span);
    box.appendChild(r);
  };
  head("Players (letter = role: T J M B S)");
  row(g => shape.player(g, TEAM[100].color, "M"), "Blue player");
  row(g => shape.player(g, TEAM[200].color, "M"), "Red player");
  row(g => shape.player(g, TEAM[100].color, "J", true), "Died since last snapshot");
  row(g => svg("line", { x1: -11, y1: 6, x2: 11, y2: -6, stroke: TEAM[100].color, "stroke-opacity": 0.7,
                         "stroke-width": 2, "stroke-dasharray": "3 3" }, g), "Moved since last snapshot");
  head("Events since last snapshot");
  row(g => shape.kill(g, TEAM[100].color), "Blue player died");
  row(g => shape.kill(g, TEAM[200].color), "Red player died");
  row(g => shape.special(g, TEAM[100].color), "Special event (ring = team)");
  row(g => shape.structureEvent(g, TEAM[100].color), "Turret plate taken (border = team)");
  const note = document.createElement("div"); note.className = "foot"; note.style.marginTop = "0";
  note.textContent = "Special = first blood, triple/quadra/penta kill, ace, Dragon, Elder, Baron, " +
                     "Herald, Voidgrubs, dragon soul, turret or inhibitor destroyed.";
  box.appendChild(note);
  head("Map");
  row(g => shape.turret(g, TEAM[100].color, true), "Turret");
  row(g => shape.turret(g, null, false), "Turret destroyed");
  row(g => shape.inhibitor(g, TEAM[200].color, true), "Inhibitor");
  row(g => shape.inhibitor(g, null, false), "Inhibitor down (5:00 respawn)");
  row(g => shape.pit(g, "dragon", 8), "Dragon pit");
  row(g => shape.pit(g, "baron", 8), "Baron pit (Voidgrubs, Herald)");
  row(g => shape.camp(g, "blue_buff"), "Blue buff");
  row(g => shape.camp(g, "red_buff"), "Red buff");
  row(g => shape.camp(g, "gromp"), "Other camps");
  row(g => shape.camp(g, "scuttle"), "Scuttle Crab spawn");
  const foot = document.createElement("div"); foot.className = "foot";
  foot.textContent = "Turrets, inhibitors and pits are exact (measured from 5,000 games). " +
                     "Camp positions are approximate (±~300 units). Hover any symbol for details.";
  box.appendChild(foot);
}

function drawMap() {
  trailLayer.replaceChildren(); evLayer.replaceChildren(); playerLayer.replaceChildren();
  const fr = D.frames[frame], prev = frame > 0 ? D.frames[frame - 1] : null;
  const [t0, t1] = windowOf(frame);
  const showTrails = document.getElementById("trails").checked;
  const showNames = document.getElementById("names").checked;
  drawLandmarks(t1);

  for (const e of D.events) {
    if (e.t <= t0 || e.t > t1 || e.x == null || !enabled.has(e.cat)) continue;
    marker(e, evLayer);
  }
  if (selected && selected.x != null && !(selected.t > t0 && selected.t <= t1)) marker(selected, evLayer);

  for (const p of D.participants) {
    const s = fr.p[p.id]; if (!s) continue;
    const color = TEAM[p.team].color;
    if (showTrails && prev && prev.p[p.id]) {
      const q = prev.p[p.id];
      svg("line", { x1: px(q.x), y1: py(q.y), x2: px(s.x), y2: py(s.y), stroke: color,
                    "stroke-opacity": 0.55, "stroke-width": 2, "stroke-dasharray": "3 3" }, trailLayer);
    }
    const died = fr.died.includes(p.id);
    const g = svg("g", { transform: `translate(${px(s.x)},${py(s.y)})` }, playerLayer);
    shape.player(g, color, ROLE_LETTER[p.role] || "?", died);
    if (showNames) {
      const n = svg("text", { "text-anchor": "middle", y: -13, "font-size": 10, fill: "#fff",
                              stroke: "#000", "stroke-width": 3, "paint-order": "stroke" }, g);
      n.textContent = p.champion;
    }
    const hp = s.hp != null && s.hpMax ? ` · HP ${Math.round(100 * s.hp / s.hpMax)}%` : "";
    svg("title", {}, g).textContent =
      `${p.champion} (${TEAM[p.team].name} ${p.role})${p.name ? "\n" + p.name : ""}\n` +
      `Lv ${s.lvl} · ${s.gold.toLocaleString()}g · ${s.cs} CS · ${s.k}/${s.d}/${s.a}${hp}` +
      (died ? "\nDied since last snapshot" : "");
  }
}

// ---- chart -------------------------------------------------------------------------
const chart = document.getElementById("chart");
const CW = 512, CH = 170, PL = 44, PR = 10, PT = 12, PB = 22;
const cx = t => PL + (t / LAST_T) * (CW - PL - PR);
const span = Math.max(1000, ...D.curves.gold.map(Math.abs), ...D.curves.xp.map(Math.abs));
const M = Math.ceil(span / 1000) * 1000;
const cy = v => PT + (1 - (v + M) / (2 * M)) * (CH - PT - PB);
let cursor;

function drawChart() {
  chart.replaceChildren();
  svg("rect", { x: PL, y: PT, width: CW - PL - PR, height: CH - PT - PB, fill: "#12161c" }, chart);
  for (const v of [M, M / 2, 0, -M / 2, -M]) {
    svg("line", { x1: PL, x2: CW - PR, y1: cy(v), y2: cy(v), stroke: v === 0 ? "#4b5563" : "#232833" }, chart);
    const lab = svg("text", { x: PL - 6, y: cy(v), dy: "0.35em", "text-anchor": "end", "font-size": 10, fill: "#9ca3af" }, chart);
    lab.textContent = v === 0 ? "0" : fmtK(v);
  }
  const pts = D.frames.map((f, i) => [cx(f.t), cy(D.curves.gold[i])]);
  const area = (clampFn, color) => {
    const top = D.frames.map((f, i) => `${cx(f.t)},${cy(clampFn(D.curves.gold[i]))}`).join(" ");
    svg("polygon", { points: `${cx(0)},${cy(0)} ${top} ${cx(LAST_T)},${cy(0)}`, fill: color, "fill-opacity": 0.22 }, chart);
  };
  area(v => Math.max(v, 0), "#3b82f6");
  area(v => Math.min(v, 0), "#ef4444");
  svg("polyline", { points: pts.map(p => p.join(",")).join(" "), fill: "none", stroke: "#e5e7eb", "stroke-width": 1.8 }, chart);
  svg("polyline", { points: D.frames.map((f, i) => `${cx(f.t)},${cy(D.curves.xp[i])}`).join(" "),
                    fill: "none", stroke: "#a78bfa", "stroke-width": 1.4, "stroke-dasharray": "4 3" }, chart);
  for (const e of D.events) {
    if (e.cat !== "kill" && e.cat !== "special") continue;
    if (e.type === "CHAMPION_SPECIAL_KILL") continue;  // already ticked as the kill itself
    const special = e.cat === "special";
    svg("line", { x1: cx(e.t), x2: cx(e.t), y1: CH - PB + 2, y2: CH - PB + (special ? 10 : 6),
                  stroke: special ? "#facc15" : (e.team ? TEAM[e.team].color : GRAY),
                  "stroke-width": special ? 2 : 1 }, chart);
  }
  const legend = svg("text", { x: PL, y: CH - 2, "font-size": 10, fill: "#9ca3af" }, chart);
  legend.textContent = "gold diff (solid) · XP diff (dashed) · above 0 = Blue ahead · ticks: kills, special (yellow)";
  cursor = svg("line", { y1: PT, y2: CH - PB, stroke: "#fbbf24", "stroke-width": 1.5 }, chart);
}

// ---- win-probability chart (Phase 5 model; only when the replay has it) -------------
const wpchart = document.getElementById("wpchart");
const WH = 150, WT = 12, WB = 22;
const wy = p => WT + (1 - p) * (WH - WT - WB);
let wpCursor, wpDot, wpLabel;

function drawWinProb() {
  if (!D.winprob) return;
  wpchart.removeAttribute("hidden");   // .hidden = false doesn't work on SVG elements
  wpchart.replaceChildren();
  const ok = D.frames.map((f, i) => D.winprob[i] != null);
  const pts = D.frames.map((f, i) => [cx(f.t), wy(ok[i] ? D.winprob[i] : 0.5)]);
  svg("rect", { x: PL, y: WT, width: CW - PL - PR, height: WH - WT - WB, fill: "#12161c" }, wpchart);
  for (const v of [1, 0.75, 0.5, 0.25, 0]) {
    svg("line", { x1: PL, x2: CW - PR, y1: wy(v), y2: wy(v), stroke: v === 0.5 ? "#4b5563" : "#232833" }, wpchart);
    const lab = svg("text", { x: PL - 6, y: wy(v), dy: "0.35em", "text-anchor": "end", "font-size": 10, fill: "#9ca3af" }, wpchart);
    lab.textContent = `${Math.round(v * 100)}%`;
  }
  // shade Blue-favoured (above 50%) blue and Red-favoured red, like the gold chart
  const area = (clampFn, color) => {
    const top = pts.map(([x, y]) => `${x},${clampFn(y)}`).join(" ");
    svg("polygon", { points: `${cx(0)},${wy(0.5)} ${top} ${cx(LAST_T)},${wy(0.5)}`, fill: color, "fill-opacity": 0.22 }, wpchart);
  };
  area(y => Math.min(y, wy(0.5)), "#3b82f6");
  area(y => Math.max(y, wy(0.5)), "#ef4444");
  svg("polyline", { points: pts.map(p => p.join(",")).join(" "), fill: "none", stroke: "#e5e7eb", "stroke-width": 1.8 }, wpchart);
  const legend = svg("text", { x: PL, y: WH - 4, "font-size": 10, fill: D.ranked === false ? "#f59e0b" : "#9ca3af" }, wpchart);
  legend.textContent = D.ranked === false
    ? `⚠ ${D.queueName}: model trained on ranked solo/duo only, so treat this curve as rough`
    : "Blue win probability · above 50% = Blue favoured · model estimate, not a verdict";
  // Phase 6 moments: shaded bands (green = good for the reviewed player's team, red = bad,
  // grey = game swing you weren't part of), numbered by rank, click to jump there.
  for (const m of (D.moments || [])) {
    const x0 = cx(m.t0), x1 = Math.max(cx(m.t1), x0 + 3);
    const col = !m.mine ? "#6b7280" : m.direction === "good" ? "#22c55e" : "#ef4444";
    const band = svg("rect", { x: x0, y: WT, width: x1 - x0, height: WH - WT - WB, fill: col,
                               "fill-opacity": m.mine ? 0.22 : 0.14, style: "cursor:pointer" }, wpchart);
    const tip = svg("title", {}, band); tip.textContent = m.title;
    band.addEventListener("click", ev => { ev.stopPropagation(); setFrame(m.frame); });
    const lab = svg("text", { x: (x0 + x1) / 2, y: WT + 10, "text-anchor": "middle", "font-size": 10,
                              "font-weight": 700, fill: col }, wpchart);
    lab.textContent = m.mine ? String(m.rank) : "c";
  }
  wpCursor = svg("line", { y1: WT, y2: WH - WB, stroke: "#fbbf24", "stroke-width": 1.5 }, wpchart);
  wpDot = svg("circle", { r: 3.5, fill: "#fbbf24" }, wpchart);
  wpLabel = svg("text", { "font-size": 11, fill: "#fbbf24", "font-weight": 600 }, wpchart);
}

function markWinProb(t1) {
  if (!D.winprob || !wpCursor) return;
  wpCursor.setAttribute("x1", cx(t1)); wpCursor.setAttribute("x2", cx(t1));
  const p = D.winprob[frame];
  wpDot.setAttribute("visibility", p == null ? "hidden" : "visible");
  wpLabel.setAttribute("visibility", p == null ? "hidden" : "visible");
  if (p == null) return;
  const x = cx(t1), y = wy(p), right = x > CW - 120;
  wpDot.setAttribute("cx", x); wpDot.setAttribute("cy", y);
  wpLabel.setAttribute("x", right ? x - 6 : x + 6);
  wpLabel.setAttribute("text-anchor", right ? "end" : "start");
  wpLabel.setAttribute("y", Math.max(WT + 10, Math.min(WH - WB - 4, y - 6)));
  wpLabel.textContent = `Blue ${Math.round(100 * p)}% · Red ${100 - Math.round(100 * p)}%`;
}

for (const el of [chart, wpchart]) {
  el.addEventListener("click", ev => {
    const r = el.getBoundingClientRect();
    const t = ((ev.clientX - r.left) * (CW / r.width) - PL) / (CW - PL - PR) * LAST_T;
    let best = 0;
    D.frames.forEach((f, i) => { if (Math.abs(f.t - t) < Math.abs(D.frames[best].t - t)) best = i; });
    setFrame(best);
  });
}

// ---- players table -----------------------------------------------------------------
function drawPlayers() {
  const fr = D.frames[frame];
  const rows = ['<tr><th class="l">Champion</th><th class="l">Role</th><th>Lv</th><th>Gold</th><th>CS</th><th>K/D/A</th><th class="l">HP</th></tr>'];
  for (const p of D.participants) {
    const s = fr.p[p.id]; if (!s) continue;
    const pct = s.hp != null && s.hpMax ? Math.max(0, Math.min(1, s.hp / s.hpMax)) : null;
    const hpColor = pct == null ? "#4b5563" : pct > 0.5 ? "#22c55e" : pct > 0.25 ? "#f59e0b" : "#ef4444";
    const hp = pct == null ? "—" : `<span class="hp"><i style="width:${pct * 100}%;background:${hpColor}"></i></span>${Math.round(pct * 100)}%`;
    const died = fr.died.includes(p.id);
    const cls = TEAM[p.team].cls + (died ? " died" : "");
    rows.push(`<tr class="${cls}" title="${(p.name || "").replace(/"/g, "&quot;")}"><td class="l">${p.champion}` +
      `${died ? '<span class="dead">died</span>' : ""}</td>` +
      `<td class="l">${p.role}</td><td>${s.lvl}</td><td>${s.gold.toLocaleString()}</td><td>${s.cs}</td>` +
      `<td>${s.k}/${s.d}/${s.a}</td><td class="l">${hp}</td></tr>`);
  }
  document.getElementById("players").innerHTML = rows.join("");
}

// ---- events list -------------------------------------------------------------------
const filters = document.getElementById("filters");
for (const [cat, label] of CATS) {
  const l = document.createElement("label");
  l.innerHTML = `<input type="checkbox" ${enabled.has(cat) ? "checked" : ""}> ${label}`;
  l.querySelector("input").addEventListener("change", ev => {
    ev.target.checked ? enabled.add(cat) : enabled.delete(cat);
    buildEvents(); render();
  });
  filters.appendChild(l);
}

const evBox = document.getElementById("events");
let rowsByEvent = new Map();
function buildEvents() {
  evBox.replaceChildren(); rowsByEvent = new Map();
  for (const e of D.events) {
    if (!enabled.has(e.cat)) continue;
    const row = document.createElement("div");
    row.className = "ev" + (e.team ? " " + TEAM[e.team].cls : "");
    row.innerHTML = `<span class="t">${mmss(e.t)}</span><span></span>`;
    row.lastChild.textContent = e.label + (e.x == null && (e.cat === "ward") ? " (no position in data)" : "");
    if (e.cat === "special") {
      const bang = document.createElement("span"); bang.className = "bang"; bang.textContent = "!";
      row.lastChild.prepend(bang);
    }
    row.addEventListener("click", () => { selected = e; setFrame(e.f); });
    evBox.appendChild(row); rowsByEvent.set(e, row);
  }
}

function markEvents(scroll) {
  const [t0, t1] = windowOf(frame);
  let first = null;
  for (const [e, row] of rowsByEvent) {
    const now = e.t > t0 && e.t <= t1;
    row.classList.toggle("now", now);
    row.classList.toggle("sel", e === selected);
    if (now && !first) first = row;
  }
  // Scroll only the event box. scrollIntoView would also scroll the page.
  if (scroll && first) {
    const top = first.offsetTop, view = evBox.scrollTop, h = evBox.clientHeight;
    if (top < view || top > view + h - first.offsetHeight) evBox.scrollTop = Math.max(0, top - 40);
  }
}

// ---- controls ----------------------------------------------------------------------
function render(scroll) {
  slider.value = frame;
  const [t0, t1] = windowOf(frame);
  const wp = D.winprob && D.winprob[frame] != null ? `  ·  Blue ${Math.round(100 * D.winprob[frame])}%` : "";
  document.getElementById("clock").textContent = `${mmss(t1)}  (${frame}/${D.frames.length - 1})${wp}`;
  drawMap(); drawPlayers(); markEvents(scroll);
  cursor.setAttribute("x1", cx(t1)); cursor.setAttribute("x2", cx(t1));
  markWinProb(t1);
}
function setFrame(f) { frame = Math.max(0, Math.min(D.frames.length - 1, f)); render(true); }
function togglePlay() {
  const btn = document.getElementById("play");
  if (timer) { clearInterval(timer); timer = null; btn.textContent = "▶"; return; }
  if (frame >= D.frames.length - 1) frame = 0;
  btn.textContent = "❚❚";
  timer = setInterval(() => {
    if (frame >= D.frames.length - 1) { togglePlay(); return; }
    setFrame(frame + 1);
  }, 800);
}

slider.addEventListener("input", () => { selected = null; setFrame(+slider.value); });
document.getElementById("prev").addEventListener("click", () => setFrame(frame - 1));
document.getElementById("next").addEventListener("click", () => setFrame(frame + 1));
document.getElementById("play").addEventListener("click", togglePlay);
document.getElementById("trails").addEventListener("change", () => render());
document.getElementById("names").addEventListener("change", () => render());
document.getElementById("landmarks").addEventListener("change", () => render());
document.addEventListener("keydown", ev => {
  if (ev.target.tagName === "INPUT" && ev.target.type !== "range") return;
  if (ev.key === "ArrowRight") { setFrame(frame + 1); ev.preventDefault(); }
  if (ev.key === "ArrowLeft") { setFrame(frame - 1); ev.preventDefault(); }
  if (ev.key === " ") { togglePlay(); ev.preventDefault(); }
});

function buildMoments() {
  if (!D.moments || !D.moments.length) return;
  document.getElementById("moments-sec").removeAttribute("hidden");
  document.getElementById("moments-note").textContent = D.momentsNote || "";
  const box = document.getElementById("moments");
  for (const m of D.moments) {
    const row = document.createElement("div");
    row.className = "mo " + (m.mine ? m.direction : "ctx");
    const h = document.createElement("div"); h.className = "h"; h.textContent = m.heading;
    const s = document.createElement("div"); s.className = "s"; s.textContent = m.detail;
    row.append(h, s);
    row.title = m.title;
    row.addEventListener("click", () => setFrame(m.frame));
    box.appendChild(row);
  }
}

buildKey(); drawChart(); drawWinProb(); buildMoments(); buildEvents(); setFrame(Math.min(10, D.frames.length - 1));
</script>
</body></html>
"""
