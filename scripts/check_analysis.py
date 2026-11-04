"""Code checks on a finished analysis, run before the stats-reviewer agent sees it.

    python scripts/check_analysis.py docs/analyses/2026-11-05-example.md

Checks (src/lolreview/contracts.py): the contract is complete, resamples by game, uses no fresh
holdout, and the results were produced under exactly this contract (hash) and say whether they
passed. Exit code 1 on any problem, so it can gate a loop.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lolreview import contracts
from lolreview.config import PROJECT_ROOT


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("doc", help="docs/analyses/<date>-<slug>.md")
    args = ap.parse_args()
    try:
        c = contracts.load(args.doc)
    except contracts.ContractError as exc:
        print(f"FAIL contract: {exc}")
        return 1
    res_path = Path(c["results"])
    res_path = res_path if res_path.is_absolute() else PROJECT_ROOT / res_path
    if not res_path.exists():
        print(f"FAIL no results at {res_path} (run {c['script']} first)")
        return 1
    problems = contracts.verify(args.doc, json.loads(res_path.read_text()))
    for p in problems:
        print(f"FAIL {p}")
    if not problems:
        print(f"OK contract {c['_sha256'][:12]} · results {res_path.name} · pass_if: {c['pass_if']}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
