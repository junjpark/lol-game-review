"""Replay data tests on a small synthetic match — no corpus or network needed."""

from __future__ import annotations

from synthetic import make_game

from lolreview.viz.replay import (
    MAX_X,
    MAX_Y,
    MIN_X,
    MIN_Y,
    ROLE_ORDER,
    build_replay_data,
    render_html,
)


def labels(data: dict) -> list[str]:
    return [e["label"] for e in data["events"]]


def test_participants_ordered_blue_then_red_by_role():
    data = build_replay_data(*make_game())
    order = [(p["team"], p["role"]) for p in data["participants"]]
    assert order[:5] == [(100, r) for r in ROLE_ORDER]
    assert order[5:] == [(200, r) for r in ROLE_ORDER]
    assert data["participants"][0]["name"] == "p1#NA1"


def test_kda_is_cumulative_per_frame():
    data = build_replay_data(*make_game())
    f0, f1, f2 = (f["p"] for f in data["frames"])
    assert (f0["1"]["k"], f0["6"]["d"], f0["2"]["a"]) == (0, 0, 0)
    assert (f1["1"]["k"], f1["6"]["d"], f1["2"]["a"]) == (1, 1, 1)
    assert f2["3"]["d"] == 1 and f2["1"]["k"] == 1  # execute gives no kill credit


def test_died_marks_victims_since_previous_frame():
    data = build_replay_data(*make_game())
    assert [f["died"] for f in data["frames"]] == [[], [6], [3]]


def test_curves_are_blue_minus_red():
    data = build_replay_data(*make_game())
    assert data["curves"]["gold"] == [0, 5 * 300, 0]
    assert data["curves"]["xp"][0] == sum(100 * p for p in range(1, 6)) - sum(100 * p for p in range(6, 11))


def test_event_labels_and_acting_team():
    data = build_replay_data(*make_game())
    by_label = {e["label"]: e for e in data["events"]}
    assert by_label["C1 killed C6 (+1 assist)"]["team"] == 100
    assert by_label["Blue destroyed Red's mid outer turret"]["team"] == 100
    assert by_label["Blue took Fire Dragon (C2)"]["team"] == 100
    assert by_label["C3 was executed"]["team"] == 200
    assert by_label["C5 placed a control ward"]["cat"] == "ward"
    assert by_label["Red took Voidgrubs (C7)"]["team"] == 200


def test_item_events_use_item_names():
    from lolreview.viz.replay import describe

    players = {1: {"champion": "Garen", "team": 100}}
    items = {1055: "Doran's Blade", 3071: "Black Cleaver"}
    ev = lambda **kw: {"participantId": 1, **kw}  # noqa: E731
    assert describe(ev(type="ITEM_PURCHASED", itemId=3071), players, items)[0] == "Garen bought Black Cleaver"
    assert describe(ev(type="ITEM_SOLD", itemId=1055), players, items)[0] == "Garen sold Doran's Blade"
    assert describe(ev(type="ITEM_DESTROYED", itemId=1055), players, items)[0] == \
        "Garen's Doran's Blade was used up or combined"
    assert describe(ev(type="ITEM_UNDO", beforeId=3071, afterId=0), players, items)[0] == \
        "Garen undid buying Black Cleaver"
    assert describe(ev(type="ITEM_UNDO", beforeId=0, afterId=1055), players, items)[0] == \
        "Garen undid selling Doran's Blade"
    # unknown IDs and no name table both fall back to the raw ID
    assert describe(ev(type="ITEM_PURCHASED", itemId=9999), players, items)[0] == "Garen bought item 9999"
    assert describe(ev(type="ITEM_PURCHASED", itemId=3071), players)[0] == "Garen bought item 3071"


def test_build_replay_data_passes_item_names_through():
    match, timeline = make_game()
    timeline["info"]["frames"][1]["events"].append(
        {"type": "ITEM_PURCHASED", "timestamp": 20_000, "participantId": 2, "itemId": 3071})
    data = build_replay_data(match, timeline, item_names={3071: "Black Cleaver"})
    assert "C2 bought Black Cleaver" in labels(data)


def test_special_category():
    from lolreview.viz.replay import category

    def sk(kind, n=None):
        return {"type": "CHAMPION_SPECIAL_KILL", "killType": kind, "multiKillLength": n}

    assert category(sk("KILL_FIRST_BLOOD")) == "special"
    assert category(sk("KILL_ACE")) == "special"
    assert category(sk("KILL_MULTI", 2)) == "kill"      # doubles are too common to be special
    assert category(sk("KILL_MULTI", 3)) == "special"
    assert category({"type": "ELITE_MONSTER_KILL"}) == "special"
    assert category({"type": "BUILDING_KILL"}) == "special"
    assert category({"type": "DRAGON_SOUL_GIVEN", "teamId": 100}) == "special"   # soul earned
    assert category({"type": "DRAGON_SOUL_GIVEN", "teamId": 0}) == "other"       # soul type revealed
    assert category({"type": "TURRET_PLATE_DESTROYED"}) == "plate"
    assert category({"type": "CHAMPION_KILL"}) == "kill"

    data = build_replay_data(*make_game())
    cats = {e["label"]: e["cat"] for e in data["events"]}
    assert cats["Blue took Fire Dragon (C2)"] == "special"
    assert cats["Blue destroyed Red's mid outer turret"] == "special"


def test_first_blood_anchored_to_its_kill_not_its_own_position():
    match, timeline = make_game()
    timeline["info"]["frames"][1]["events"].append(
        {"type": "CHAMPION_SPECIAL_KILL", "timestamp": 30_010, "killerId": 1,
         "killType": "KILL_FIRST_BLOOD", "position": {"x": 7000, "y": 7000}})
    data = build_replay_data(match, timeline)
    kill = next(e for e in data["events"] if e["type"] == "CHAMPION_KILL")
    fb = next(e for e in data["events"] if e["type"] == "CHAMPION_SPECIAL_KILL")
    assert (fb["x"], fb["y"]) == (kill["x"], kill["y"])
    assert fb["cat"] == "special"


def test_kill_markers_carry_the_victims_team():
    data = build_replay_data(*make_game())
    kills = {e["label"]: e for e in data["events"] if e["type"] == "CHAMPION_KILL"}
    assert kills["C1 killed C6 (+1 assist)"]["victimTeam"] == 200  # red player died
    assert kills["C3 was executed"]["victimTeam"] == 100           # blue player died


def test_coordinates_normalised_with_y_up():
    data = build_replay_data(*make_game())
    kills = [e for e in data["events"] if e["type"] == "CHAMPION_KILL"]
    assert (kills[0]["x"], kills[0]["y"]) == (0.0, 0.0)  # blue fountain corner
    assert (kills[1]["x"], kills[1]["y"]) == (1.0, 1.0)  # red fountain corner


def test_events_without_position_have_no_coordinates():
    data = build_replay_data(*make_game())
    ward = next(e for e in data["events"] if e["type"] == "WARD_PLACED")
    assert "x" not in ward and "y" not in ward


def test_frame_index_matches_riot_grouping():
    data = build_replay_data(*make_game())
    assert {e["f"] for e in data["events"] if e["t"] <= 60_000} == {1}
    assert {e["f"] for e in data["events"] if e["t"] > 60_000} == {2}


def test_html_is_self_contained_and_escapes_script_tags():
    data = build_replay_data(*make_game({1: "</script><b>x</b>"}))
    html = render_html(data, map_png=b"\x89PNG fake")
    assert html.count("</script>") == 1  # only the real closing tag
    assert "data:image/png;base64," in html
    assert "http" not in html.split("<script>")[1].replace("http://www.w3.org/2000/svg", "")


def test_landmarks_included_and_destroyed_turret_tracked():
    data = build_replay_data(*make_game())
    assert len(data["structures"]) == 28 and len(data["pits"]) == 2 and len(data["camps"]) == 14
    down = {s["id"]: s["down"] for s in data["structures"]}
    assert down["200_mid_outer"] == [[70_000, None]]  # turrets never come back
    assert sum(1 for v in down.values() if v) == 1
    assert all(0 <= s["x"] <= 1 and 0 <= s["y"] <= 1 for s in data["structures"])


def test_inhibitor_down_interval_includes_respawn():
    match, timeline = make_game()
    timeline["info"]["frames"][2]["events"].append(
        {"type": "BUILDING_KILL", "timestamp": 115_000, "teamId": 100, "killerId": 6,
         "buildingType": "INHIBITOR_BUILDING", "laneType": "MID_LANE",
         "position": {"x": 3210, "y": 3217}})
    down = {s["id"]: s["down"] for s in build_replay_data(match, timeline)["structures"]}
    assert down["100_mid_inhib"] == [[115_000, 115_000 + 300_000]]


def test_plates_drawn_on_their_outer_turret_not_event_position():
    from lolreview.rift import STRUCTURES, normalize

    match, timeline = make_game()
    timeline["info"]["frames"][2]["events"].append(
        {"type": "TURRET_PLATE_DESTROYED", "timestamp": 105_000, "teamId": 200, "killerId": 1,
         "laneType": "TOP_LANE", "position": {"x": 7000, "y": 7000}})
    plate = next(e for e in build_replay_data(match, timeline)["events"]
                 if e["type"] == "TURRET_PLATE_DESTROYED")
    turret = next(s for s in STRUCTURES if s["id"] == "200_top_outer")
    assert (plate["x"], plate["y"]) == normalize(turret["x"], turret["y"])


def test_render_without_map_uses_schematic():
    html = render_html(build_replay_data(*make_game()), map_png=None)
    assert "const MAP = null;" in html
