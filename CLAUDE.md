# CLAUDE.md

Map for AI assistants working in this repo. It stays short on purpose: read the linked doc when
a task touches its area, not all of them up front. The phased plan is `docs/v1-plan.md`.

## What this is

A chess.com-style game review for League of Legends. Input: a ranked solo/duo match from Riot's
developer API. Output: the moments that moved the player's team's win probability, what each
cost, and (later) what stronger players tend to do in comparable spots.

Owner (Justin) plays Emerald. **Scope: NA ranked solo/duo (queue 420) only.** Corpus, baselines,
model and holdouts are NA. TW2 (Hong Kong) and non-ranked games load and replay but are flagged
and aren't validation targets.

Two models, everything else deterministic code: (1) XGBoost win probability per game-minute,
calibrated; its swings are the severity signal. (2) A prompted (not fine-tuned) LLM coach,
Phase 9. Details and reasons: `docs/decisions.md`.

## Hard constraints (full text in `docs/decisions.md`)

- **No oracle.** Comparative, statistical claims only. Never "objectively right/wrong". No
  "blunder"/"mistake" wording in review output (enforced by `review/wording.py` + tests).
- **No counterfactuals.** "What others do" = nearest-neighbour lookup with sample counts, hedged.
- **Hindsight bias is the top product risk.** Say what the player was credited with and where
  they were; never blame them for what they couldn't see.
- **Calibration blocks release.** Gate thresholds are fixed; never loosen them after seeing
  results. **A holdout is evaluated once** (enforced by `check_holdout.py`'s used-marker).
- **Effective sample size is games, not rows.** Size every check so its noise is inside its limit.
- **Train a model only when the rule can't be written down.**
- Don't suggest anything in "Deliberately excluded from v1" unless asked.

## Map

| Question | Read |
|---|---|
| Why is it built this way? Scope, data rules, exclusions, Jev | `docs/decisions.md` |
| Riot API / timeline quirks (routing, TW2, patches, events, units) | `docs/riot-gotchas.md` |
| Corpora, extraction tables, baselines, pro games | `docs/data-notes.md` |
| Field mapping and known data gaps | `docs/extraction-contract.md` |
| Win-prob model, gate history, holdouts, fed-carry, XP test | `docs/model-history.md` |
| Moments: definitions, thresholds, owner decisions, build log | `docs/phase6-spec.md` |
| Owner's validation of reviews | `docs/phase6-validation.md` |
| What happened in past sessions | `docs/sessions.md` (newest first) |
| What broke before, and which check now catches it | `docs/failures.md` |
| Running an analysis ("does X predict Y?"): contract, checks, review | `docs/analyses/README.md` |

Code (`src/lolreview/`, stages in order): `riot/` API client, routing, cache, queues ·
`extract/` timelines → frames/events/teams tables · `baselines/` percentiles · `model/` dataset,
training, calibration, gate, `predict.WinProb` · `review/` moments, death timers, wording ·
`viz/` HTML replay · `rift.py` map geometry · `contracts.py` analysis contracts. Scripts in `scripts/`, offline tests in `tests/`.

## Commands

```bash
pytest                                              # offline; no key or data needed
python scripts/moments.py --riot-id "Name#TAG"      # review a game (+ --back N, --platform, --queue)
python scripts/replay.py  --riot-id "Name#TAG"      # replay only
UPDATE_GOLDEN=1 pytest tests/test_golden.py         # re-freeze review output after an intended change
python scripts/train_winprob.py                     # owner's Mac only (sandbox has no xgboost)
python scripts/check_holdout.py --name <fresh_set>  # ONCE per fresh set; owner runs it
python scripts/check_analysis.py docs/analyses/<f>  # code gate for an analysis, before the stats-reviewer
```

## Current state (update at the end of each session)

- Phases 0–5 done. Model v2 (37 features) passed the development gate; v1 is the released
  model until v2 passes the fresh 16.22 holdout.
- Phase 6 built; owner validating on NA ranked games (games 1–2 were TW2 tuning games).
- **Next:** Phase 6 validation (≥ 5 counted games). ~Nov 11: collect `emerald_holdout_1622`,
  then `check_holdout.py --name emerald_holdout_1622` once (the v2 release).

## Working agreement

1. **Start of a task: a contract** of 4–5 lines, agreed with the owner before code:
   objective · must not change · done when (evidence, e.g. tests/numbers) · needs owner approval.
   Plan first for anything large or irreversible.
2. **End of a session: a change receipt** at the top of `docs/sessions.md`: changed · verified
   (how) · not verified · decisions · open risks. Then update "Current state" above.
3. **When something breaks:** fix it, then add a line to `docs/failures.md` naming the check
   that would have caught it (test, hook, CI, map entry) and add that check.
4. If a rule matters, enforce it in code (test, hook, CI) rather than adding text here.
5. **Analyses** (feature tests, "does X predict Y") follow `docs/analyses/`: contract committed
   before code, `check_analysis.py`, then an independent stats-reviewer that sees the contract,
   script and results but not the conclusions. The owner decides.

## Conventions

- Package lives in `src/lolreview/`, installed with `pip install -e .`
- Subpackages mirror pipeline stages: `riot`, `extract`, `baselines`, `model`, `review`, `viz`
- Everything under `data/` and `models/` is gitignored; never commit cache or keys. API keys only
  via `.env` (dev keys expire every 24h).
- **Used as a library by other projects** (e.g. the owner's separate pro-markets repo, Nov 1).
  `.env`, `data/` and `models/` resolve from `config.PROJECT_ROOT`, which is this repo unless
  `LOLREVIEW_HOME` is set. The other project sets it to its own root *before* importing
  lolreview, so nothing it does lands in this repo. Keep library code free of hard-coded paths
  outside `config`.
- **Assistants: don't run git in the sandbox** (not even `git status`). It creates
  `.git/index.lock` and can't delete it from the mounted folder, which blocks the owner's next
  `git add`. Inspect files directly instead; the owner runs git.
- **Hard-to-reverse lane is closed to agents** (`.claude/hooks/agent_guard.py`): git,
  `check_holdout.py`, `collect_*.py`, and `rm` under `data/`/`models/`. Give the owner the command.
- Owner works plan-first, wants concise plain explanations and pushback with evidence.
- Long runs on a laptop: `caffeinate -i python ...`.
- Build the walking skeleton end-to-end before deepening any one component
