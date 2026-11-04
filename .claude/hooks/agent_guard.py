#!/usr/bin/env python3
"""PreToolUse hook: keeps agents out of the hard-to-reverse lane. The owner runs these himself.

Blocked for agents (exit code 2; stderr is shown to the agent):
  git                      in the Cowork sandbox even `git status` leaves .git/index.lock behind,
                           which blocks the owner's next `git add` (docs/failures.md)
  check_holdout.py         uses up a fresh holdout; it can be evaluated once (docs/model-history.md)
  collect_summoners.py,    hours of API time on the shared Riot key; one collection at a time
  collect_matches.py       across this repo and lol-pro-markets
  rm on data/ or models/   the corpus and trained models take hours to rebuild

Everything else passes (exit 0). Override for one session (owner only): LOLREVIEW_ALLOW_AGENT=1.
"""
import json
import os
import re
import sys

START = r"(^|[\s;&|(`$])"
RULES = [
    (re.compile(START + r"git(\s|$)"),
     "agents don't run git in this repo (it leaves .git/index.lock behind in the sandbox). "
     "Inspect files directly, and give the owner the git commands to run."),
    (re.compile(r"check_holdout\.py"),
     "check_holdout.py uses up a fresh holdout, which is evaluated once. Give the owner the command."),
    (re.compile(r"collect_(summoners|matches)\.py"),
     "collections use the shared Riot API key for hours. Give the owner the command."),
    (re.compile(START + r"rm\b[^;&|]*?[\s/'\"](data|models)(/|\s|$|['\"])"),
     "deleting under data/ or models/ is the owner's call (hours to rebuild)."),
]


def verdict(command: str) -> str | None:
    for pattern, reason in RULES:
        if pattern.search(command):
            return reason
    return None


def main() -> int:
    if os.environ.get("LOLREVIEW_ALLOW_AGENT") == "1":
        return 0
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    reason = verdict(str((payload.get("tool_input") or {}).get("command") or ""))
    if reason:
        print(f"Blocked: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
