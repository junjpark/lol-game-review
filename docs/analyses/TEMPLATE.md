# <Short title>

Copy to `docs/analyses/<YYYY-MM-DD>-<slug>.md`. Fill in and commit the contract **before** writing
or running the analysis script. After the run, add Results; after the review, add Review and
Decision. Don't edit the contract once results exist (the hash check will catch it). If the plan
has to change, write a new contract and say why under Decision.

```contract
# one line each; this block is hashed
question: <what we want to know, in plain words>
hypothesis: <the expected answer, specific enough to be wrong>
metric: <exactly what is computed, on which rows, e.g. "5-fold CV log-loss gain by game, minutes 8+">
pass_if: <the rule, fixed now, e.g. "gain > 0 and the 90% interval's lower bound > 0">
data: <corpora, comma-separated, e.g. emerald, diamond>
resample_unit: game
script: scripts/analysis/<slug>.py
results: data/processed/analyses/<slug>.json
```

## Why

<Two or three lines: what decision this informs, and what we'd do on pass vs fail.>

## Results

<Filled in after the run: the headline numbers with their intervals, sample sizes in games, and
whether pass_if held. Link the results file. No interpretation beyond the rule.>

## Review

<stats-reviewer verdict per claim (UNIT / VERDICT / REASON / EVIDENCE / SCOPE), plus the
check_analysis.py output.>

## Decision

<Owner: accept / reject / rerun under a new contract, and why. If a lesson came out of it, add it to
docs/failures.md or turn it into a check, and note which here.>
