"""Pure curve tests, including a 1:1 comparison with the template sensors this integration replaces."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from astral import Observer
from astral.sun import SunDirection, elevation, midnight, noon, time_at_elevation
import pytest

from custom_components.night_compensation import curve

TZ = ZoneInfo("Europe/Amsterdam")
OBSERVER = Observer(52.0, 5.0)  # generic central-Netherlands location


def test_season_runs_from_june_to_december():
    assert curve.season(date(2026, 6, 21)) == pytest.approx(-1, abs=0.01)
    assert curve.season(date(2026, 12, 21)) == pytest.approx(1, abs=0.01)
    assert abs(curve.season(date(2026, 3, 20))) < 0.05


def test_winter_bias_lowers_effective_sun_in_winter():
    assert curve.effective_elevation(10, 3, date(2026, 12, 21)) == pytest.approx(7, abs=0.05)
    assert curve.effective_elevation(10, 3, date(2026, 6, 21)) == pytest.approx(13, abs=0.05)


def test_elevation_from_lux():
    assert curve.elevation_from_lux(2000) == pytest.approx(2.7, abs=0.05)
    assert curve.elevation_from_lux(600) == pytest.approx(0)
    assert curve.elevation_from_lux(0) == -18
    assert curve.elevation_from_lux(1e6) == 60


BOOST = curve.Curve(evening_start=15, evening_full=12, boost=True, peak=100, hold_until=-6, settle_end=-12, cosy=60, morning_start=12, morning_off=15)


@pytest.mark.parametrize(("e", "level", "phase"), [(20, 0, "day"), (13.5, 50, "evening_ramp"), (5, 100, "peak"), (-9, 80, "settle"), (-15, 60, "cosy")])
def test_evening_with_boost(e, level, phase):
    assert curve.evening_level(BOOST, e) == pytest.approx((level, phase))


def test_evening_without_boost_goes_straight_to_cosy():
    plain = curve.Curve(boost=False, cosy=60)
    assert curve.evening_level(plain, 13.5) == pytest.approx((30, "evening_ramp"))
    assert curve.evening_level(plain, 0) == (60, "cosy")


@pytest.mark.parametrize(("e", "level", "phase"), [(-5, 60, "cosy"), (13.5, 30, "morning_ramp"), (20, 0, "day")])
def test_morning_has_no_boost(e, level, phase):
    assert curve.morning_level(BOOST, e) == pytest.approx((level, phase))


def test_caps_in_december_shift_the_evening_ramp():
    day = date(2026, 12, 21)
    highest = elevation(OBSERVER, noon(OBSERVER, day, TZ))
    lowest = elevation(OBSERVER, midnight(OBSERVER, day, TZ))
    capped, active = curve.capped(BOOST, highest, lowest)
    assert active
    assert capped.evening_start == pytest.approx(highest - 1)
    assert capped.evening_start - capped.evening_full == pytest.approx(3)
    assert capped.morning_off == pytest.approx(highest - 1)
    assert (capped.hold_until, capped.settle_end) == (-6, -12)


def test_caps_in_june_lift_an_unreachable_settle_end():
    deep = curve.Curve(boost=True, hold_until=-10, settle_end=-16)
    capped, active = curve.capped(deep, 61.5, -14.5)
    assert active
    assert capped.settle_end == pytest.approx(-13.5)
    assert capped.hold_until == pytest.approx(-7.5)


def test_no_caps_at_the_equinox():
    assert curve.capped(BOOST, 38.0, -38.0) == (BOOST, False)


def test_example_timeline_3_november():
    """Design §4.3: phases at the computed crossing times."""
    day = date(2026, 11, 3)
    at = lambda e: time_at_elevation(OBSERVER, e, day, SunDirection.SETTING, TZ)
    level = lambda moment: curve.evening_level(BOOST, elevation(OBSERVER, moment))
    assert level(at(15) - timedelta(minutes=5))[1] == "day"
    assert level(at(13.5))[1] == "evening_ramp"
    assert level(at(12) + timedelta(minutes=5))[1] == "peak"
    assert level(at(-9))[0] == pytest.approx(80, abs=1)
    assert level(at(-12) + timedelta(minutes=5)) == (60, "cosy")


SCHEDULE = curve.Schedule(off_weekday=time(0), on_weekday=time(7), off_weekend=time(2), on_weekend=time(10), fade=30)


@pytest.mark.parametrize(("moment", "level", "phase"), [
    ("2026-11-02 23:30", 100, None),            # Monday evening
    ("2026-11-03 00:15", 50, "bedtime_fade"),   # Monday night, weekday off 00:00
    ("2026-11-03 03:00", 0, "night"),
    ("2026-11-03 07:15", 50, "wake_fade"),
    ("2026-11-06 23:30", 100, None),            # Friday evening: weekend times
    ("2026-11-07 01:00", 100, None),            # Friday night, weekend off 02:00
    ("2026-11-07 08:00", 0, "night"),           # weekend on 10:00
    ("2026-11-08 23:30", 100, None),            # Sunday evening: weekday times
    ("2026-11-09 00:15", 50, "bedtime_fade"),
])
def test_schedule(moment, level, phase):
    now = datetime.fromisoformat(moment).replace(tzinfo=TZ)
    assert curve.schedule_level(SCHEDULE, now) == pytest.approx((level, phase))


def test_schedule_off_before_midnight_stays_off():
    """The current templates turn lights back on after the fade when the off time is before midnight."""
    early = curve.Schedule(off_weekday=time(23), on_weekday=time(7), off_weekend=time(23), on_weekend=time(7), fade=30)
    assert curve.schedule_level(early, datetime(2026, 11, 2, 23, 45, tzinfo=TZ))[0] == 0


def test_zone_level_priorities():
    common = dict(override_level=80, peak=100, sleep_behaviour="off", sleep_level=0)
    assert curve.zone_level(40, "cosy", mode="pause", sleep_on=True, **common) == (None, "paused")
    assert curve.zone_level(40, "cosy", mode="boost", sleep_on=True, **common) == (0, "sleep")
    assert curve.zone_level(40, "cosy", mode="boost", sleep_on=False, **common) == (100, "boost")
    assert curve.zone_level(40, "cosy", mode="level", sleep_on=False, **common) == (80, "override")
    assert curve.zone_level(40, "cosy", mode="auto", sleep_on=False, **common) == (40, "cosy")
    assert curve.zone_level(40, "cosy", mode="auto", sleep_on=True, override_level=80, peak=100, sleep_behaviour="ignore", sleep_level=0) == (40, "cosy")


def test_light_brightness():
    assert curve.light_brightness(60, 50, 0) == 30
    assert curve.light_brightness(1, 50, 5) == 5
    assert curve.light_brightness(0, 50, 5) == 0


# --- 1:1 comparison with the current template sensors -------------------------------------------------------------

def old_sun_factor(e: float, on: float, off: float) -> float:
    a = 1 / (on - off)
    return max(min(a * e - a * off, 1), 0)


def old_time_factor(now: datetime) -> float:
    weekday = now.weekday() < 5
    off, on = (0, 7 * 3600) if weekday else (2 * 3600, 10 * 3600)
    t, dim = now.hour * 3600 + now.minute * 60 + now.second, 30 * 60
    if t <= off:
        return 1
    if t <= off + dim:
        return 1 - (t - off) / dim
    if t < on:
        return 0
    if t <= on + dim:
        return (t - on) / dim
    return 1


@pytest.mark.parametrize(("on", "off"), [(12, 15), (-10, 0)])
def test_sun_factor_matches_templates_over_a_year(on, off):
    port = curve.Curve(evening_start=off, evening_full=on, boost=False, cosy=100, morning_start=on, morning_off=off)
    start = datetime(2026, 1, 1, tzinfo=TZ)
    for step in range(0, 365 * 24 * 4, 7):  # every 1h45m through the year
        now = start + timedelta(minutes=15 * step)
        e = elevation(OBSERVER, now)
        level_fn = curve.morning_level if now < noon(OBSERVER, now.date(), TZ) else curve.evening_level
        assert level_fn(port, e)[0] == pytest.approx(100 * old_sun_factor(e, on, off), abs=1e-6), now


def test_schedule_matches_templates_over_a_week():
    start = datetime(2026, 11, 2, tzinfo=TZ)  # Monday
    for minute in range(0, 7 * 24 * 60, 5):
        now = start + timedelta(minutes=minute)
        assert curve.schedule_level(SCHEDULE, now)[0] == pytest.approx(100 * old_time_factor(now), abs=1e-6), now
