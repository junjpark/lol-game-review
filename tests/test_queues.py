"""--queue parsing and the non-ranked flag in replays."""

from __future__ import annotations

import pytest
from synthetic import make_game

from lolreview.riot.queues import parse_queue, queue_name
from lolreview.viz.replay import build_replay_data


def test_parse_queue():
    assert parse_queue(None) == 420
    assert parse_queue(None, default="config") == "config"
    assert parse_queue("draft") == 400
    assert parse_queue("ANY") is None
    assert parse_queue("440") == 440
    with pytest.raises(ValueError):
        parse_queue("urf-but-wrong")


def test_queue_names():
    assert queue_name(400) == "Normal Draft"
    assert queue_name(None) == "any queue"
    assert queue_name(1700) == "queue 1700"


def test_replay_flags_non_ranked_games():
    match, timeline = make_game()
    match["info"]["queueId"] = 400
    d = build_replay_data(match, timeline)
    assert d["queueName"] == "Normal Draft" and d["ranked"] is False
    match["info"]["queueId"] = 420
    assert build_replay_data(match, timeline)["ranked"] is True
