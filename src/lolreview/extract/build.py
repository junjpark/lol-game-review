"""Run extraction over a tier's corpus and write the three Parquet tables.

Output (data/processed/):
    frames_<tier>.parquet   one row per player per minute
    events_<tier>.parquet   one row per event
    teams_<tier>.parquet    one row per team per minute
    extract_<tier>.json     what was built, from which corpus, when

Games are processed in match-ID order and written in batches, so memory stays flat no
matter how big the corpus gets. Files are written to a temp name and renamed at the end,
so an interrupted run never leaves a half-written table that looks complete.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Callable

from ..riot.cache import JsonCache
from . import extract_game
from .schema import KEYS, TABLES, arrow_schema
from .validate import game_checks, summarize

# Bump when the extraction logic changes, so stale tables are recognisable.
EXTRACT_VERSION = 1


def table_path(processed_dir: Path, name: str, tier: str) -> Path:
    return Path(processed_dir) / f"{name}_{tier.lower()}.parquet"


def corpus(processed_dir: Path, tier: str) -> list[str]:
    manifest = Path(processed_dir) / f"matches_{tier.lower()}.json"
    if not manifest.exists():
        raise FileNotFoundError(f"No corpus for tier {tier!r}: {manifest} missing")
    return sorted(json.loads(manifest.read_text())["kept"])


def build_tier(
    tier: str,
    cache: JsonCache,
    processed_dir: Path,
    limit: int | None = None,
    batch_size: int = 200,
    log: Callable[[str], None] = print,
) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq

    tier = tier.lower()
    ids = corpus(processed_dir, tier)[:limit] if limit else corpus(processed_dir, tier)
    schemas = {n: arrow_schema(spec) for n, spec in TABLES.items()}
    final = {n: table_path(processed_dir, n, tier) for n in TABLES}
    tmp = {n: p.with_name(p.name + ".tmp") for n, p in final.items()}
    writers = {n: pq.ParquetWriter(tmp[n], schemas[n], compression="zstd") for n in TABLES}

    buf: dict[str, list[dict]] = {n: [] for n in TABLES}
    rows = Counter()
    checks = Counter()
    skipped: list[str] = []
    started = time.time()

    def flush() -> None:
        for n, spec in TABLES.items():
            if buf[n]:
                cols = {c: [r[c] for r in buf[n]] for c in spec}
                writers[n].write_table(pa.Table.from_pydict(cols, schema=schemas[n]))
                rows[n] += len(buf[n])
                buf[n].clear()

    try:
        for i, mid in enumerate(ids, 1):
            match, timeline = cache.get("matches", mid), cache.get("timelines", mid)
            if not match or not timeline:
                skipped.append(mid)
                continue
            tables = extract_game(match, timeline, tier)
            checks.update(game_checks(match, tables))
            for n, r in tables.items():
                buf[n].extend(r)
            if i % batch_size == 0:
                flush()
                log(f"  {tier}: {i:,}/{len(ids):,} games  ({time.time() - started:.0f}s)")
        flush()
    finally:
        for w in writers.values():
            w.close()

    for n in TABLES:
        os.replace(tmp[n], final[n])

    meta = {
        "tier": tier,
        "extract_version": EXTRACT_VERSION,
        "built": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "games": len(ids) - len(skipped),
        "skipped": skipped,
        "rows": dict(rows),
        "keys": {n: list(k) for n, k in KEYS.items()},
        "seconds": round(time.time() - started, 1),
        "cross_check_vs_riot_summary": summarize(checks),
    }
    (Path(processed_dir) / f"extract_{tier}.json").write_text(json.dumps(meta, indent=2))
    return meta


def load(name: str, tier: str, processed_dir: Path | None = None, **kwargs):
    """Read a table as a pandas DataFrame. kwargs go to pandas.read_parquet
    (e.g. columns=[...], filters=[("minute", "==", 10)])."""
    import pandas as pd

    from ..config import PROCESSED_DIR

    return pd.read_parquet(table_path(processed_dir or PROCESSED_DIR, name, tier), **kwargs)
