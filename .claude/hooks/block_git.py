#!/usr/bin/env python3
"""PreToolUse hook: agents don't run git in this repo; the owner does.

Why: in the Cowork sandbox even `git status` creates .git/index.lock, which the sandbox can't
delete from the mounted folder, and that blocks the owner's next `git add` (docs/failures.md).
Reads the hook payload on stdin; exit code 2 blocks the call and shows stderr to the agent.
Override for one session (owner only): LOLREVIEW_ALLOW_AGENT_GIT=1.
"""
import json
import os
import re
import sys

GIT = re.compile(r"(^|[\s;&|(`$])(git)(\s|$)")


def main() -> int:
    if os.environ.get("LOLREVIEW_ALLOW_AGENT_GIT") == "1":
        return 0
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if GIT.search(command):
        print("Blocked: agents don't run git in this repo (it leaves .git/index.lock behind in the "
              "sandbox). Inspect files directly, and give the owner the git commands to run.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
