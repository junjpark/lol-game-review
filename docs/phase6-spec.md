# Phase 6 spec — the moments worth reviewing

**Fixed Nov 2, 2026, with the owner, before running anything on the owner's games.** Any
change to a definition or threshold after the validation games have been seen is recorded
here as an explicit owner decision, with the reason (same rule as the Phase 5 gate).

Goal (from `v1-plan.md`): for any game, a ranked list of **6–8 moments**, each with how much it
moved the reviewed player's team's win probability and what it cost directly. Bar (Phase 4
checkpoint): it must show the owner something the box score didn't.

## Owner decisions (Nov 2)

| # | Question | Decision |
|---|---|---|
| D1 | Perspective | The reviewed player's **team's** win probability. Good and bad moments both shown, marked by direction. |
| D2 | Whose moment | **Two lists:** *your moments* and *game swings* (context, never graded against you). |
| D3 | Several events in one minute | One moment per cluster, **one combined swing**, events listed in order. No splitting credit. |
| D4 | What's big | Relative to what's normal for that point in the game (percentiles below). No "blunder"/"mistake" wording in Phase 6. |
| D5 | Death cost | Time dead from a **measured** death-timer table; time back in play from minute snapshots (labelled "to the minute"); CS/XP vs lane opponent; objectives lost meanwhile. |
| D6 | Validation games | **Both** the owner's recent TW2 games (qualitative; the model isn't validated on TW2) and any NA games the owner remembers. |

## Definitions

### 1. Win-probability series
- `p(m)` = the current model's P(reviewed player's team wins) at snapshot `m` (minute `m`).
  Blue's probability for Blue players, 1 − it for Red.
- Swing over snapshots `[a, b]` = `p(b) − p(a)`. Positive = good for your team.

### 2. How unusual a swing is
- Percentile of `|swing|` among corpus swings **of the same length** (k = 1, 2 or 3 minutes)
  **starting at the same minute**, from calibrated **out-of-fold** predictions (honest, never
  in-sample). Stored in `models/wpa_distribution.json` under `windows` (the training script
  writes it; minutes past the last reliable one reuse that one, as now).
- **Show** a moment only if its percentile is **≥ 75**. Label: **≥ 99 "major"**, **≥ 90
  "notable"**, otherwise "moderate".
- For scale (1-minute swings, Nov 1 model): typical 4–5 pts; top 10% 13–19 pts; top 1%
  22–39 pts, larger later in the game.

### 3. Events → clusters (one cluster = one candidate moment)
- **Fight:** champion kills chained in time order. A kill joins a cluster if it comes ≤ **15 s**
  after the cluster's last kill **and** ≤ **3,000 units** from any of its kills.
  *Measured (800 Emerald games):* the median gap between consecutive kills is 12 s, 54% of
  consecutive kills are ≤ 15 s apart, and simultaneous kills in different parts of the map are
  often 7,000+ units apart, so time alone over-merges.
- **Objective:** dragon, Baron, Herald, Voidgrubs, Atakhan (`ELITE_MONSTER_KILL`). Kills
  ≤ 30 s before it within 3,000 units of it join it ("fight for the objective").
- **Structure:** tower or inhibitor (`BUILDING_KILL`, exact positions). Kills ≤ 30 s before,
  within 3,000 units, join it.
- Clusters whose events overlap in time (≤ 15 s) and space (≤ 3,000 units) merge.
- **Span:** from the snapshot at or before the first event to the snapshot after the last.
  Maximum 3 minutes; longer chains are split at the largest time gap.
- Minutes with a swing ≥ the 90th percentile and **no** cluster (farm, plates, slow pressure)
  become "quiet swing" candidates for the game-swings list.

### 4. Involvement (whose moment)
For the reviewed player, a cluster is:
- **direct:** they killed, died, or assisted on any kill in it; or are the killer or in the
  listed helpers of an objective or structure. *Measured:* helper lists are present for 99% of
  Barons, 81% of dragons, 71% of inhibitors, 58% of Heralds, 44% of towers, 40% of Voidgrub
  kills.
- **dead:** an enemy objective or structure in it happened while they were dead (death time
  + measured timer, below).
- **nearby:** alive and within **2,500 units** of any of its events at either bracketing
  snapshot. *Measured (1,200 Emerald games):* 2,500 units catches 74% of credited participants
  and 25% of uncredited teammates. Minute snapshots make distance fuzzy, so "nearby"
  moments are **labelled "nearby, not credited"** so validation can tell whether they feel fair.
- **Your moments** = direct, dead or nearby. **Game swings** = everything else that passes the
  ≥ 75 percentile, plus quiet swings ≥ 90.

### 5. Ranking and selection
- Your moments: rank by percentile (ties: larger |swing|). Keep up to **8**, with starts at least
  **2 minutes apart** (the higher-ranked one wins). Fewer than 6 is fine if fewer pass ≥ 75.
  Don't pad.
- Game swings: up to **3**, percentile ≥ 90, same spacing rule, not overlapping your moments.
- Both lists are shown in game-time order, with rank and label.

### 6. Direct cost (moments where the reviewed player died)
- **Time dead:** looked up in a **measured death-timer table** by level at death and game
  minute. *Measured (1,200 Emerald games, about 209k death–snapshot pairs):* a player counts as
  dead at a snapshot if health is 0. A boundary fit per level × 5-minute bucket gives the
  timer. It matches the community formula at mid levels (level 7 ≈ 21 s, level 10 ≈ 32 s) and is
  longer at low levels (consistent with Riot's 2024 early-timer change). Single snapshots are
  noisy (≈15% misclassified), so the table is ± a few seconds. The table lives in code
  (`lolreview/review/death_timer.py`, like `rift.py`'s measured landmarks) with the script that
  rebuilds it.
- **Positions while dead stay at the death location** (median 10,100 units from the fountain),
  so respawn can't be read from position: it comes from the timer.
- **Back in play:** the first snapshot after respawn where the player is outside their own base
  (beyond the inhibitor turrets, from `rift.py`). Reported **to the minute**.
- **CS/XP forgone:** from the snapshot before the death to the back-in-play snapshot, the lane
  opponent's CS and XP gain minus the player's own. Junglers are compared with the enemy
  jungler. Supports get XP only.
- **Objectives lost meanwhile:** enemy objectives and structures between the death and back in
  play.
- **Base visits (context only):** item purchases are timestamped and only happen in base, so
  they mark "in base at t" (e.g. "dragon taken while you were buying"). They aren't a moment
  trigger.

### 7. Output
- `scripts/moments.py --riot-id "Name#TAG" [--back N] [--platform tw2] [--queue …]` and
  `scripts/moments.py <matchId>`: a terminal table plus a JSON payload (the Phase 9 coach's
  input), and **markers on the replay's win-probability chart** that jump to the moment.
- Per moment: `minute_start/end`, `t_events`, `kind` (fight/objective/structure/quiet),
  `events[]`, `involvement` (direct/dead/nearby + how), `swing`, `percentile`, `label`,
  `direction`, `direct_cost` (if died), `in_base_windows`, `confidence_notes` (e.g.
  "nearby, not credited", "back in play to the minute", "non-ranked queue", "TW2: model not
  validated").

## Validation (fixed before seeing results)

- **Games:** at least **5** the owner remembers: recent **TW2** games (qualitative, labelled as
  such) plus any **NA** games the owner remembers (NA is the official scope, but the owner
  remembers few).
- For each shown moment the owner marks **(a)** real and important, **(b)** real but minor, or
  **(c)** wrong or unfair. Per game: "did it show me something I hadn't noticed?" yes/no. Also
  note any important moment it **missed**.
- **Pass:** (a)+(b) ≥ **70%** of shown moments, **≤ 1** (c) per game on average, and
  "something new" in **≥ half** the games.
- Results go in `docs/phase6-validation.md`. "Nearby, not credited" and context-list moments are
  tallied separately, so it's clear which rule causes any unfair verdicts.

## Amendments made while building (Nov 2), before any owner game was reviewed

1. **Minimum size (`min_abs_swing` = 5 pts).** Percentiles alone let a 2-pt wiggle at minute 0
   rank "top 10%", because swings there are tiny. A shown moment (and a game swing) must also
   move win probability by at least 5 pts, roughly a typical one-minute swing.
2. **Fights in the same minute merge (implements D3).** The first build gave each of two
   separate fights between the same snapshots the *whole* minute's swing (a bot-lane kill showed
   "fight 1–0, +22 pts" when the minute was really a 4–0). Clusters whose snapshot spans overlap
   now merge into one moment listing every part (`moments.merge_by_snapshot`), as long as the
   merged span stays ≤ 3 minutes.

## Amendments from the owner's validation (Nov 2, after games 1–2)

The owner reviewed two of his games (`docs/phase6-validation.md`): verdicts right, analysis
wrong. He was "in" dragon fights he wasn't at, and solo kills in different lanes read as one
"fight". Both fixed:

3. **Parts stay separate (refines amendment 2 and D3).** Fights between the same snapshots still
   share **one** swing (the model can't split it), but each part keeps its own place, fight size
   and **your involvement in that part only**. A moment is listed under *your moments* if you
   were in any part. The text shows one line per part ("Top lane: you killed Nasus" / "Dragon
   pit: 5v4 teamfight, your team lost 2–4 (you weren't there)"), with a note that the swing is
   combined. Places come from a rough region labeller (`rift.region_of`: lanes, river, pits,
   bases, jungle quarters; checked against every outer/inner turret and jungle camp; full region
   polygons remain Phase 7.1).
4. **Same fight needs a shared player or very close distance.** Two events link only if ≤ 15 s,
   ≤ 3,000 units, **and** they share a player (killer, victim or helper) or are ≤ 1,500 units
   apart. *Measured (800 games):* 92% of kill–kill links already share a player; the rule splits
   4% of them and 10% of links involving structures/objectives. Those are separate pushes inside a
   base, where everything is within 3,000 units (the owner's 29:00 case).
5. **Involvement names what the game credited** ("credited with an assist on Miss Fortune",
   "died to Nasus"), so the player can check it, plus display champion names ("Wukong", not
   "MonkeyKing").

## Implementation notes and corpus sanity (Nov 2)

- Code: `src/lolreview/review/{moments,death_timer}.py`, `scripts/moments.py`,
  `scripts/build_death_timers.py`, `tests/test_moments.py`. Replay: bands on the win-probability
  chart plus a "Moments to review" panel (`build_replay_data(..., moments=)`).
- `models/wpa_distribution.json` needs the k-minute `windows`. They're written by
  `train_winprob.py` from out-of-fold predictions, so **rerun training once** (same settings →
  same model). Until then, multi-minute moments are compared with 1-minute swings and the review
  says so.
- **Corpus sanity (391 Emerald corpus games, one random player each; in-sample model, so this
  checks behaviour, not quality):** median **4** moments per game (0–8; 29% of games have
  6–8; 43% fewer than 4); 90% direct, 9% nearby, 1% while dead; 50/50 good/bad overall, but
  winners' moments are 67% good and losers' 34%; 46% of moments include the player's death (so
  carry a direct cost). The rules are conservative, so many games show fewer than the plan's
  6–8. Per "don't pad" that stands; validation records **missed** important moments to decide
  whether the bar is too strict.
- **After the k-minute windows existed (Nov 2, same 391 games):** multi-minute moments are now
  compared with multi-minute swings (at 20:00 the top-10% bar is 14 pts for 1 minute, 20 for 2,
  26 for 3), so the count fell to a **median of 3** (50% of games < 4, 24% have 6–8). Labels:
  53% moderate, 41% notable, 6% major. This is the baseline the owner validates against.

## Out of scope (later phases)
What the player *did* (Phase 7 `action_taken`), what they could see (Phase 7
`knowable_at_the_time`), what stronger players do (Phase 8), coaching text (Phase 9),
`downstream` consequences (Phase 7.4).
