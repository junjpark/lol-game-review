---
description: Gate a finished analysis — code checks first, then an independent stats review
argument-hint: docs/analyses/<file>.md
---

Review the analysis at `$ARGUMENTS`. Follow these steps in order and stop at the first failure.

1. Run `python scripts/check_analysis.py $ARGUMENTS`. If it prints any FAIL line, report the
   failures to the owner and stop. Don't fix anything.
2. Read the contract block in `$ARGUMENTS` to get the script and results paths.
3. Launch the `stats-reviewer` agent. Give it only these three paths: the analysis doc, the script,
   the results file. Tell it to ignore any Decision section. Do not summarise the analysis or
   share your own opinion of it in the prompt.
4. Paste the reviewer's output verbatim under the doc's "Review" section, together with the
   check_analysis.py output line.
5. Tell the owner the OVERALL verdict and any rejects in two or three lines. The decision is
   theirs: don't write the Decision section.
