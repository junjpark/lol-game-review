# Analyses

One file per question ("does gold concentration predict wins beyond team gold?"), written so the
answer can't be bent after the numbers are in. The fed-carry and XP tests (Nov 1,
`docs/model-history.md`) predate this; new ones follow it.

The flow is a small graph: one human decision at the end, everything before it checkable.

```text
owner + assistant: contract (TEMPLATE.md), committed before any code
  -> analyst: writes scripts/analysis/<slug>.py, which calls contracts.load() and writes
     contracts.stamp() + "passed" into the results file; runs it
  -> code gate: python scripts/check_analysis.py docs/analyses/<file>.md
     (complete contract, resample by game, no fresh holdout, contract unchanged since the run)
  -> stats-reviewer agent (.claude/agents/stats-reviewer.md): fresh context, read-only,
     sees contract + script + results, NOT the analyst's write-up; tries to reject each claim
  -> owner: decides (Decision section)
```

Return path: a rejected claim goes back alone, with the reviewer's evidence and a scope ("fix the
resampling in this function only"). Three rejections of the same claim means the contract was the
problem: write a new one. Learning path: a confirmed reviewer finding becomes a row in
`docs/failures.md` and, where possible, a test (as `tests/test_leakage.py` did).

In Claude Code: `/review-analysis docs/analyses/<file>.md` runs the code gate, then the reviewer.
In Cowork, ask the assistant to run the same steps; it can launch a separate reviewer agent with the
brief in `.claude/agents/stats-reviewer.md`.
