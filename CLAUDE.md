# CLAUDE.md

Context for AI assistants working in this repo. Read `docs/v1-plan.md` for the phased
build plan; this file captures the *decisions and constraints* behind it, so they don't
get re-litigated or contradicted.

## What this is

A chess.com-style game review for League of Legends. Input: a ranked solo/duo match
pulled from Riot's developer API. Output: per-moment verdicts — what the decision was,
how much it moved win probability, and what stronger players tend to do in comparable
spots.

Owner plays Emerald on NA. v1 is validated against the owner's own games.

## Architecture: two models, separate jobs

1. **Win-probability model** — gradient-boosted trees over per-minute game state.
   Produces one number: P(win) at each point. The delta across a decision (WPA, win
   probability added) is the severity signal, analogous to centipawn loss in chess.
2. **LLM coach** — turns a curated payload (state, action, eval, comparison) into
   readable coaching text. **Prompted, not fine-tuned.**

Everything between and around them is deterministic code: extraction, baselines,
decision detection, semantic fields.

### Why two models and not one

- An LLM cannot produce a calibrated win probability against this specific rank
  population. That number only exists in the corpus and has to be learned from outcomes.
- The win-prob model cannot write prose.
- Feeding raw timeline JSON to an LLM and asking for a review directly would require a
  large hand-written dataset of (stats → expert review) pairs to fine-tune against.
  That dataset doesn't exist and building it was explicitly ruled out.

## Hard constraints

**No oracle exists.** Chess engines can search a position and prove the best move.
League's action space isn't enumerable and the game has hidden information. So this tool
makes **comparative statistical** claims ("this cost ~10% win probability; players in
similar spots usually warded first"), never claims of proven optimality. Do not write
copy or prompts that assert a play was "objectively correct/wrong."

**Counterfactuals are not available.** The logs only record what happened, never what
would have happened otherwise. "What you should have done" is a nearest-neighbor lookup
against similar states in a higher-rank corpus, reported with sample counts and hedged
language — not a computed alternative.

**Hindsight bias is the top product risk.** Without `knowable_at_the_time` (ward
coverage, enemy jungler last-seen, enemies visible), the review blames players for
threats they could not see. This is the fastest way to make output feel worthless to
someone who was in the game.

**Calibration blocks release.** If the win-prob model is miscalibrated, every downstream
severity label is wrong, and one confident "blunder" on a play the user knows was fine
destroys trust permanently. Phase 5.3's calibration plot is a blocker, not polish. Be
more conservative than chess.com: widen the "reasonable" band, reserve "blunder" for
large swings well outside normal variance.

## Design rule: model vs. code

**Train a model only when the rule can't be written down.** `gold_diff = a - b` is code.
"Probability of winning given this state" only exists as a pattern across thousands of
outcomes, so it's a model. Everything in the payload except win probability is lookups,
hand-encoded game rules, or offline aggregation (baselines and percentiles are GROUP BYs,
not inference).

## Data decisions

- **Queue 420 only.** Flex/normals/ARAM would corrupt baselines and the model.
- **Baselines must be rank-matched and single-region.** Comparing an Emerald player to a
  mixed-rank or mixed-region baseline makes the z-scores meaningless.
- **The comparison set for "what you should have done" is deliberately one to two tiers
  higher.** Same-rank comparisons teach players to play like their peers.
- **The win-prob model can span ranks/regions** with rank and region as features; it
  tolerates mixing far better than baselines do.
- **Sample many players, few games each** — 5 games from 1,000 players beats 50 from 100
  for the same API budget.
- **Filter remakes and early surrenders.** Check the surrender flags.
- **Effective sample size for the win-prob model is the number of games, not rows** —
  minute-rows within a game share one label. 5k games is thin; target 15–20k.
- **Patch drift invalidates baselines.** Baseline tables are versioned and recomputed.

## Riot API gotchas

- Two routing schemes: platform (`na1`) for LEAGUE-V4, regional (`americas`) for
  MATCH-V5 and ACCOUNT-V1. See `src/lolreview/riot/routing.py`.
- `gameMode: CLASSIC` does **not** mean ranked — `queueId: 420` does.
- **Patch numbering:** `gameVersion` uses `16.x` in 2026; Riot's public patch names use
  the year (`26.x`). Patch notes "26.19" = `gameVersion` "16.19". `TARGET_PATCHES` must
  use the 16.x form — a 26.x value silently rejected an entire 5,000-match pull once.
- Match IDs increase over time, so sorting cached files by name sorts oldest-first.
- `TURRET_PLATE_DESTROYED` positions are **not** at the turret (they scatter by thousands of
  units). Use the owner team + lane's outer turret from `lolreview/rift.py` instead.
  `BUILDING_KILL` positions, by contrast, are exact.
- `CHAMPION_SPECIAL_KILL` (first blood, multikill, ace) is recorded at a **different position**
  from its `CHAMPION_KILL`, typically 300–400 units away (likely killer vs. victim location,
  unverified). Every one matches a kill by the same killer within 50 ms; join on that.
- `DRAGON_SOUL_GIVEN` with `teamId: 0` fires after the game's **2nd** dragon to reveal the
  soul type; only `teamId` 100/200 means a team earned it (always at its 4th dragon).
- `WARD_PLACED`/`WARD_KILL` also fire for champion ability objects: `wardType` `UNDEFINED`
  (Zyra seeds, Jhin traps, Yorick, Illaoi, …) and `TEEMO_MUSHROOM`. Count only
  `extract.events.REAL_WARD_TYPES` to match Riot's ward totals.
- Team kills on the scoreboard = player kills + deaths to turrets/minions (credited to the
  enemy); deaths to neutral monsters count for nobody. See `is_scoreboard_execution`.
- Riot's `wasAfk` participant flag is reliable (every flagged player's team lost); prefer it
  to idle-position heuristics.
- Map landmarks (turrets, inhibitors, pits, camps) live in `src/lolreview/rift.py`, measured
  from the corpus. The map is point-symmetric via `rift.mirror()`.
- `gameDuration` is in seconds; timeline event timestamps are in milliseconds.
- `timeEnemySpentControlled` (participant frames) is **milliseconds** of CC dealt, summed
  over enemies; it equals the match summary's `totalTimeCCDealt` (seconds) × 1000 — not
  `timeCCingOthers`.
- Use `teamPosition` for role, not `role`/`lane`/`individualPosition`.
- Timeline indexes players by participant ID; the match file by PUUID. Join via
  `info.participants[]`.
- CS is `minionsKilled + jungleMinionsKilled`, not one field.
- `totalGold` (cumulative earned) vs `currentGold` (held now) — using the wrong one
  silently corrupts every gold-diff feature.
- Development API keys expire every 24h. Never hard-code; always via `.env`.
- Timelines are ~1.8 MB pretty-printed. The real fetcher must gzip or minify — at 5k
  matches, `indent=2` would mean ~9 GB of cache.

## Consequence attribution

Two separate payload fields, not one window:

- `direct_cost` — the lockout: time dead, time walking back, CS/XP forgone, objectives
  conceded during it. Measured from position data, not estimated. Clean attribution.
- `downstream` — anchored to the next resolution event with a game-time-scaled cap, and
  flagged lower-confidence so the LLM hedges. Longer windows capture more real
  consequence but increasingly blame the player for teammates' mistakes.

## Deliberately excluded from v1

Do not suggest these without being asked:

- Any UI, hosting, or the eval graph
- LLM fine-tuning (prompt first; fine-tune only if prompting demonstrably fails)
- Sequence models — RNN/LSTM/transformer over the timeline. Trees first. An RNN was
  explored conceptually and rejected for this data: tabular boosting trains in seconds,
  debugs via feature importances, and will likely match it. Revisit only if trees plateau.
- A learned `action_taken` classifier — rules first
- Full vision reconstruction — approximations only
- Rank brackets beyond Emerald/Diamond
- Multi-game trend tracking

## Possible future: Jev / TypeSafe System One

Jev (TypeSafe AI, released Sep 2026) is a non-generative model returning type-safe
structured decisions with calibrated probabilities in ~70–500ms. Their Doom demo runs on
structured game state, not images — the same input shape as this project.

Best candidate fit is `action_taken` (Phase 7.2): typed label out of a fixed vocabulary,
with confidence low enough to suppress rather than mislabel. Secondary fits: moment
scoring in Phase 6, and verifying LLM output against payload facts in Phase 9.4.

It replaces neither model — it can't produce population-calibrated win probability and
can't generate text. Evaluate at Phase 7.2, not before; the rules-based path must work
without it.

## Status

- **Phase 0 done.** Field mapping lives in `docs/extraction-contract.md`. Read its Gaps
  section: ward events carry no position, there is no vision state, and there are no
  summoner-spell events, so 3 of the 4 `knowable_at_the_time` fields are unavailable.
- **Phase 1 done.** Fetcher in `src/lolreview/riot/`
  (rate limiter, gzip cache, client); `scripts/collect_summoners.py` and
  `scripts/collect_matches.py`.
  - **Emerald corpus complete:** 5,000 matches on `TARGET_PATCHES=16.18,16.19,16.20`
    (765 / 898 / 3,337). About 45% of sampled Emerald players have no recent ranked games,
    so yield is ~2.3 usable matches per player.
  - **The corpus is defined by `data/processed/matches_<tier>.json` → `"kept"`.** The raw
    cache in `data/raw/` is shared across tiers and also holds rejected matches, so never
    treat "everything in the cache" as a tier's corpus.
  - **Diamond corpus complete:** 2,000 matches, same patches, from 2,056 downloaded (97%
    yield, thanks to the `startTime` filter and Diamond players being more active).
  - Patch 16.21 is out (Data Dragon lists it). Both corpora stay on 16.18–16.20 so the
    Diamond comparison set matches Emerald; refresh both together later.
- Long runs on a laptop: `caffeinate -i python ...`. The Emerald run lost ~70 minutes to
  what was probably the Mac sleeping.

- **Phase 2 done.** Alignment verified over the real Data Dragon minimap.
  `scripts/replay.py <matchId> | --random | --riot-id "Name#TAG"` writes a
  self-contained HTML replay to `data/processed/replays/` (logic in `src/lolreview/viz/replay.py`).
  Steps minute to minute and deliberately does **not** interpolate positions. Uses the Data
  Dragon minimap if reachable, else a schematic. Draws turrets/inhibitors with live
  destroyed/respawning state, Dragon/Baron pits, jungle camps, and a symbol key. Item IDs
  are named per patch via `src/lolreview/riot/ddragon.py` (Data Dragon, cached in
  `data/raw/ddragon/`); `--offline` skips downloads.
  `build_replay_data(..., winprob=)` is the Phase 5 overlay hook.

- **Phase 3 done** (`src/lolreview/extract/`, `scripts/extract.py`, `scripts/inspect_game.py`).
  Owner verified one of their own games against op.gg: all stats match.
  - `extract_game(match, timeline, tier)` is pure and per-game; `build.py` runs it over a
    tier and writes `frames_/events_/teams_<tier>.parquet` (keys and column types in
    `extract/schema.py`). Read with `extract.build.load(name, tier, columns=, filters=)`.
  - 7,000 games → 2.15M player-minute rows, 6.0M events, 430K team-minute rows; ~15 ms/game.
  - **Validated against Riot's own end-of-game summary on every game** (runs inside every
    build; results in `extract_<tier>.json`): player K/D/A, CS, gold, damage, level, wards
    killed 100%; wards placed 99.99%; team towers/inhibs/dragons/barons/heralds/grubs 100%;
    team kills 99.83%.
  - `teams` columns describe the game *as of that minute* — no future leakage (e.g.
    `first_blood` turns true only when it happens). `win`, `game_has_afk`, `afk_players`,
    `was_afk`, `idle_max` describe the whole game: **filters/labels, never model features.**
  - 162 of 7,000 games have an AFK player (Riot `wasAfk`); exclude via `game_has_afk`.
  - Built tables total ~190 MB of Parquet in `data/processed/`.

- **Phase 4 built** (`src/lolreview/baselines/`, `scripts/build_baselines.py`,
  `scripts/report.py`). Awaiting the owner's checkpoint (4.3.2).
  - **Percentiles, not z-scores, for every stat** (decided with the owner). Z-scores are
    fine for bell-shaped stats (gold diff matches a normal curve almost exactly) but
    misplace count stats: 0 deaths at 10:00 as a jungler is "top 14%" by z-score, but a
    third of junglers tie on 0. Reports word ties explicitly. Mean/std are still stored.
  - **Role-level, not champion-level** (decided with the owner; ~170 champions would
    leave most cells empty). Reports say so.
  - Cells need 300+ player-rows: Emerald reliable through minute 42, Diamond through 38.
    Past that, lookups return None rather than a guess.
  - Holdout check (build on 80% of games, place the other 20%): every stat's average
    percentile 49.1–50.5; continuous stats put ~9% in each extreme tenth.
  - **Finding: Emerald and Diamond are statistically indistinguishable on these stats**
    (median gap 0, identical spread). What separates the tiers is not average CS / gold /
    kills / wards — it must be in conversion, timing, decisions. Implications: the report's
    Diamond column mostly echoes Emerald; for Phase 8's "a tier above" comparison,
    consider Master+ — adjacent tiers share lobbies (Emerald I ↔ Diamond IV), which
    likely blurs the difference.

**Next:** owner runs `scripts/report.py` on 3–5 of their own games and judges whether it
tells them anything new (4.3.2, the go/no-go gate) → Phase 5 win-probability model.

## Conventions

- Package lives in `src/lolreview/`, installed with `pip install -e .`
- Subpackages mirror pipeline stages: `riot`, `extract`, `baselines`, `model`, `review`, `viz`
- Everything under `data/` and `models/` is gitignored; never commit cache or keys
- Build the walking skeleton end-to-end before deepening any one component
