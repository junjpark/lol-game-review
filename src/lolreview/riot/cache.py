"""Gzipped disk cache for Riot API responses.

Match and timeline payloads are immutable once a game ends, so they are cached
permanently and never re-fetched. API calls are the scarce resource here — a cache hit
costs nothing against the rate limit, which is what makes collection resumable.

Gzip matters more than it sounds: a pretty-printed timeline is ~1.8 MB, so a 5,000-match
corpus would be roughly 9 GB uncompressed. Compact-separator JSON through gzip brings
that down by better than an order of magnitude.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any


class JsonCache:
    """Namespaced cache of gzipped JSON documents on disk."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, namespace: str, key: str) -> Path:
        # Match IDs look like NA1_5628109779 — safe as filenames, but sanitize anyway.
        safe = key.replace("/", "_").replace("..", "_")
        return self.root / namespace / f"{safe}.json.gz"

    def has(self, namespace: str, key: str) -> bool:
        return self._path(namespace, key).exists()

    def get(self, namespace: str, key: str) -> Any | None:
        path = self._path(namespace, key)
        if not path.exists():
            return None
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, EOFError, json.JSONDecodeError):
            # A truncated file means a crash mid-write. Drop it and re-fetch.
            path.unlink(missing_ok=True)
            return None

    def put(self, namespace: str, key: str, value: Any) -> Path:
        path = self._path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file and rename so a crash can't leave a half-written doc
        # that looks like a valid cache hit.
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(value, fh, separators=(",", ":"))
        tmp.replace(path)
        return path

    def keys(self, namespace: str) -> list[str]:
        d = self.root / namespace
        if not d.exists():
            return []
        return [p.name[: -len(".json.gz")] for p in d.glob("*.json.gz")]

    def size_bytes(self, namespace: str) -> int:
        d = self.root / namespace
        if not d.exists():
            return 0
        return sum(p.stat().st_size for p in d.glob("*.json.gz"))
