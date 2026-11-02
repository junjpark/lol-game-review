"""Phase 6 — re-measure the death-timer table from the extracted corpus.

    python scripts/build_death_timers.py                 # emerald corpus
    python scripts/build_death_timers.py --tier diamond

Reads frames_/events_<tier>.parquet. For every champion death, the victim's health at the next
three minute snapshots says whether they were still dead; the best cut-off per (level, game-time
bucket) is the timer. Prints a TABLE to paste into src/lolreview/review/death_timer.py when a patch
changes death timers. See that module for the method and its noise.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from lolreview.config import PROCESSED_DIR
from lolreview.extract.build import load, table_path
from lolreview.review.death_timer import TABLE, bucket_of, fit_table, table_source


def death_rows(frames: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """One row per (death, later snapshot within 2 minutes)."""
    kills = events[events["type"] == "CHAMPION_KILL"][["match_id", "t_ms", "victim_id"]].dropna()
    kills = kills.astype({"victim_id": int})
    f = frames[["match_id", "minute", "t_ms", "participant_id", "hp", "level"]]
    rows = []
    by = {k: g.set_index("minute") for k, g in f.groupby(["match_id", "participant_id"])}
    for mid, t_ms, v in kills.itertuples(index=False):
        g = by.get((mid, v))
        if g is None:
            continue
        k = int(t_ms // 60000) + 1                 # first snapshot after the death
        lvl = g["level"].get(k - 1, 1)
        for j in range(k, k + 3):
            if j in g.index:
                since = (g.at[j, "t_ms"] - t_ms) / 1000
                if 0 < since <= 120:
                    rows.append((t_ms / 60000, int(lvl), since, g.at[j, "hp"] == 0))
    return pd.DataFrame(rows, columns=["minute", "level", "since_s", "dead"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="emerald")
    args = ap.parse_args()
    for name in ("frames", "events"):
        if not table_path(PROCESSED_DIR, name, args.tier).exists():
            sys.exit(f"No {name} table for {args.tier}. Run: python scripts/extract.py --tier {args.tier}")
    frames = load("frames", args.tier, columns=["match_id", "minute", "t_ms", "participant_id", "hp", "level",
                                                  "game_has_afk"])
    frames = frames[~frames["game_has_afk"].astype(bool)]
    events = load("events", args.tier, columns=["match_id", "t_ms", "type", "victim_id"],
                  filters=[("type", "==", "CHAMPION_KILL")])
    rows = death_rows(frames, events)
    table = fit_table(rows)
    pred = np.array([rows.since_s.iat[i] < table[lv][b] if lv in table and b in table[lv] else np.nan
                     for i, (lv, b) in enumerate(zip(rows.level, [bucket_of(m) for m in rows.minute]))], float)
    ok = ~np.isnan(pred)
    print(f"{len(rows):,} death-snapshot pairs from {frames['match_id'].nunique():,} games; "
          f"misclassified {np.mean(pred[ok].astype(bool) != rows.dead.to_numpy()[ok]):.3f}")
    print("\n" + table_source(table))
    changed = [(lv, b) for lv in table for b in table[lv]
               if abs(table[lv][b] - TABLE.get(lv, {}).get(b, np.inf)) > 2]
    print(f"\n{len(changed)} cells differ from the current TABLE by more than 2 s"
          + (f": {changed[:10]}" if changed else ""))


if __name__ == "__main__":
    main()
