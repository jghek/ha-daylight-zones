"""Pure curve tests, including a 1:1 comparison with the template sensors this integration replaces."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from astral import Observer
from astral.sun import SunDirection, elevation, midnight, noon, time_at_elevation
import pytest

from custom_components.daylight_zones import curve

TZ = ZoneInfo("Europe/Amsterdam")
OBSERVER = Observer(52.0, 5.0)  # generic central-Netherlands location


def test_season_runs_from_june_to_december():
    assert curve.season(date(2026, 6, 21)) == pytest.approx(-1, abs=0.01)
    assert curve.season(date(2026, 12, 21)) == pytest.approx(1, abs=0.01)
    assert abs(curve.season(date(2026, 3, 20))) < 0.05


def test_winter_bias_lowers_effective_sun_in_winter():
    assert curve.effective_elevation(10, 3, date(2026, 12, 21)) == pytest.approx(7, abs=0.05)
    assert curve.effective_elevation(10, 3, date(2026, 6, 21)) == pytest.approx(13, abs=0.05)


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


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")
# The current setup: off 00:00-07:00 on weekdays, 02:00-10:00 at the weekend (by the day after midnight).
NIGHTS = {day: [{"from": "00:00:00", "to": "07:00:00"}] for day in WEEKDAYS} | {day: [{"from": time(2), "to": time(10)}] for day in ("saturday", "sunday")}


def blocks(week, now):
    return curve.off_blocks(week, now)


@pytest.mark.parametrize(("moment", "level", "phase"), [
    ("2026-11-02 23:30", 100, None),            # Monday evening
    ("2026-11-03 00:15", 50, "off_fade"),   # Monday night, weekday off 00:00
    ("2026-11-03 03:00", 0, "off_hours"),
    ("2026-11-03 07:15", 50, "on_fade"),
    ("2026-11-06 23:30", 100, None),            # Friday evening: weekend times
    ("2026-11-07 01:00", 100, None),            # Friday night, weekend off 02:00
    ("2026-11-07 08:00", 0, "off_hours"),           # weekend on 10:00
    ("2026-11-08 23:30", 100, None),            # Sunday evening: weekday times
    ("2026-11-09 00:15", 50, "off_fade"),
])
def test_off_window(moment, level, phase):
    now = datetime.fromisoformat(moment).replace(tzinfo=TZ)
    assert curve.off_window_level(blocks(NIGHTS, now), now, 30) == pytest.approx((level, phase))


def test_off_before_midnight_stays_off():
    """The current templates turn lights back on after the fade when the off time is before midnight."""
    early = {day: [{"from": "00:00:00", "to": "07:00:00"}, {"from": "23:00:00", "to": "24:00:00"}] for day in WEEKDAYS + ("saturday", "sunday")}
    late = datetime(2026, 11, 2, 23, 45, tzinfo=TZ)
    assert curve.off_window_level(blocks(early, late), late, 30)[0] == 0
    after_midnight = datetime(2026, 11, 3, 0, 10, tzinfo=TZ)  # 23:00-24:00 and 00:00-07:00 are one block: no new fade at midnight
    assert curve.off_window_level(blocks(early, after_midnight), after_midnight, 30) == (0, "off_hours")


def test_off_blocks_merge_and_accept_time_objects():
    week = {"monday": [{"from": time(22), "to": time.max}], "tuesday": [{"from": time(0), "to": time(6)}]}
    now = datetime(2026, 11, 2, 23, 0, tzinfo=TZ)
    assert (datetime(2026, 11, 2, 22, 0, tzinfo=TZ), datetime(2026, 11, 3, 6, 0, tzinfo=TZ)) in curve.off_blocks(week, now)


OFFICE = {day: [{"from": "22:00:00", "to": "24:00:00"}] for day in WEEKDAYS + ("saturday", "sunday")}


def test_next_off_is_the_first_off_moment_after_switching_on():
    at = lambda moment: datetime.fromisoformat(moment).replace(tzinfo=TZ)
    assert curve.next_off(blocks(OFFICE, at("2026-11-04 08:30")), at("2026-11-04 08:30")) == at("2026-11-04 22:00")
    assert curve.next_off(blocks(OFFICE, at("2026-11-04 23:10")), at("2026-11-04 23:10")) == at("2026-11-05 22:00")  # working late: not again tonight
    assert curve.next_off(blocks(NIGHTS, at("2026-11-04 08:30")), at("2026-11-04 08:30")) == at("2026-11-05 00:00")
    assert curve.next_off([], at("2026-11-04 08:30")) is None


@pytest.mark.parametrize(("moment", "level", "phase", "switch_off"), [
    ("2026-11-04 21:59", 100, None, False),
    ("2026-11-04 22:05", 66.67, "off_fade", False),  # the fade starts at the off time
    ("2026-11-04 22:15", 0, "off_fade", True),
])
def test_switch_off(moment, level, phase, switch_off):
    now = datetime.fromisoformat(moment).replace(tzinfo=TZ)
    assert curve.switch_off_level(blocks(OFFICE, now), now, datetime(2026, 11, 4, 8, 30, tzinfo=TZ), 15) == pytest.approx((level, phase, switch_off), abs=0.01)


def test_switch_off_after_working_late_waits_for_tomorrow():
    switched_on = datetime(2026, 11, 4, 23, 10, tzinfo=TZ)
    now = datetime(2026, 11, 4, 23, 30, tzinfo=TZ)
    assert curve.switch_off_level(blocks(OFFICE, now), now, switched_on, 15) == (100, None, False)


CT = curve.ColorTemp(day=5000, dusk=3500, night=2400, day_elevation=20, night_elevation=-8)


@pytest.mark.parametrize(("e", "kelvin"), [(40, 5000), (20, 5000), (10, 4250), (0, 3500), (-4, 2950), (-8, 2400), (-30, 2400)])
def test_color_temp_follows_elevation(e, kelvin):
    assert curve.color_temp(CT, e) == pytest.approx(kelvin)


def test_base_level_off_hours_phase_wins_while_dimming():
    assert curve.base_level(60, "cosy", 50, "off_fade") == (30, "off_fade")
    assert curve.base_level(0, "day", 0, "off_hours") == (0, "day")
    assert curve.base_level(80, "on", 100, None) == (80, "on")


def test_dimmer_is_linear_from_min_to_scale():
    assert curve.dimmer_brightness(60, 50, 0) == 30
    assert curve.dimmer_brightness(100, 40, 10) == 40
    assert curve.dimmer_brightness(50, 40, 10) == 25
    assert curve.dimmer_brightness(1, 40, 10) == pytest.approx(10.3)
    assert curve.dimmer_brightness(0, 40, 10) == 0


def test_on_off_threshold_with_margin():
    assert curve.on_off(30, 30, False, 2)
    assert not curve.on_off(29, 30, False, 2)
    assert curve.on_off(29, 30, True, 2)  # within the margin: stays on
    assert not curve.on_off(27.9, 30, True, 2)
    assert not curve.on_off(0, 0, True, 2)


def test_light_settings_read_the_older_format():
    from custom_components.daylight_zones.const import light_settings
    assert light_settings({"max": 40, "min": 5}) == ("dimmer", 40, 5, 50)
    assert light_settings({"type": "on_off", "threshold": 20}) == ("on_off", 100, 0, 20)


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
        assert curve.off_window_level(blocks(NIGHTS, now), now, 30)[0] == pytest.approx(100 * old_time_factor(now), abs=1e-6), now
