"""LOLREVIEW_HOME: another project using lolreview as a library keeps its own .env, data/, models/."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def _root(env_home: str | None) -> Path:
    env = {k: v for k, v in os.environ.items() if k != "LOLREVIEW_HOME"}
    if env_home:
        env["LOLREVIEW_HOME"] = env_home
    env["PYTHONPATH"] = os.pathsep.join([str(SRC), env.get("PYTHONPATH", "")])
    out = subprocess.run([sys.executable, "-c", "import lolreview.config as c; print(c.PROJECT_ROOT, c.MODEL_DIR)"],
                         env=env, capture_output=True, text=True, check=True).stdout.split()
    root, models = Path(out[0]), Path(out[1])
    assert models == root / "models"
    return root


def test_default_root_is_this_repo():
    assert _root(None) == SRC.parent


def test_lolreview_home_moves_everything(tmp_path):
    assert _root(str(tmp_path)) == tmp_path.resolve()
