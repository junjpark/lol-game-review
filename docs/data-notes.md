# Data, extraction and baselines: build notes (Phases 0–4)

Facts about the corpora, the extraction tables and the baselines. Moved verbatim from CLAUDE.md's
Status section on Nov 4, 2026.

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

## Pro games (from the lol-pro-markets sidetrack)

- **Sidetrack concluded (Nov 1–2): `../lol-pro-markets`** (trading pro LoL on Kalshi). Stopped
  on its own pre-set kill criteria: Kalshi prices move a median 2.1 s *before* Baron/inhibitor/
  fight events appear in pro timelines (so live trading is out), and the market's pre-match
  prices beat Elo ratings (log loss 0.578 vs 0.606; a 5¢-edge bet rule lost ~7¢/contract).
  **Useful here:** Leaguepedia has Riot v5 match + timeline JSON for **3,697 recent pro games**
  (LCK/LEC/LCS/internationals 2024–26; LPL has none), and 523/523 sampled ran through
  `extract_game` unchanged (not yet checked against end-of-game totals). That's a candidate
  "stronger players" comparison set for Phase 8. Fetch code: `lol-pro-markets/src/lolpro/pro/`.
