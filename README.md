# lol-game-review

A chess.com-style game review for League of Legends, built on Riot's developer APIs.

Point it at a ranked match and it returns per-moment verdicts — what the decision was,
how much it moved your win probability, and what players a tier above tend to do in
comparable spots.

## How it works

Two models with cleanly separated jobs:

1. **A win-probability model** (gradient-boosted trees) trained on per-minute game state.
   Produces one number: P(win) at each point in the game. The change in that number
   across a decision — win probability added, or WPA — is the severity signal, the same
   way centipawn loss is in chess.
2. **An LLM** that turns a curated payload of state, action, and eval into readable
   coaching text. Called with a prompt, not fine-tuned.

Everything between and around those two — feature extraction, rank-matched baselines,
decision detection, the semantic fields — is deterministic code. The trained model is a
small component in the middle of a large amount of plumbing.

## Why this isn't just a chess engine port

Chess has an oracle: an engine can search the position and tell you the objectively best
move. League has no equivalent — the action space isn't enumerable, and the game has
hidden information. So this tool makes **comparative statistical** claims ("this cost you
10% win probability; players in similar spots usually warded first"), never claims of
proven optimality. That distinction drives a lot of the design, particularly the
`knowable_at_the_time` fields that keep advice from being hindsight-biased.

## Status

Pre-Phase 0. See [`docs/v1-plan.md`](docs/v1-plan.md) for the full build plan.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env               # then paste your Riot key into .env
```

Get a development API key at https://developer.riotgames.com. Development keys expire every
24 hours, so expect to re-paste it often — nothing should ever hard-code it.

## Layout

```
src/lolreview/
  riot/        API client, routing values, rate limiting, disk cache
  extract/     timeline JSON -> tidy per-participant-per-minute frames
  baselines/   rank x role x minute aggregates, z-scoring
  model/       win-probability training and calibration
  review/      decision detection, payload assembly, LLM prompting
  viz/         timeline replay and debugging views
data/
  raw/         cached Riot API responses (gitignored)
  processed/   parquet feature tables (gitignored)
  baselines/   computed baseline lookups (gitignored)
docs/          plan and design notes
notebooks/     exploration
scripts/       entry points for collection and batch jobs
```

## Scope of v1

Working pipeline that produces a useful review on a single Emerald ranked game.
No UI, no hosting, no fine-tuning, single region. See the plan for what's deliberately
excluded and why.
