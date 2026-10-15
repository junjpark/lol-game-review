# LoL Game Review — v1 Build Plan

**Goal of v1:** point the pipeline at one of your own Emerald ranked games and read a review that is actually useful — per-moment verdicts with severity labels, grounded in a calibrated win-probability model, written by an LLM that only says things the data supports.

**Out of scope for v1:** any UI, hosting, multi-user support, other rank brackets, LLM fine-tuning. Scripts and notebooks only.

**Realistic timeline:** 6–10 weeks part-time. Phase 7 is the long pole.

---

## Guiding principles

1. **Walking skeleton first.** Build a cheap version of every component and get one review out end-to-end before deepening any single piece.
2. **Hard semantic fields come last** (Phase 7), not first — they need the visualizer to debug and decision detection to scope.
3. **Collection runs in parallel.** Phase 1 kicks off and runs overnight while Phases 2–3 are being built.
4. **Train a model only when you can't write the rule.** Everything except win probability is deterministic code.
5. **Calibration over accuracy.** A miscalibrated win-prob model poisons every downstream label.

---

## Phase 0 — Foundations

*Est: 1 evening · Blocks: everything*

### 0.1 Riot API access
- [x] 0.1.1 Register a developer account and generate a personal API key
- [x] 0.1.2 Store the key in an env var or gitignored config — personal keys expire every 24h, you'll rotate it constantly
- [x] 0.1.3 Note the binding rate limit: 100 requests / 2 minutes (not the 20/s)
- [x] 0.1.4 Pick and pin your region/routing values

### 0.2 Project scaffolding
- [x] 0.2.1 Repo, virtualenv, dependencies (`requests`, `pandas`, `pyarrow`, `xgboost`, `scikit-learn`, `matplotlib`)
- [x] 0.2.2 Directory layout: `data/raw/` (JSON cache), `data/processed/`, `data/baselines/`, `models/`, `notebooks/`, `src/`
- [x] 0.2.3 Config for key, region, target patch, queue ID (420 = ranked solo/duo)

### 0.3 Single-match spike
- [x] 0.3.1 Walk the full chain by hand for one match: Riot ID → PUUID → match IDs → match + timeline
- [x] 0.3.2 Dump both JSONs to disk and read them manually
- [x] 0.3.3 Write down every field name you'll need — this becomes your extraction contract and prevents rework later

**Exit criteria:** two JSON files on disk for one of your own games, and a written list of the fields you care about.

---

## Phase 1 — Data collection

*Est: 2–3 evenings to build, then overnight runs · Start this before Phase 2 and let it run in background*

### 1.1 Fetcher
- [ ] 1.1.1 Token-bucket rate limiter respecting 100/2min
- [ ] 1.1.2 Disk cache keyed by matchId — never re-fetch anything
- [ ] 1.1.3 Retry with exponential backoff on 429 and 5xx
- [ ] 1.1.4 Resumable — a crash at hour 4 must not lose hours 1–3

### 1.2 Sampling frame
- [ ] 1.2.1 Enumerate Emerald I–IV entries via LEAGUE-V4, paging through each division
- [ ] 1.2.2 Sample *across* pages rather than taking the first page of each — target ~800–1,000 distinct summoners
- [ ] 1.2.3 Repeat for Diamond (smaller pull) — this becomes the aspirational comparison set in Phase 8
- [ ] 1.2.4 Persist the summoner list so pulls are reproducible

### 1.3 Corpus pull
- [ ] 1.3.1 4–5 recent matches per summoner, queue 420 only
- [ ] 1.3.2 Filter to current patch (or last 2–3) — patch drift invalidates baselines
- [ ] 1.3.3 Dedupe by matchId (you will get collisions when two sampled players share a game)
- [ ] 1.3.4 **Filter out remakes and early surrenders** (games under ~15 min, or with an early-surrender flag) — they skew baselines and add near-useless rows to win-prob training
- [ ] 1.3.5 Flag games with AFK/disconnected participants for optional exclusion
- [ ] 1.3.6 Targets: ~5,000 Emerald matches, ~2,000 Diamond
- [ ] 1.3.7 Log collection stats: match count, unique players, patch distribution, role distribution

**Exit criteria:** 5k+ deduped Emerald matches cached on disk, collection script resumable and re-runnable.

**Note on sample size:** 5k matches is comfortable for baselines (≈50k participant observations) but thin for the win-prob model, where rows within a game share a label so effective N ≈ number of games. Plan on additional overnight runs toward 15–20k games before Phase 5 validation.

---

## Phase 2 — Visualizer

*Est: 2–3 evenings · Build this BEFORE extraction logic*

This is the debugging substrate for every hard field later. You cannot verify "was the jungler visible here" or "did they push past river" by reading JSON.

### 2.1 Timeline replay
- [ ] 2.1.1 Static Summoner's Rift map image with correct coordinate mapping
- [ ] 2.1.2 Plot all 10 participant positions per frame
- [ ] 2.1.3 Step/scrub through frames

### 2.2 Event track
- [ ] 2.2.1 Kills, deaths, wards placed/killed on a time axis
- [ ] 2.2.2 Objectives (dragon, herald, baron) and building kills
- [ ] 2.2.3 Click an event → jump the replay to that frame

### 2.3 Curves
- [ ] 2.3.1 Team gold and XP differential over time
- [ ] 2.3.2 Leave a hook to overlay the win-prob curve later (Phase 5.4)

**Exit criteria:** you can load any cached match and watch it back well enough to answer "what was happening at minute 14."

---

## Phase 3 — Deterministic extraction, easy fields

*Est: 3–4 evenings · Depends on: 1.3, 2.x*

### 3.1 Frame parser
- [ ] 3.1.1 `participantFrames` → tidy dataframe, one row per participant per minute
- [ ] 3.1.2 Role assignment from `teamPosition` (more reliable than `role`/`lane`, which misclassify autofills and lane swaps)
- [ ] 3.1.3 Lane opponent pairing (participant ↔ their direct matchup)

### 3.2 Core features
- [ ] 3.2.1 Gold diff, XP diff, level diff vs lane opponent
- [ ] 3.2.2 CS = `minionsKilled + jungleMinionsKilled`; CS/min
- [ ] 3.2.3 Game phase labels (thresholds on minute)
- [ ] 3.2.4 Team-level aggregates: total gold diff, objective counts

### 3.3 Event aggregation
- [ ] 3.3.1 K/D/A and kill participation
- [ ] 3.3.2 Wards placed, wards killed, control wards purchased
- [ ] 3.3.3 Objective participation; first blood and first tower timings
- [ ] 3.3.4 Bucket all events into the same per-minute windows as the frames

### 3.4 Validation
- [ ] 3.4.1 Spot-check extracted numbers for one match against the visualizer
- [ ] 3.4.2 Cross-check the same match against op.gg — your CS@10 and gold diffs should match

**Exit criteria:** a processed parquet of per-participant-per-minute rows for the whole corpus, numbers verified against an external source.

---

## Phase 4 — Baselines

*Est: 1–2 evenings · Depends on: 3.x*

### 4.1 Aggregation
- [ ] 4.1.1 GROUP BY rank × role × minute → mean and std for every feature
- [ ] 4.1.2 Store as a lookup table (parquet or sqlite)
- [ ] 4.1.3 Record sample counts per cell — flag thin cells (late-game minutes have fewer games)

### 4.2 Z-scoring
- [ ] 4.2.1 Convert raw features to rank-matched z-scores
- [ ] 4.2.2 Sanity check: distributions should be approximately N(0,1)
- [ ] 4.2.3 Decide handling for minutes beyond typical game length

### 4.3 v0 output — first useful artifact
- [ ] 4.3.1 Generate a percentile report for a single game, no model involved
- [ ] 4.3.2 Run it on 3–5 of your own games and judge honestly whether it tells you anything you didn't know

**Exit criteria:** you can produce a rank-matched percentile breakdown of any game. If 4.3.2 reads as useless, that's important signal to address before investing in Phase 5.

---

## Phase 5 — Win probability model

*Est: 2–3 evenings (training is fast, validation is the time) · Depends on: 4.x*

### 5.1 Dataset prep
- [ ] 5.1.1 One row per team per minute; label = did this team win
- [ ] 5.1.2 Features: z-scored diffs, objective state, phase, rank
- [ ] 5.1.3 **Split by match, never by row** — rows from the same game in both train and test is leakage and will fake great numbers

### 5.2 Train
- [ ] 5.2.1 XGBoost / LightGBM baseline
- [ ] 5.2.2 Add lagged and 5-minute rolling features to capture momentum
- [ ] 5.2.3 Review feature importances — anything absurd at the top means a bug upstream

### 5.3 Validate
- [ ] 5.3.1 **Calibration plot** — when it predicts 70%, do ~70% of those teams win? This matters more than accuracy
- [ ] 5.3.2 Accuracy by game phase — should sit near 50% at minute 0 and climb
- [ ] 5.3.3 Eyeball known stomps and known comebacks; curves should look right
- [ ] 5.3.4 Check calibration separately for Emerald vs Diamond subsets

### 5.4 Integrate
- [ ] 5.4.1 Overlay the win-prob curve in the visualizer (the hook from 2.3.2)
- [ ] 5.4.2 Compute WPA (win probability added) per minute across the corpus
- [ ] 5.4.3 Build the WPA distribution needed for percentile context later

**Exit criteria:** a calibrated model whose curve, viewed over a game you remember, matches your intuition about when it was slipping away.

---

## Phase 6 — Decision detection

*Est: 2–3 evenings · Depends on: 5.x*

### 6.1 Candidate moments
- [ ] 6.1.1 Rule-based triggers: deaths, objective contests, large WPA swings, back timings
- [ ] 6.1.2 Merge overlapping triggers into single moments

### 6.2 Ranking and selection
- [ ] 6.2.1 Score candidates by |WPA| and percentile-vs-similar
- [ ] 6.2.2 Select top 6–8 per game — you are not reviewing every tick

### 6.3 Direct cost
- [ ] 6.3.1 Lockout duration **measured** from position data (death → re-entering lane region), not estimated from champion speed
- [ ] 6.3.2 CS and XP forgone during lockout
- [ ] 6.3.3 Objectives conceded during lockout

**Exit criteria:** for any game, a ranked list of 6–8 reviewable moments with their WPA and direct cost.

---

## Phase 7 — Hard semantic fields

*Est: 1–2 weeks · The long pole · Depends on: 2.x (visualizer), 6.x*

### 7.1 Map regions
- [ ] 7.1.1 Define coordinate boxes/polygons: lanes, river, jungle quadrants, bases, pit areas
- [ ] 7.1.2 Validate region assignment by replaying games in the visualizer

### 7.2 `action_taken`
- [ ] 7.2.1 Define a vocabulary of 8–10 recognizable patterns (pushed past river unwarded, backed with a full wave, contested objective without vision, overstayed at low HP, etc.)
- [ ] 7.2.2 Implement each as a rule over positions + events
- [ ] 7.2.3 Moments matching no pattern are excluded from the review — coverage gaps are acceptable
- [ ] 7.2.4 Measure coverage: what fraction of top-ranked moments get a label?

### 7.3 `knowable_at_the_time`
- [ ] 7.3.1 Ward coverage approximation: ward within radius R, placed within duration D
- [ ] 7.3.2 Enemy jungler last-seen heuristic (frames since last visible)
- [ ] 7.3.3 Count of enemies visible on minimap at the moment
- [ ] 7.3.4 Validate hard against the visualizer — this is where it pays for itself
- [ ] 7.3.5 Do **not** build full vision reconstruction; approximate is enough to avoid hindsight-biased advice

### 7.4 `consequence`
- [ ] 7.4.1 `direct_cost` from 6.3 — clean attribution, always defensible
- [ ] 7.4.2 `downstream` anchored to the next resolution event (objective contested, fight ends), with a cap that scales with game time
- [ ] 7.4.3 Flag downstream as lower-confidence so the LLM hedges its language there

**Exit criteria:** the three hard fields populate for most selected moments, and spot-checking 10 of them in the visualizer shows the labels are fair.

---

## Phase 8 — Comparison set

*Est: 3–4 evenings · Depends on: 7.x, Diamond corpus from 1.2.3*

### 8.1 Similarity metric
- [ ] 8.1.1 Hand-weighted distance over normalized state features (gold diff, HP, phase, objective timers, role)
- [ ] 8.1.2 Decide which features must match exactly (role, phase) vs. approximately

### 8.2 Nearest-neighbor lookup
- [ ] 8.2.0 Run Phase 3, 6 and 7.2 extraction over the **Diamond** corpus too — you can't report "what they did differently" without action labels on the comparison set
- [ ] 8.2.1 Index Diamond corpus states
- [ ] 8.2.2 Retrieve k similar situations for a given moment
- [ ] 8.2.3 Summarize: most common action taken, average WPA of that action, sample size
- [ ] 8.2.4 Suppress the comparison entirely when k is too small — better silent than wrong

**Exit criteria:** for a given moment, you get back a defensible "in similar spots, players did X" with a sample count attached.

---

## Phase 9 — LLM coaching layer

*Est: 2–3 evenings · Depends on: 7.x, 8.x*

### 9.1 Payload assembly
- [ ] 9.1.1 Build the per-moment JSON: player, moment, state_before, knowable_at_the_time, action_taken, direct_cost, downstream, eval, similar_situations, game_pattern
- [ ] 9.1.2 Compute `game_pattern` by grouping flagged moments by tag and finding repeats
- [ ] 9.1.3 Keep it curated — do not dump raw timeline JSON

### 9.2 Prompt
- [ ] 9.2.1 System prompt: coaching role, tone, severity vocabulary, rank-appropriate advice level
- [ ] 9.2.2 Explicit hedging rules for counterfactuals ("in similar situations" not "you should have")
- [ ] 9.2.3 Anti-hallucination constraint: only reference facts present in the payload
- [ ] 9.2.4 Write 5–10 few-shot examples by hand in exactly the voice you want

### 9.3 Output
- [ ] 9.3.1 Per-moment verdict with severity label
- [ ] 9.3.2 Game summary naming recurring patterns, not repeating individual moments
- [ ] 9.3.3 Conservative severity thresholds — widen the "reasonable" band, reserve "blunder" for large swings

### 9.4 Guardrails
- [ ] 9.4.1 Programmatic check that generated claims reference only payload fields
- [ ] 9.4.2 Spot-check for invented item names, ability interactions, or champion facts

**Exit criteria:** given a payload, the LLM produces commentary you'd believe came from a competent coach who watched the game.

---

## Phase 10 — End-to-end and honest evaluation

*Est: ongoing · Depends on: all*

- [ ] 10.1 Single entry point: matchId + PUUID → full review
- [ ] 10.2 Run it on 10 of your own recent games
- [ ] 10.3 For each, grade it yourself: is the verdict right? generic? hindsight-biased? miscalibrated?
- [ ] 10.4 Tally failure modes and decide what to deepen — this list, not a roadmap written today, determines v2

**Exit criteria:** you'd show the output to a friend without caveating it first.

---

## Parallel tracks

| Track | Runs during | Note |
|---|---|---|
| Corpus collection (1.3) | Phases 2–4 | Overnight runs; start early, expand toward 15–20k games before 5.3 |
| Visualizer improvements (2.x) | Phases 3, 6, 7 | Add views as extraction needs them |
| Few-shot example writing (9.2.4) | Anytime after Phase 4 | Write these while reviewing your own games manually |

---

## Known risks

**Miscalibration cascades.** If the win-prob model is off, every severity label downstream is wrong and the tool loses trust instantly. Phase 5.3 is not optional polish — treat a failed calibration plot as a blocker.

**Patch drift.** Baselines computed on patch N may not hold on patch N+3. Design the baseline table to be recomputed and versioned from the start rather than retrofitting it later.

**Hindsight bias.** Without `knowable_at_the_time`, the review blames players for things they could not see. This is the single fastest way to make the output feel stupid to someone who was actually in the game.

**Scope creep into the app.** Building UI before v1 produces useful text is the most likely way this stalls. The eval graph is tempting and it is not v1.

**Key expiry and rate limits.** Personal keys die every 24h. Anything long-running needs to survive a key swap without losing progress.

**Effective sample size confusion.** 500k minute-rows looks like a lot; for a per-game label it behaves like the number of games. Don't be fooled by row counts when judging whether the model has enough data.

---

## What v1 deliberately excludes

- Any UI or hosting
- LLM fine-tuning (prompting first; fine-tune only if prompting demonstrably fails)
- Rank brackets beyond Emerald/Diamond
- Sequence models (RNN/transformer) — trees first, revisit only if they plateau
- A learned `action_taken` classifier — rules first
- Full vision reconstruction — approximations only
- Multi-game trend tracking across your match history
