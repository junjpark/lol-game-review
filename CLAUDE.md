# CLAUDE.md

Context for AI assistants working in this repo. Read `docs/v1-plan.md` for the phased
build plan; this file captures the *decisions and constraints* behind it, so they don't
get re-litigated or contradicted.

## What this is

A chess.com-style game review for League of Legends. Input: a ranked solo/duo match
pulled from Riot's developer API. Output: per-moment verdicts — what the decision was,
how much it moved win probability, and what stronger players tend to do in comparable
spots.

Owner plays Emerald on NA. v1 is validated against the owner's own games. **As of Oct 31 the
owner is playing on the Hong Kong server (TW2).** Everything so far (corpus, baselines, win-prob
model, holdout) is NA-only, so on TW2 games the model is unvalidated and the NA baselines don't
apply (baselines must be single-region). Scope decision pending.

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
- **Hong Kong = server `tw2`** (Taiwan/HK/Macao): ladder on `tw2.api…`, match history on
  `sea.api…`, Riot ID lookups on `asia.api…`. ACCOUNT-V1 is only served by americas/asia/europe
  (`routing.account_host`). Match downloads route by the match ID's own prefix
  (`routing.match_host`), so `TW2_…` games fetch fine whatever `.env` says. ph2/th2 merged into
  sg2 in Jan 2025.
- **One corpus per server.** `collect_summoners.py` stamps each player with `platform`;
  `collect_matches.py` refuses to add games from another server to an existing corpus name.
  Use names like `emerald_tw2`.
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
  - **Emerald expansion (Oct 30):** 8,605 kept on 16.18–16.20 after a second pull toward
    the model's 15–20k target. 3,937 patch-16.21 games are cached but were rejected because
    `.env` still had `TARGET_PATCHES=16.18,16.19,16.20`; adding 16.21 and re-running keeps
    them (timelines only). Decision: the Emerald corpus takes 16.21 for the Phase 5 model;
    baselines get rebuilt on it. Diamond stays 16.18–16.20 for now — it's statistically
    indistinguishable from Emerald anyway (see Phase 4).
  - `collect_summoners.py` capacity = per_page × max_page × 4 divisions. The old default
    (20 × 40 × 4 = 3,200) silently capped larger targets; default max_page is now 150 and
    it warns when short.
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
  `build_replay_data(..., winprob=)` draws P(Blue wins) as its **own chart** under the gold/XP
  chart (owner's request; it used to be overlaid). Shown only when a trained model exists.
  The final point comes after the nexus falls, and the model can't see the nexus, so it isn't
  pinned to 0/100%.
  `replay.py`, `report.py` and `inspect_game.py` take `--riot-id` with `--back N`, `--platform`
  (e.g. tw2) and `--queue` (ranked by default; draft/blind/flex/any/number; see
  `riot/queues.py`). Games that aren't ranked solo/duo load fine but are flagged: an amber
  warning on the win-probability chart and in the terminal, and a NOTE in the percentile report.
  The model and baselines are queue-420 only.

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

- **Phase 4 done** (`src/lolreview/baselines/`, `scripts/build_baselines.py`,
  `scripts/report.py`).
  - **Checkpoint result (4.3.2): the percentile report told the owner nothing new.** It
    restates box-score outcomes players already see post-game. Consequence: **percentiles
    are background context, not the product.** The bar for Phases 5–6: the moment-level
    review (when and why win probability swung) must surface something the percentile
    report couldn't. Check this explicitly at Phase 6 on the owner's games.
  - The owner found **population patterns over time** interesting where their own box score
    wasn't: e.g. mid kill participation is 25% at 8:00 (tied lowest) but joins kills at
    near-bot rate after 14:00 (48% vs 49%) — early kills are bot-side 2v2s that credit
    2–3 players, while early mid kills are often 1v1 solo kills. Same in Diamond.
    Timing/phase framing resonates; box-score framing doesn't.
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

- **Phase 5: model RELEASED (Oct 31); 5.3.3 eyeball check + exit check still open** (`src/lolreview/model/`, `scripts/build_winprob_dataset.py`,
  `scripts/train_winprob.py`, `scripts/check_holdout.py`). Training runs on the owner's Mac
  (the sandbox has no xgboost/sklearn).
  - **Dataset:** one row per game-minute from Blue's side, 29 features (`dataset.FEATURES`, with
    monotone constraints). 13,654 games / 408,740 rows (Emerald 12,000 on 16.18–16.21, Diamond
    2,000 on 16.18–16.20; AFK games and each final post-nexus snapshot dropped; 5 games in
    both corpora kept once).
  - **Shipped model:** XGBoost (depth 4, min_child_weight 50, 446 trees) trained on all
    games, plus `models.TimeCalibrator`: logit(p') = a + b·m + (c + d·m)·logit(p). Smooth in
    time, because a per-phase correction would jump at 14:00/25:00 and fake a "moment". It
    never reorders predictions. Trust by minute: 0:1.10, 15:1.00, 25:0.93, 35:0.86, 45:0.79;
    late game was really overconfident by up to ~4 pts before it.
  - **Side by side** (test split, all trained on the same 9.5k games, log loss): gold-diff
    logistic 0.5702 · XGBoost 0.5544 · MLP 0.5522 · XGBoost + time calibration 0.5534. The MLP's
    best epoch was 1. A 9-setting regularisation search moved val log loss by < 0.001: the
    model is at the limit of these features, not overfitting.
  - **Calibration gate history (read before touching the gate):**
    - Thresholds were fixed before training (`metrics.GateSpec`): ECE ≤ 2 pts overall, ≤ 3 per
      phase/tier/patch; bins with ≥ 200 games within ±4 pts. They have never been loosened.
    - v1 scored overall+phase on the 1,971 test games. Every variant failed by 4.1–5.0 pts in
      bins whose noise was ~±3.5, and *which* bins failed moved between variants. A
      well-calibrated stand-in failed too. The gate's sample size was the problem.
    - **Gate v2 (owner's explicit post-hoc decision):** (1) development gate = every check on
      out-of-fold predictions over all games, calibration cross-fitted
      (`models.cross_fit_time_calibration`): passed 88/88, worst bin +2.0, noise ±1.1.
      (2) **release gate = the same unchanged gate on a fresh holdout**: newest patch, players
      not in the corpus, no shared matches.
    - **Holdout result:** `emerald_holdout`, 5,739 games after AFK removal (5,885 collected),
      all 16.21, 0 shared matches/players. **Passed all 55 checks.** Log loss 0.5426, ECE 0.78
      (early 1.01, mid 1.06, late 0.49), worst bin −3.0 (early 90–100%, 368 games, noise
      −4.6…−1.5). Recorded in `winprob_meta.json` (`holdout`, `released: true`).
    - **Run a holdout check once per fresh set.** Re-checking the same games after changing
      the model turns it into training signal. A 16.22 set (live Nov 4) would test patch drift.
  - **Known mild bias:** very lopsided *early* states (> 90% before 14:00) are slightly
    underconfident (−3.0 pts on the holdout, outside noise but inside the limit). Early stomps
    read a little less decisive than they are. Watch for it in Phase 6; don't patch it on the
    holdout.
  - **Owner's requirement: late game must not drift toward 50% by default.** A lopsided late
    state should read lopsided, and the curve must swing hard on decisive events. Measured by
    `metrics.volatility_profile` (share of predictions above 90%/below 10%; mean move toward
    the team that just took Baron or Elder, took an inhibitor, or won a fight by 3+ kills, via
    `decisive_events`, where that team wins 81% of the time late). Holdout: late >90/<10 30%,
    decisive move +9.5 pts vs quiet 5.7. Watch out: `min_child_weight` sums p(1−p), so large
    values block splits mostly in lopsided states.
  - `model/predict.py`: `WinProb.load().for_game(extract_game(...))`, `WinProb.released`; the
    replay draws the curve automatically. `wpa_distribution.json` = per-minute quantiles of
    |ΔP| from calibrated out-of-fold predictions over all games: Phase 6's yardstick for
    "unusual" swings.
  - **v1 blind spot: gold distribution (owner's hypothesis, confirmed Nov 1).** Every gold
    feature is a team total, so a lead held by one fed carry and a lead spread evenly look the
    same. Test on 11,704 Emerald corpus games (holdout untouched), logistic, 5-fold CV by
    game, minute 8+:
    - At even team gold (±500) in minutes 15–30, Blue wins **57.4%** when it has the richest
      player by 1.5k+ (600 games) vs **45.8%** when Red has the richest player (2,584 games).
      Team-gold-only predicts ~49–50% for both. Down 2–4k: 36.8% vs 23.9%.
    - Per-role gold matters: 1k of support gold is worth about 40% of 1k of carry gold at
      10:00 (logit 0.18 vs ~0.5 per 1k); bot gold holds its value best late.
    - CV log-loss gain vs team gold only: per-role diffs +0.0026; + richest-player gap +0.0031;
      + concentration +0.0035 (90% range +0.0024…+0.0048). Small on average because these
      states are uncommon, but they're exactly the moments a review cares about. With both
      added, the even-gold case is predicted 57.3% / 45.6%, matching the actual outcomes.
    - Proposed v2 features: 5 per-role gold diffs, richest-player gold gap, gold-concentration
      shares. Per-player gold comes from the frames table, no re-extraction needed. Release
      needs a fresh holdout (16.22); the 16.21 holdout can join training.
    - **XP/level distribution tested too (Nov 1): adds nothing beyond the gold features. Don't
      add it.** On top of v2 gold + team XP/level (same 11,704 games, minute 3+): per-role XP
      +0.0001 (−0.0003…+0.0004), level-6/11/16 spike counts +0.0001, highest-XP-player gap
      −0.0000, all three +0.0001. Per-player XP mostly duplicates per-player gold. The v2 gold
      features still add +0.0038 (+0.0027…+0.0049) even with team XP/level present.
      Level-6 race (2+ more players at 6, minutes 6–9, gold and XP even): 55.6% vs 44.7%, but
      only ~150 games per side. Real-looking but too rare to matter. A player 2+ levels above
      everyone, at even team XP, goes with *losing* (45.4%), and the gold features already
      predict that (45.1%).
  - **v2 built (Nov 1), not yet trained.** `dataset.FEATURES` now has 37 features (v1's 29 +
    `gold_diff_top/jungle/mid/bot/support`, `richest_gap` [unconstrained], `blue_/red_gold_top_share`
    [unconstrained]); role diffs are monotone +1 and NaN when a role is missing or doubled (0 of
    1,468 sampled games). `V1_FEATURE_NAMES` = the first 29. Per-player gold comes from
    `frames_<corpus>.parquet` (`dataset.gold_spread`). Default corpora: emerald, diamond,
    emerald_holdout (~19.4k games). The tier comes from the corpus name (`tier_of`). The training
    report adds an "XGBoost, v1 features" side-by-side model and a **fed-carry check** table
    (`metrics.gold_spread_check`). `train_winprob.py` archives the current model to
    `models/archive/<version>_<built>/` before overwriting. `WinProb` predicts with the feature list
    in the model's own metadata, so archived v1 models still load. `check_holdout.py --name` is now
    required and refuses any corpus the model trained on. **v1 stays the released model until v2
    passes the gate on a fresh 16.22 set.** Sandbox end-to-end dry run (stand-in XGBoost, 4.5k
    games): pipeline OK.
  - **v2 trained (Nov 1): development gate PASSED.** Now the current model in `models/` (v1 archived
    to `models/archive/v1_2026-10-30T184751/`); `released` is unset until the 16.22 check. Test-split
    log loss: XGBoost on v1 features 0.5549 → v2 0.5521 (**+0.0028**, as the logistic analysis
    predicted) → v2 + time calibration 0.5512; MLP 0.5525; gold-diff logistic 0.5712.
    Fed-carry check, out-of-fold over all games, minutes 15–30: at even team gold, Blue-richest
    actual 58.6% vs v2 55.7% (970 games); Red-richest 45.7% vs 46.1% (4,296 games). Down 2–4k:
    36.4% vs 33.0%. v2 captures most of the effect but **still underrates the fed-carry side by
    ~3–4 pts in every band**. That's a known residual; don't tune it away on the holdout.
  - Effective sample size is games, not rows: small slices need large samples. With ~300
    games per bin, coin-flip noise alone gives ECE ≈ 3 pts. Size any future check so per-bin
    noise is well inside its limit.

**Next:** ~Nov 11, collect a fresh 16.22 holdout and run `check_holdout.py --name
emerald_holdout_1622` once (the v2 release). Meanwhile: 5.3.3 (eyeball stomps/comebacks in the replay) and the
Phase 5 exit check on the owner's games, then Phase 6 (decision detection).

## Conventions

- Package lives in `src/lolreview/`, installed with `pip install -e .`
- Subpackages mirror pipeline stages: `riot`, `extract`, `baselines`, `model`, `review`, `viz`
- Everything under `data/` and `models/` is gitignored; never commit cache or keys
- **Used as a library by other projects** (e.g. the owner's separate pro-markets repo, Nov 1).
  `.env`, `data/` and `models/` resolve from `config.PROJECT_ROOT`, which is this repo unless
  `LOLREVIEW_HOME` is set. The other project sets it to its own root *before* importing
  lolreview, so nothing it does lands in this repo. Keep library code free of hard-coded paths
  outside `config`.
- **Assistants: don't run git in the sandbox** (not even `git status`). It creates
  `.git/index.lock` and can't delete it from the mounted folder, which blocks the owner's next
  `git add`. Inspect files directly instead; the owner runs git.
- Build the walking skeleton end-to-end before deepening any one component
