"""Phase 5.1 — the win-probability dataset: one row per game-minute, from Blue's side.

Why Blue's side only: the teams table has two mirror-image rows per minute. Training on
both lets a model say Blue 60% / Red 45% at the same moment. One row of Blue-minus-Red
differences guarantees P(Red) = 1 − P(Blue); any Blue-side advantage is learned.

Every feature describes the game AS OF that minute — nothing from the future. Dropped:
AFK games (already decided), and each game's final snapshot (taken after the nexus falls,
so the outcome is already known).

The same function builds training rows for 13k games and features for one game at
review time, so the two can never drift apart.
"""

from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

# (feature, monotone constraint, meaning). +1: more of this can only raise Blue's win
# probability; -1: can only lower it; 0: unconstrained (let the data decide).
FEATURES: list[tuple[str, int, str]] = [
    ("minute", 0, "game minute"),
    ("gold_diff", +1, "Blue minus Red total gold earned"),
    ("xp_diff", +1, "Blue minus Red total XP"),
    ("cs_diff", +1, "Blue minus Red total CS"),
    ("level_diff", +1, "Blue minus Red average level"),
    ("kill_diff", +1, "Blue minus Red scoreboard kills"),
    ("tower_diff", +1, "Blue minus Red towers destroyed"),
    ("plate_diff", +1, "Blue minus Red turret plates taken"),
    ("blue_inhibs_down", -1, "Blue inhibitors currently down"),
    ("red_inhibs_down", +1, "Red inhibitors currently down"),
    ("blue_dragons", +1, "elemental dragons taken by Blue"),
    ("red_dragons", -1, "elemental dragons taken by Red"),
    ("blue_soul", +1, "Blue has dragon soul"),
    ("red_soul", -1, "Red has dragon soul"),
    ("blue_elder_active", +1, "Blue killed Elder in the last 2:30"),
    ("red_elder_active", -1, "Red killed Elder in the last 2:30"),
    ("blue_barons", +1, "Barons taken by Blue"),
    ("red_barons", -1, "Barons taken by Red"),
    ("blue_baron_active", +1, "Blue killed Baron in the last 3:00"),
    ("red_baron_active", -1, "Red killed Baron in the last 3:00"),
    ("herald_diff", +1, "Blue minus Red Rift Heralds"),
    ("voidgrub_diff", +1, "Blue minus Red Voidgrubs"),
    ("first_blood", +1, "+1 Blue got first blood, -1 Red, 0 not yet"),
    ("first_tower", +1, "+1 Blue got first tower, -1 Red, 0 not yet"),
    ("blue_recent_deaths", -1, "Blue deaths in the last minute (stand-in for 'currently dead')"),
    ("red_recent_deaths", +1, "Red deaths in the last minute"),
    ("gold_diff_3m", 0, "change in gold diff over the last 3 minutes (momentum)"),
    ("gold_diff_5m", 0, "change in gold diff over the last 5 minutes"),
    ("is_diamond", 0, "1 if the game is from the Diamond corpus"),
]
FEATURE_NAMES = [f for f, _, _ in FEATURES]
MONOTONE = [m for _, m, _ in FEATURES]

ELDER_BUFF_MS = 150_000
BARON_BUFF_MS = 180_000
RECENT_DEATH_MS = 60_000

TEAM_COLUMNS = [
    "match_id", "tier", "patch", "minute", "t_ms", "team_id", "win", "game_has_afk",
    "gold", "xp", "cs", "level_avg", "kills", "towers", "plates", "enemy_inhibs_down",
    "dragons", "soul", "barons", "heralds", "voidgrubs", "first_blood", "first_tower",
]
EVENT_COLUMNS = ["match_id", "t_ms", "type", "monster", "team_id"]
EVENT_TYPES = ["CHAMPION_KILL", "ELITE_MONSTER_KILL"]


def split_of(match_id: str) -> str:
    """Deterministic 70/15/15 split by GAME (never by row — minutes of one game share an
    outcome, so splitting rows would leak answers into the test set)."""
    b = zlib.crc32(match_id.encode()) % 100
    return "train" if b < 70 else "val" if b < 85 else "test"


def fold_of(match_id: str, k: int = 5) -> int:
    """Fold for out-of-fold scoring, by GAME. Salted so folds don't line up with split_of."""
    return zlib.crc32(f"fold:{match_id}".encode()) % k


def _event_features(frames: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Buff-active flags and recent deaths at each frame time, per game."""
    out = {k: np.zeros(len(frames), dtype="float32") for k in (
        "blue_elder_active", "red_elder_active", "blue_baron_active", "red_baron_active",
        "blue_recent_deaths", "red_recent_deaths")}
    if events.empty:
        return pd.DataFrame(out, index=frames.index)
    ev = events[events["type"].isin(EVENT_TYPES)]
    ev_by_game = {m: g for m, g in ev.groupby("match_id", sort=False)}
    pos = 0
    for mid, g in frames.groupby("match_id", sort=False):
        idx = np.arange(pos, pos + len(g))
        pos += len(g)
        t = g["t_ms"].to_numpy()
        e = ev_by_game.get(mid)
        if e is None:
            continue

        def times(mask):
            return np.sort(e.loc[mask, "t_ms"].to_numpy())

        kills = e["type"] == "CHAMPION_KILL"
        monsters = e["type"] == "ELITE_MONSTER_KILL"
        for side, team in (("blue", 100), ("red", 200)):
            # CHAMPION_KILL team_id is the acting team, i.e. the victim's enemy
            deaths = times(kills & (e["team_id"] == 300 - team))
            out[f"{side}_recent_deaths"][idx] = (
                np.searchsorted(deaths, t, side="right") - np.searchsorted(deaths, t - RECENT_DEATH_MS, side="right"))
            for monster, key, dur in (("ELDER_DRAGON", "elder", ELDER_BUFF_MS),
                                      ("BARON_NASHOR", "baron", BARON_BUFF_MS)):
                kt = times(monsters & (e["monster"] == monster) & (e["team_id"] == team))
                last = np.searchsorted(kt, t, side="right") - 1
                active = (last >= 0) & (t - kt[np.clip(last, 0, None)] < dur) if len(kt) else np.zeros(len(t), bool)
                out[f"{side}_{key}_active"][idx] = active
    return pd.DataFrame(out, index=frames.index)


def build_features(teams: pd.DataFrame, events: pd.DataFrame, keep_final: bool = False,
                   drop_afk: bool = True) -> pd.DataFrame:
    """teams: rows of the teams table (any number of games). events: rows of the events
    table for the same games (at least EVENT_COLUMNS). Returns one row per game-minute
    with FEATURE_NAMES plus match_id, tier, patch, minute, t_ms, blue_win, split."""
    t = teams[TEAM_COLUMNS]
    blue = t[t["team_id"] == 100].set_index(["match_id", "minute"]).sort_index()
    red = t[t["team_id"] == 200].set_index(["match_id", "minute"]).sort_index()
    df = pd.DataFrame(index=blue.index)
    df["tier"], df["patch"], df["t_ms"] = blue["tier"], blue["patch"], blue["t_ms"]
    df["blue_win"] = blue["win"].astype("int8")
    df["game_has_afk"] = blue["game_has_afk"].astype(bool)
    df["gold_diff"] = blue["gold"] - red["gold"]
    df["xp_diff"] = blue["xp"] - red["xp"]
    df["cs_diff"] = blue["cs"] - red["cs"]
    df["level_diff"] = blue["level_avg"] - red["level_avg"]
    df["kill_diff"] = blue["kills"] - red["kills"]
    df["tower_diff"] = blue["towers"] - red["towers"]
    df["plate_diff"] = blue["plates"] - red["plates"]
    df["blue_inhibs_down"] = red["enemy_inhibs_down"]   # inhibs Red has taken and are still down
    df["red_inhibs_down"] = blue["enemy_inhibs_down"]
    df["blue_dragons"], df["red_dragons"] = blue["dragons"], red["dragons"]
    df["blue_soul"], df["red_soul"] = blue["soul"].astype(int), red["soul"].astype(int)
    df["blue_barons"], df["red_barons"] = blue["barons"], red["barons"]
    df["herald_diff"] = blue["heralds"] - red["heralds"]
    df["voidgrub_diff"] = blue["voidgrubs"] - red["voidgrubs"]
    df["first_blood"] = blue["first_blood"].astype(int) - red["first_blood"].astype(int)
    df["first_tower"] = blue["first_tower"].astype(int) - red["first_tower"].astype(int)
    df = df.reset_index()

    # momentum: computed within each game, so it never reaches across a game boundary.
    # Before minute 3 / 5 it's the change since the start (gold diff is 0 at minute 0).
    by_game = df.groupby("match_id", sort=False)["gold_diff"]
    df["gold_diff_3m"] = df["gold_diff"] - by_game.shift(3).fillna(0)
    df["gold_diff_5m"] = df["gold_diff"] - by_game.shift(5).fillna(0)
    df["is_diamond"] = (df["tier"] == "diamond").astype(int)
    df = pd.concat([df, _event_features(df, events)], axis=1)

    if drop_afk:
        df = df[~df["game_has_afk"]]
    if not keep_final:  # the last snapshot is taken after the nexus falls
        df = df[df["minute"] < df.groupby("match_id")["minute"].transform("max")]
    df["split"] = df["match_id"].map(split_of)
    out = df[["match_id", "tier", "patch", "t_ms", "split", "blue_win", *FEATURE_NAMES]].copy()
    out[FEATURE_NAMES] = out[FEATURE_NAMES].astype("float32")
    return out.reset_index(drop=True)


def build_dataset(tiers: tuple[str, ...] = ("emerald", "diamond"), processed_dir=None) -> pd.DataFrame:
    """The full training table from the extracted teams + events Parquet files.

    A game can sit in two corpora — Emerald I and Diamond IV share lobbies (5 such games
    on the Oct 30 corpus). Each game is kept once, under the first tier listed; otherwise
    it would count twice.
    """
    from ..extract.build import load

    parts = []
    for tier in tiers:
        teams = load("teams", tier, processed_dir, columns=TEAM_COLUMNS)
        events = load("events", tier, processed_dir, columns=EVENT_COLUMNS,
                      filters=[("type", "in", EVENT_TYPES)])
        parts.append(build_features(teams, events))
    df = pd.concat(parts, ignore_index=True)
    owner = df.groupby("match_id", sort=False)["tier"].transform("first")
    return df[df["tier"] == owner].reset_index(drop=True)


def features_for_game(extracted: dict[str, list[dict]]) -> pd.DataFrame:
    """Features for one game straight from extract_game() output — every snapshot,
    including the final one, for drawing a curve."""
    return build_features(pd.DataFrame(extracted["teams"]), pd.DataFrame(extracted["events"]),
                          keep_final=True, drop_afk=False)
