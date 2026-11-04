"""The agent guard (.claude/hooks/agent_guard.py) blocks the hard-to-reverse lane and nothing else."""

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "agent_guard.py"


def run(command: str, env: dict | None = None) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(HOOK)], input=payload, text=True, capture_output=True,
                          env=env).returncode


BLOCKED = [
    "git status", "cd repo && git add -A", "ls; git log -1", "(git diff)", "echo $(git rev-parse HEAD)",
    "python scripts/check_holdout.py --name emerald_holdout_1622",
    "caffeinate -i python scripts/collect_matches.py --tier emerald",
    "python scripts/collect_summoners.py --tier EMERALD --target 10",
    "rm -rf data/processed", "rm models/winprob_xgb.json", "cd x && rm -r ./data", "rm -rf models",
]
ALLOWED = [
    "pytest -q", "cat .gitignore", "ls .github/workflows", "grep -rn digit src",
    "python scripts/moments.py --riot-id 'A#B'", "python scripts/train_winprob.py",
    "ls data/processed", "rm -rf /tmp/scratch", "rm tests/golden/old.expected.json",
    "python -c 'import lolreview.metadata'", "cat docs/model-history.md",
]


def test_blocks_hard_to_reverse_lane():
    for cmd in BLOCKED:
        assert run(cmd) == 2, cmd


def test_allows_everything_else():
    for cmd in ALLOWED:
        assert run(cmd) == 0, cmd


def test_owner_override():
    assert run("git status", env={**os.environ, "LOLREVIEW_ALLOW_AGENT": "1"}) == 0
