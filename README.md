# lol-game-review

A chess.com-style game review for League of Legends, built on Riot's developer APIs.

Point it at a ranked match and it returns per-moment verdicts — what the decision was,
how much it moved your win probability, and what players a tier above tend to do in
comparable spots.

## How it works

Two models with cleanly separated jobs:

1. **A win-probability model** (gradient-boosted trees) trained on per-minute game state.
   Produces one number: P(win) at each point. The change in that number across a decision
   — win probability added, or WPA — is the severity signal, the same way centipawn loss
   is in chess.
2. **An LLM** that turns a curated payload of state, action, and eval into readable
   coaching text. Called with a prompt, not fine-tuned.

Everything between and around those two — feature extraction, rank-matched baselines,
decision detection, the semantic fields — is deterministic code.

## Why this isn't just a chess engine port

Chess has an oracle: an engine can search the position and tell you the objectively best
move. League has no equivalent — the action space isn't enumerable, and the game has
hidden information. So this tool makes **comparative statistical** claims ("this cost you
10% win probability; players in similar spots usually warded first"), never claims of
proven optimality.

## Status

| Phase | | |
|---|---|---|
| 0 — Foundations & extraction contract | ✅ | [`docs/extraction-contract.md`](docs/extraction-contract.md) |
| 1 — Data collection | ✅ | 12,000 Emerald + 2,000 Diamond ranked games (16.18–16.21), plus a 5,885-game fresh holdout |
| 2 — Replay visualizer | ✅ | interactive HTML replay of any game, with the win-probability curve |
| 3 — Extraction | ✅ | 2.1M player-minute rows, validated against Riot's end-of-game summary |
| 4 — Baselines | built | percentile of every stat vs. same rank, role and minute |
| 5 — Win probability | released (v1) | XGBoost + time-aware calibration; passed the calibration gate on fresh games (ECE 0.8 pts). v2 adds how gold is spread across players |
| 6–10 | | decision detection, coaching |

Full plan: [`docs/v1-plan.md`](docs/v1-plan.md). Design decisions and data gotchas:
[`CLAUDE.md`](CLAUDE.md).

## Setup

```bash
python -m venv venv
source venv/bin/activate           # Windows: venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env               # then paste your Riot key into .env
pytest                             # should all pass, no key or data needed
```

Get a development API key at https://developer.riotgames.com. Development keys expire
every 24 hours, so expect to re-paste it often — nothing should ever hard-code it.

## Usage

```bash
# Collect a corpus (resumable; safe to stop and restart)
python scripts/collect_summoners.py --tier EMERALD --target 1000
python scripts/collect_matches.py   --tier emerald --target 5000

# Build the tables (frames, events, teams) — ~2 minutes, no API key
python scripts/extract.py --tier all

# Build baselines (seconds) and compare one of your games against them
python scripts/build_baselines.py --tier all
python scripts/report.py --riot-id "Name#TAG"        # add --back 1, 2, ... for older games

# Win-probability model: dataset (~1 min), then train + calibration report (a few minutes)
python scripts/build_winprob_dataset.py
python scripts/train_winprob.py                      # --no-nn skips the neural-net comparison

# Release check: the same calibration gate on fresh games (newest patch, new players)
# (use a NEW name each time; a set the model trained on is refused)
python scripts/collect_summoners.py --tier EMERALD --target 5000 --name emerald_holdout_1622 --exclude emerald,diamond,emerald_holdout --seed 2
python scripts/collect_matches.py --name emerald_holdout_1622 --patches 16.22 --exclude emerald,diamond,emerald_holdout --since 2026-11-04 --target 6000
python scripts/extract.py --tier emerald_holdout_1622
python scripts/check_holdout.py --name emerald_holdout_1622

# Print the hand-check numbers for one of your games
python scripts/inspect_game.py --riot-id "Name#TAG"

# Open a game as an interactive replay
python scripts/replay.py --riot-id "Name#TAG"        # your latest ranked game
python scripts/replay.py --random --tier emerald     # random game from the corpus
python scripts/replay.py NA1_5660427960              # a specific match
```

## Layout

```
src/lolreview/
  config.py    settings from .env, data paths
  rift.py      Summoner's Rift geometry: turrets, pits, camps (measured from the corpus)
  riot/        API client, routing, rate limiter, gzip cache, Data Dragon
  extract/     timelines -> per-player-per-minute tables          (Phase 3)
  baselines/   rank x role x minute aggregates                     (Phase 4)
  model/       win-probability training and calibration            (Phase 5)
  review/      decision detection, payload assembly, LLM coaching  (Phases 6–9)
  viz/         replay visualizer
scripts/       command-line entry points
tests/         offline tests (no API key or corpus needed)
docs/          plan, extraction contract
data/          cache and processed tables (gitignored)
```

---

*lol-game-review isn't endorsed by Riot Games and doesn't reflect the views or opinions of
Riot Games or anyone officially involved in producing or managing Riot Games properties.
Riot Games, and all associated properties are trademarks or registered trademarks of Riot
Games, Inc.*
