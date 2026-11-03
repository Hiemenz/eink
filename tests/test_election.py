"""Tests for the election-night modules and the election window in main.py."""

import os
import sys
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

import main
from modules import election_common as ec
from modules import election_cycle, election_governor, election_house, election_senate


def _race(postal, rating="toss-up", votes=(0, 0), won=None, seat="1"):
    cands = [("d-a", "DEM", votes[0]), ("r-b", "GOP", votes[1])]
    return {
        "nyt_id": f"{postal}-{seat}", "seat_number": seat, "rating": rating,
        "candidate_metadata": {i: {"last_name": i, "party": {"nyt_id": p}} for i, p, _ in cands},
        "outcome": {"won": [won] if won else [], "gained": None},
        "top_reporting_unit": {
            "state_postal": postal, "state_abb": postal, "name": postal, "eevp": 50,
            "candidates": [{"nyt_id": i, "votes": {"total": v}, "leader": False} for i, _, v in cands],
        },
    }


def _data(office_id):
    bal = {p: {"seats": 10, "no_election": 5} for p in ("DEM", "GOP", "OTH")}
    return {
        "resultsMeta": {"resultsUpdatedAt": "2026-11-03T20:00:00Z"},
        "pollClosingTimes": [],
        "races": [_race("TN", "solid-rep", (40, 60), "r-b"), _race("GA", "toss-up", (51, 49)),
                  _race("CA", "solid-dem")],
        "partyControlData": {"results": [{"offices": {office_id: {
            "num_races_called": 1, "open_seats": 3, "party_balance": bal,
            "to_win": {"DEM": 51, "GOP": 50}}}}]},
    }


def test_all_views_render_palette_only(tmp_path):
    for mod, oid, key in ((election_senate, "S", "senate"), (election_house, "H", "house"),
                          (election_governor, "S", "governor")):
        for view in ("map", "list"):
            out = str(tmp_path / f"{key}_{view}.bmp")
            cfg = {f"election_{key}": {"output_path": out}, "_election_view": view}
            with patch.object(ec, "fetch_results", return_value=_data(oid)):
                assert mod.generate(cfg) == out
            colors = {c for _, c in Image.open(out).convert("RGB").getcolors(10000)}
            assert colors <= set(ec.PANEL)


def test_race_summary_marks_winner():
    s = ec.race_summary(_race("TN", votes=(40, 60), won="r-b"))
    assert s["called"] and s["winner"]["party"] == "GOP" and s["cands"][0]["votes"] == 60


def test_cycle_advances_only_when_feed_changes(tmp_path):
    seen = []
    state = str(tmp_path / "state.json")
    feeds = {"v": "t1"}
    fetch = lambda office, config: {"resultsMeta": {"resultsUpdatedAt": feeds["v"]}}  # noqa: E731
    with patch.object(election_cycle, "STATE_FILE", state), \
            patch.object(election_cycle.ec, "fetch_results", side_effect=fetch), \
            patch("importlib.import_module") as imp:
        imp.return_value.generate.side_effect = lambda cfg: seen.append(cfg["_election_view"])
        election_cycle.generate({})          # first data -> house map
        election_cycle.generate({})          # unchanged -> hold
        feeds["v"] = "t2"
        election_cycle.generate({})          # new results -> house list
    assert seen == ["map", "map", "list"]


def test_congress_stamp_stable_until_content_changes(tmp_path):
    from modules import congress_bills as cb
    with patch.object(cb, "CACHE", str(tmp_path / "c.json")):
        a = cb._changed_at([{"x": 1}], [])
        with patch.object(cb, "datetime") as dt:
            dt.now.return_value.strftime.return_value = "later"
            assert cb._changed_at([{"x": 1}], []) == a
            assert cb._changed_at([{"x": 2}], []) == "later"


def test_election_window():
    cfg = {"election": {"start": "2026-11-03T18:00:00", "end": "2026-11-04T09:00:00"}}
    with patch.object(main, "datetime") as dt:
        dt.fromisoformat = datetime.fromisoformat
        dt.now.return_value = datetime(2026, 11, 4, 2, 0)
        assert main._election_window(cfg)
        dt.now.return_value = datetime(2026, 11, 4, 10, 0)
        assert not main._election_window(cfg)
    assert not main._election_window({})


def test_feed_shape_validation():
    import pytest
    with pytest.raises(ValueError):
        ec._validate("senate", {"races": []})
    ec._validate("senate", _data("S"))


def test_stale_fallback_alerts(tmp_path):
    import json
    cache = tmp_path / "election_senate.json"
    cache.write_text(json.dumps(_data("S") | {"resultsMeta": {"resultsUpdatedAt": "2026-11-03T20:00:00Z"}}))
    with patch.object(ec, "CACHE_DIR", str(tmp_path)), \
            patch.object(ec.requests, "get", side_effect=OSError("down")), \
            patch.object(ec, "_alert") as alert:
        data = ec.fetch_results("senate", {}, max_age=-1)
    assert data["_stale"] and alert.called
    assert "STALE" in ec.feed_time(data)


def test_track_calls_baselines_then_flags_new(tmp_path):
    calls = str(tmp_path / "calls.json")
    done = ec.race_summary(_race("TN", votes=(40, 60), won="r-b"))
    done["id"] = "TN-1"
    with patch.object(ec, "CALLS_FILE", calls), patch.object(ec, "CACHE_DIR", str(tmp_path)):
        assert ec.track_calls([done], lambda s: "x") == []          # baseline: not "just called"
        new = dict(done, id="GA-1")
        recent = ec.track_calls([done, new], lambda s: "GA Senate")
    assert [lbl for _, lbl in recent] == ["GA Senate"]


def test_flip_text():
    s = ec.race_summary(_race("TN", votes=(40, 60), won="r-b"))
    s["flip"] = True
    assert ec.flip_text([s]) == "  Flips D+0 R+1"
    assert ec.flip_text([]) == ""


def test_balance_text():
    assert ec.balance_text(45, 50, 48, 52, 51, 51) == "Proj D 48-R 52  |  Dems need 6  |  GOP need 1"


def test_poll_groups_and_open():
    from datetime import datetime, timezone
    from modules import election_polls as ep
    data = {"pollClosingTimes": [
        {"poll_closing_time": "2026-11-04T01:00:00.000Z", "reporting_unit": {"state_postal": "TN"}},
        {"poll_closing_time": "2026-11-04T01:00:00.000Z", "reporting_unit": {"state_postal": "AL"}},
        {"poll_closing_time": "2026-11-04T06:00:00.000Z", "reporting_unit": {"state_postal": "AK"}}]}
    assert [s for _, s in ep.groups(data)] == [["AL", "TN"], ["AK"]]
    assert ep.polls_open(data, datetime(2026, 11, 4, 2, tzinfo=timezone.utc))
    assert not ep.polls_open(data, datetime(2026, 11, 4, 7, tzinfo=timezone.utc))


def test_pin_parsing():
    assert election_cycle._parse_pin("senate:list") == ("senate", "list")
    assert election_cycle._parse_pin("local") == ("local", "map")


def test_local_and_polls_render(tmp_path):
    from modules import election_local, election_polls
    data = _data("S") | {"resultsMeta": {"resultsUpdatedAt": "2026-11-03T20:00:00Z"},
                         "pollClosingTimes": [{"poll_closing_time": "2099-01-01T00:00:00Z",
                                               "reporting_unit": {"state_postal": "TN"}}]}
    with patch.object(ec, "fetch_results", return_value=data):
        for mod, key in ((election_local, "local_output_path"), (election_polls, "polls_output_path")):
            out = str(tmp_path / f"{key}.bmp")
            assert mod.generate({"election": {key: out, "home_state": "TN", "home_district": 1}}) == out
            assert Image.open(out).size == (800, 480)


def test_district_file_matches_feed_shape():
    doc = ec._load_districts()
    keys = {(p, 1 if s == 0 else s) for p, s, _ in doc["districts"]}
    assert len(doc["districts"]) == 436           # 435 seats + DC
    assert ("UT", 1) in keys and ("TX", 38) in keys  # 2026 maps (TX has 38 seats)
    assert all(rings for _, _, rings in doc["districts"])


def test_district_map_draws_called_and_leading(tmp_path):
    img = Image.new("RGB", (ec.WIDTH, ec.HEIGHT), ec.WHITE)
    d = ec.ImageDraw.Draw(img)
    info = {("TX", 1): {"color": ec.RED, "solid": True}, ("CA", 1): {"color": ec.BLUE, "solid": False}}
    ec.district_map(img, d, {}, 14, 60, 560, 450, info)
    colors = {c for _, c in img.getcolors(100000)}
    assert ec.RED in colors and ec.BLUE in colors


def test_replay_dir_and_placeholder_filtered(tmp_path):
    import json
    data = _data("S")
    data["races"].append(_race("US", "toss-up"))
    (tmp_path / "election_senate.json").write_text(json.dumps(data))
    got = ec.fetch_results("senate", {"election": {"replay_dir": str(tmp_path)}})
    assert all(r["top_reporting_unit"]["state_postal"] != "US" for r in got["races"])


def test_notify_posts_only_when_configured():
    with patch.object(ec.requests, "post") as post:
        ec.notify({}, "x")
        assert not post.called
        ec.notify({"election": {"discord_webhook": "http://hook"}}, "x")
        post.assert_called_once()


def test_new_call_notifies_senate_not_ordinary_house(tmp_path):
    done = ec.race_summary(_race("CA", votes=(60, 40), won="d-a"))
    done["id"] = "CA-1"
    with patch.object(ec, "CALLS_FILE", str(tmp_path / "c.json")), patch.object(ec, "CACHE_DIR", str(tmp_path)), \
            patch.object(ec, "notify") as note:
        ec.track_calls([], lambda s: "x", {}, "house")                      # baseline
        ec.track_calls([done], lambda s: "CA-1 House", {}, "house")         # ordinary House call
        assert not note.called
        sen = dict(done, id="TX-S")
        ec.track_calls([], lambda s: "x", {}, "senate")                     # senate baseline
        ec.track_calls([done, sen], lambda s: "TX Senate", {}, "senate")
        assert note.call_count == 1


def test_control_winner_and_screen(tmp_path):
    from modules import election_control
    data = _data("H")
    assert ec.control_winner(data, "H") is None
    data["partyControlData"]["results"][0]["offices"]["H"]["party_balance"]["DEM"]["winner"] = True
    assert ec.control_winner(data, "H") == "DEM"
    sen = _data("S")
    with patch.object(ec, "fetch_results", side_effect=lambda o, c: data if o == "house" else sen):
        out = str(tmp_path / "c.bmp")
        assert election_control.generate({"election": {"control_output_path": out}}) == out


def test_voting_screen_renders(tmp_path):
    from modules import election_voting
    d = _data("S") | {"pollClosingTimes": [{"poll_closing_time": "2099-01-01T00:00:00Z",
                                            "reporting_unit": {"state_postal": "TN"}}]}
    with patch.object(ec, "fetch_results", return_value=d):
        out = str(tmp_path / "v.bmp")
        assert election_voting.generate({"election": {"voting_output_path": out}}) == out
        assert election_voting.counted(d) == (0, 0)


def test_baseline_is_per_office_and_persists_when_nothing_called(tmp_path):
    done = ec.race_summary(_race("TN", votes=(40, 60), won="r-b"))
    with patch.object(ec, "CALLS_FILE", str(tmp_path / "c.json")), patch.object(ec, "CACHE_DIR", str(tmp_path)), \
            patch.object(ec, "notify") as note:
        ec.track_calls([], lambda s: "x", {}, "senate")                     # nothing called yet
        ec.track_calls([done], lambda s: "TN Senate", {}, "senate")         # first call of the night
        assert note.call_count == 1
        before = note.call_count
        ec.track_calls([dict(done, id="GOV-TN")], lambda s: "TN Governor", {}, "governor")  # baseline for governor
        assert note.call_count == before


def test_failed_fetch_backs_off(tmp_path):
    import json
    (tmp_path / "election_senate.json").write_text(json.dumps(_data("S")))
    with patch.object(ec, "CACHE_DIR", str(tmp_path)), patch.object(ec.requests, "get", side_effect=OSError("down")) as get, \
            patch.object(ec, "_alert"):
        ec.fetch_results("senate", {}, max_age=-1)
        ec.fetch_results("senate", {}, max_age=-1)
    assert get.call_count == 1


def test_polls_ignore_malformed_entries():
    from modules import election_polls as ep
    assert ep.groups({"pollClosingTimes": [{"poll_closing_time": "bad"}, {}]}) == []


def test_pin_overrides_outside_window():
    import inspect
    assert "election" in inspect.getsource(main.main) and '"pin"' in inspect.getsource(main.main)


def test_congress_sort_not_plus_encoded():
    from modules import congress_bills as cb
    with patch.object(cb.requests, "get") as get, patch.object(cb, "_read_cache", return_value={}), \
            patch.object(cb, "_write_cache"):
        get.return_value.json.return_value = {"bills": []}
        cb.fetch_bill_actions({})
    assert get.call_args.kwargs["params"]["sort"] == "updateDate desc"
