"""The agent git block (.claude/hooks/block_git.py) blocks git and nothing else."""

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "block_git.py"


def run(command: str, env: dict | None = None) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(HOOK)], input=payload, text=True, capture_output=True,
                          env=env).returncode


def test_blocks_git():
    for cmd in ["git status", "cd repo && git add -A", "ls; git log -1", "(git diff)", "echo $(git rev-parse HEAD)"]:
        assert run(cmd) == 2, cmd


def test_allows_everything_else():
    for cmd in ["pytest -q", "cat .gitignore", "ls .github/workflows", "grep -rn digit src", "python scripts/moments.py"]:
        assert run(cmd) == 0, cmd


def test_owner_override():
    import os
    assert run("git status", env={**os.environ, "LOLREVIEW_ALLOW_AGENT_GIT": "1"}) == 0
