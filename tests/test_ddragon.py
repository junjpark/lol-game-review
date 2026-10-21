"""Data Dragon helpers — offline, using a tiny fake item.json."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from lolreview.riot.ddragon import item_names, parse_item_names, version_for_patch

VERSIONS = ["16.21.1", "16.20.1", "16.19.1", "16.18.1"]
ITEM_JSON = {
    "type": "item",
    "version": "16.20.1",
    "data": {
        "1055": {"name": "Doran's Blade"},
        "3071": {"name": "Black Cleaver"},
        "2003": {"name": "Health Potion"},
    },
}


def test_version_for_patch_matches_major_minor():
    assert version_for_patch("16.20", VERSIONS) == "16.20.1"
    assert version_for_patch("16.18", VERSIONS) == "16.18.1"


def test_version_for_patch_falls_back_to_newest():
    assert version_for_patch("15.1", VERSIONS) == "16.21.1"
    assert version_for_patch("16.20", []) is None


def test_parse_item_names():
    assert parse_item_names(ITEM_JSON) == {1055: "Doran's Blade", 3071: "Black Cleaver", 2003: "Health Potion"}


def test_item_names_reads_cache_without_network():
    d = Path(tempfile.mkdtemp())
    (d / "items_16.20.1.json").write_text(json.dumps(ITEM_JSON))
    assert item_names("16.20", d)[3071] == "Black Cleaver"
