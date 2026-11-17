"""Calendar, day-cycle and rendering tests for the village events in the Trusted Train scene."""

import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.trusted_train import EVENTS  # noqa: E402
from dashboard.trusted_train import (
    EVENT_SEASONS,
    night_level,
    nth_saturday,
    orbit,
    resolve_event,
    train_html,
    village_event,
)

D = dt.date


def test_nth_saturday():
    assert nth_saturday(2026, 4, -1) == D(2026, 4, 25)
    assert nth_saturday(2026, 9, -1) == D(2026, 9, 26)
    assert nth_saturday(2026, 12, 2) == D(2026, 12, 12)
    assert nth_saturday(2026, 5, 1) == D(2026, 5, 2)
    assert nth_saturday(2025, 11, -1) == D(2025, 11, 29)
    assert nth_saturday(2025, 12, -1) == D(2025, 12, 27)  # month rollover


@pytest.mark.parametrize(
    "day, expected",
    [
        (D(2026, 12, 12), "carol_weekend"),
        (D(2026, 12, 7), "carol_weekend"),  # Monday of the festival week
        (D(2026, 12, 13), "carol_weekend"),  # Sunday
        (D(2026, 12, 14), None),
        (D(2026, 11, 28), "lantern_night"),
        (D(2026, 11, 25), None),
        (D(2026, 9, 26), "harvest_fair"),
        (D(2026, 9, 20), None),
        (D(2026, 7, 4), "founders_day"),
        (D(2026, 7, 6), None),
        (D(2026, 4, 25), "spring_fair"),
        (D(2026, 5, 2), "village_market"),  # a Saturday in spring
        (D(2026, 7, 11), "village_market"),
        (D(2026, 10, 3), None),  # fall Saturdays belong to the harvest festival
        (D(2026, 2, 10), None),
    ],
)
def test_village_event_calendar(day, expected):
    assert village_event(day) == expected


def test_resolve_event_auto_follows_calendar_and_theme():
    assert resolve_event("auto", "auto", D(2026, 7, 4)) == (
        "summer",
        False,
        "founders_day",
    )
    assert resolve_event("auto", "auto", D(2026, 12, 12)) == (
        "winter",
        True,
        "carol_weekend",
    )
    # A theme that disagrees with the date drops the event instead of mixing seasons.
    assert resolve_event("auto", "fall", D(2026, 7, 4)) == ("fall", False, None)
    assert resolve_event("none", "auto", D(2026, 7, 4)) == ("summer", False, None)


def test_resolve_event_named_picks_its_season():
    assert resolve_event("harvest_fair", "auto", D(2026, 1, 15)) == (
        "fall",
        False,
        "harvest_fair",
    )
    assert resolve_event("carol_weekend", "auto", D(2026, 6, 1)) == (
        "winter",
        True,
        "carol_weekend",
    )
    with pytest.raises(ValueError):
        resolve_event("founders_day", "winter", D(2026, 7, 4))
    with pytest.raises(ValueError):
        resolve_event(
            "carol_weekend", "winter", D(2026, 2, 1)
        )  # winter, but not Christmastide
    with pytest.raises(ValueError):
        resolve_event("nonsense", "auto", D(2026, 7, 4))


@pytest.mark.parametrize("event", [e for e in EVENTS if e not in ("auto", "none")])
@pytest.mark.parametrize("mode", ["clock", "light", "dark", "auto"])
def test_every_event_renders(event, mode):
    out = train_html(mode=mode, event=event, seed=3, now=dt.datetime(2026, 1, 1, 10))
    assert "tt-a-hollis" in out  # heritage row is always there
    assert 'class="tt-stage' in out


def test_event_props_are_specific_to_their_event():
    kw = dict(mode="auto", seed=3)
    assert "tt-a-wheelring" in train_html(event="harvest_fair", **kw)
    assert "tt-a-wheelring" not in train_html(event="spring_fair", **kw)
    assert "tt-a-dkbanner" in train_html(event="carol_weekend", **kw)
    assert "tt-a-luminaria" in train_html(event="lantern_night", **kw)
    assert "tt-a-fw0" in train_html(event="founders_day", **kw)
    assert "tt-a-farmers" in train_html(event="village_market", **kw)


def test_pilgrimage_and_luminaria_replace_the_corn_maze():
    assert "tt-a-cornmaze" in train_html(theme="fall", event="none", seed=1)
    assert "tt-a-cornmaze" not in train_html(event="harvest_fair", seed=1)
    assert "tt-a-cornmaze" not in train_html(event="lantern_night", seed=1)


def test_dickens_weekend_puts_the_whole_cast_on_the_street():
    out = train_html(event="carol_weekend", seed=5, now=dt.datetime(2026, 12, 12, 10))
    for name in ("scrooge", "dickens", "cratchit", "marley", "fezziwig", "lamplighter"):
        assert "tt-a-dk%s" % name in out


def test_still_mode_has_no_animation_for_events():
    for event in ("harvest_fair", "founders_day", "lantern_night"):
        out = train_html(event=event, still=True, mode="auto", seed=2)
        assert (
            "tt-spin 70s" not in out
            and "tt-fw 9s" not in out
            and "tt-lum 3.2s" not in out
        )


def test_all_event_seasons_are_known():
    assert set(EVENT_SEASONS) == set(EVENTS) - {"auto", "none"}


def test_day_cycle_levels():
    assert night_level(0) == 1 and night_level(3) == 1
    assert night_level(12) == 0
    assert 0 < night_level(6) < 1 and 0 < night_level(19) < 1
    assert night_level(22) == 1


def test_orbit_sun_and_moon():
    x, y, o = orbit(12.5, 6.0)  # sun at midday: high and bright
    assert y > 8 and o == 1
    assert orbit(3, 6.0)[2] == 0  # sun down at 3am
    assert orbit(0, 18.0)[2] == 1  # moon up at midnight
    assert orbit(12, 18.0)[2] == 0


def test_clock_mode_follows_the_hour():
    noon = train_html(
        mode="clock",
        theme="spring",
        event="none",
        seed=1,
        now=dt.datetime(2026, 4, 15, 12),
    )
    late = train_html(
        mode="clock",
        theme="spring",
        event="none",
        seed=1,
        now=dt.datetime(2026, 4, 15, 23),
    )
    assert (
        "-43200s" in noon and "-82800s" in late
    )  # animations start at the right time of day
    assert (
        "tt-a-station-n" in noon
    )  # every sprite carries its night picture for the fade
    assert "@keyframes tt-nite" in noon and "@keyframes tt-sun" in noon


def test_clock_still_mode_is_a_snapshot():
    out = train_html(
        mode="clock",
        still=True,
        theme="spring",
        event="none",
        seed=1,
        now=dt.datetime(2026, 4, 15, 23),
    )
    for name in ("tt-nite", "tt-dayf", "tt-warmk", "tt-sun", "tt-moon"):
        assert "@keyframes " + name not in out
    assert 'class="tt-ov' in out


def test_no_real_world_names_left():
    out = train_html(event="carol_weekend", seed=1).lower()
    for word in ("franklin", "skaneateles", "ramsey", "pilgrimage", "carnton"):
        assert word not in out
