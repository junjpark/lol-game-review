# Session log

One change receipt per working session, newest first. It replaces the old ever-growing Status
section of CLAUDE.md: the next session reads the top entry plus CLAUDE.md's "Current state".
History before Nov 4 lives in `docs/data-notes.md`, `docs/model-history.md` and the build log at
the end of `docs/phase6-spec.md`.

Templates (keep each to a few lines):

```text
CONTRACT (start, agreed with the owner before code)
objective:      what must exist at the end
must not change: behaviour, files or numbers that stay put
done when:      the evidence (tests, numbers, owner check)
needs approval: anything irreversible or outside the repo

RECEIPT (end)
changed:        what, where
verified:       how (tests run, numbers checked)
not verified:   what wasn't, and why
decisions:      choices made, and by whom
open risks:     what could still be wrong
```

---

## Nov 4, 2026: harness (map, checks, contracts, failure log)

**Contract.** Objective: adopt items 1–5 of the harness plan. Must not change: any model, review
or pipeline behaviour; any content of the old CLAUDE.md (moved, not deleted). Done when: the full
test suite passes, every old CLAUDE.md line exists in the new docs, and each new check has a test
that shows it can fail. Needs approval: nothing (owner commits).

**Changed**
- `CLAUDE.md` 425 → ~100 lines: a map (constraints, which doc answers what, commands, current
  state, working agreement). Content moved verbatim to `docs/decisions.md`, `docs/riot-gotchas.md`,
  `docs/data-notes.md`, `docs/model-history.md`, and the Phase 6 build log in `docs/phase6-spec.md`.
  Pointers updated in code comments, README, extraction contract, and `../lol-pro-markets/CLAUDE.md`
  (one line).
- CI: `.github/workflows/ci.yml` (pytest + ruff on Python 3.11/3.12); ruff limited to bug rules
  (`[tool.ruff.lint]` in `pyproject.toml`).
- Golden reviews: `tests/golden/` (5 anonymised games: TW2 validation games 1–2, the owner's NA
  Janna game, two corpus games covering "dead" and "nearby" involvement), frozen curves and swing
  table, `tests/golden_fixtures.py`, `tests/test_golden.py`, `scripts/make_golden.py`.
- Wording rule in code: `src/lolreview/review/wording.py`, checked on every golden review.
- Holdout run-once lock: `src/lolreview/model/holdouts.py`, wired into `scripts/check_holdout.py`
  (`--reuse-reason` to override; a reuse never releases a model), `tests/test_holdouts.py`.
- Agent git block: `.claude/settings.json` + `.claude/hooks/block_git.py`, `tests/test_hooks.py`;
  `.claude/settings.local.json` gitignored.
- `docs/failures.md` (seeded with 17 past failures and their checks), this file.

**Verified**
- All test modules pass in the sandbox (stand-in pytest; xgboost/pyarrow-dependent tests skipped
  there, as before). New: golden 5, holdouts 4, hooks 3.
- Lost-content check: every non-empty line of the old CLAUDE.md is present in the new files,
  except headings and two sentences that were reworded in the new CLAUDE.md.
- Golden tests can fail: restoring the old event-linking rule (amendment 4) changes all 5 reviews.
- Holdout lock: detects `emerald_holdout` from the archived v1 model's metadata.
- Fixtures contain no Riot IDs or PUUIDs.

**Not verified**
- `ruff check .` and real `pytest` (no ruff/pytest/xgboost in the sandbox): owner runs both once.
- Whether the git hook fires in Cowork: Claude Code loads `.claude/settings.json`; Cowork may not.
- CI on GitHub: first push will tell.

**Decisions** (owner asked for items 1–5)
- Golden fixtures freeze the win-prob curve, so retrains don't change them; the model has its own gate.
- Holdout reuse is possible only with a recorded reason and can't set `released`.

**Open risks**
- If ruff finds an undefined name somewhere, CI goes red on the first push. That's a real bug to fix.
- Golden outputs were made with the sandbox's numpy evaluator of the saved model; reviews are
  compared after rounding to 4 decimals, so platform differences shouldn't matter.
