# Decisions and constraints

Why the project is built the way it is. Moved verbatim from CLAUDE.md on Nov 4, 2026; CLAUDE.md
keeps a one-line summary of each and points here. Don't re-litigate these without the owner.

## Scope

Owner plays Emerald on NA. v1 is validated against the owner's own games. **As of Oct 31 the
owner is playing on the Hong Kong server (TW2).** **Scope decision (Nov 2, owner): NA only for
now.** Corpus, baselines, model and holdouts stay NA; TW2 games load and replay but are not a
validation target, and the NA baselines don't apply to them (baselines must be single-region).

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

Nov 4 review (owner question): still holds. Jev is generally available via OpenRouter (no waitlist,
billed per input token). Fits: Phase 7.2 `action_taken` (Choice, compared against the rules on ~100
hand-labelled moments) and Phase 9.4 claim checking (Noul: "is this sentence supported by the
payload?"). Strip Riot IDs from anything sent.
