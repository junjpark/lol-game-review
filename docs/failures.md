# Failure log

When something breaks: fix it, add a row here, and add the check that would have caught it
(test, hook, CI, script guard, or a map entry in CLAUDE.md / a linked doc). A fixed output helps
one run; a new check helps every later one. Newest first. "Open" means no automatic check yet.

| When | What broke | Cause | Check that catches it now | Status |
|---|---|---|---|---|
| Nov 4 | "No future information in features" and "resample by game" were only review questions | Rules lived in reviewers' heads | `tests/test_leakage.py` (cut-the-game test; fails on a planted leak); `contracts.py` refuses row/minute resampling and fresh holdouts | Check in place |
| Nov 4 | Agents could start a holdout check or a collection | Hard-to-reverse actions were only described in text | `.claude/hooks/agent_guard.py` + `tests/test_hooks.py` | Check in place |
| Nov 2 | Review said the player was "in" the dragon fight while he was 1v1 in top lane; kills in different lanes read as one fight | Same-minute merge also merged involvement and fight counts | Parts with own place/involvement (spec amendments 3–4); `tests/test_moments.py`; **golden reviews** `tests/test_golden.py` (all 5 fail if the old linking rule returns) | Check in place |
| Nov 2 | Two separate fights in one minute each got the whole minute's swing | Clusters scored independently against one shared swing | `merge_by_snapshot` + `tests/test_moments.py`; golden reviews | Check in place |
| Nov 2 | 2-point wiggles at minute 0 ranked "top 10%" | Percentiles alone; early swings are tiny | `min_abs_swing` (5 pts) in `review/moments.SPEC`; golden reviews | Check in place |
| Nov 2 | Multi-minute moments compared with 1-minute swings | Swing table predated the k-minute windows | Review prints a note when windows are missing; retrain writes them | Check in place |
| Nov 1 | `git status` from the sandbox left `.git/index.lock`, blocking the owner's `git add` | Sandbox can't delete files it creates in `.git/` on the mounted folder | `.claude/hooks/agent_guard.py` (PreToolUse hook) + `tests/test_hooks.py` | Check in place (hook needs a harness that loads `.claude/settings.json`) |
| Nov 1 | Told the owner `replay.py --back` existed; it didn't | Answered from memory of a sibling script | None automatic. Habit: check `--help` / the parser before telling the owner a flag exists | Open |
| Nov 1 | Riot ID lookups failed for TW2 | ACCOUNT-V1 has no `sea` host | `routing.account_host` + `tests/test_routing.py`; `docs/riot-gotchas.md` | Check in place |
| Nov 1 | `ModuleNotFoundError: lolreview` when running scripts | conda base env shadowing the venv | pytest: `pythonpath` in `pyproject.toml`. Scripts: `conda deactivate; source venv/bin/activate` (README) | Partly (scripts rely on the habit) |
| Oct 31 | Win-prob chart never appeared in the replay | Setting `.hidden = false` doesn't work on SVG elements | Fixed with `removeAttribute("hidden")` | Open (no test renders the page) |
| Oct 31 | Gate v1 failed every variant by 4–5 pts, with failing bins moving between variants | 1,971 test games: per-bin noise (±3.5) close to the ±4 limit | Gate v2 (out-of-fold dev gate + fresh holdout); `check_holdout.py` warns under 4,000 games; `docs/model-history.md` | Check in place |
| Oct 31 | Diamond looked miscalibrated (Blue won 40% of 265 test games) | Small test slice, not the model | Tier/patch checks run out-of-fold over all games | Check in place |
| Oct 31 | "A holdout is used once" was only a sentence in CLAUDE.md | Rule lived in text | `model/holdouts.py` lock in `check_holdout.py` + `tests/test_holdouts.py` (added Nov 4) | Check in place |
| Oct 31 | "No 'blunder'/'objectively' wording" was only a sentence in CLAUDE.md | Rule lived in text | `review/wording.py`, checked on every golden review (added Nov 4) | Check in place |
| Oct | A whole 5,000-match pull was rejected | `TARGET_PATCHES` used Riot's public `26.x` names; `gameVersion` is `16.x` | `docs/riot-gotchas.md` only | **Open: `collect_matches.py` should refuse patch values that don't match the `16.x` form** |
| Oct | Summoner collection silently stopped at 3,200 players | Capacity = per_page × max_page × 4 divisions | Default max_page 150; script warns when short | Check in place |
| Oct | Emerald collection lost ~70 minutes | Mac went to sleep | `caffeinate -i` convention (CLAUDE.md) | Habit only |
| Oct | Risk of mixing servers in one corpus (baselines must be single-region) | — | `collect_matches.py` refuses a second server in an existing corpus | Check in place |
