"""Pure calculations for night compensation: no Home Assistant imports.

Levels are percentages (0-100), elevations are degrees.
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
import math

# Approximate clear-sky horizontal illuminance by solar elevation (design §3.2).
LUX_TABLE = ((-18.0, 0.0007), (-12.0, 0.008), (-6.0, 3.4), (0.0, 600.0), (5.0, 5500.0), (10.0, 16000.0), (20.0, 38000.0), (30.0, 55000.0), (60.0, 100000.0))
CAP_MARGIN = 1.0
NOON = time(12)


@dataclass(frozen=True)
class Curve:
    evening_start: float = 15.0
    evening_full: float = 12.0
    boost: bool = False
    peak: float = 100.0
    hold_until: float = -6.0
    settle_end: float = -12.0
    cosy: float = 60.0
    morning_start: float = 12.0
    morning_off: float = 15.0


@dataclass(frozen=True)
class Schedule:
    off_weekday: time = time(0)
    on_weekday: time = time(7)
    off_weekend: time = time(2)
    on_weekend: time = time(10)
    fade: float = 30.0  # minutes


def season(day: date) -> float:
    """-1 at the June solstice, 0 at the equinoxes, +1 at the December solstice (from the solar declination)."""
    declination = -23.44 * math.cos(2 * math.pi / 365 * (day.timetuple().tm_yday + 10))
    return -declination / 23.44


def effective_elevation(elevation: float, winter_bias: float, day: date) -> float:
    return elevation - winter_bias * season(day)


def elevation_from_lux(lux: float) -> float:
    """Elevation at which a clear sky gives this illuminance (log-linear in LUX_TABLE, clamped to its ends)."""
    if lux <= LUX_TABLE[0][1]:
        return LUX_TABLE[0][0]
    for (e0, l0), (e1, l1) in zip(LUX_TABLE, LUX_TABLE[1:], strict=False):
        if lux <= l1:
            return e0 + (e1 - e0) * (math.log10(lux) - math.log10(l0)) / (math.log10(l1) - math.log10(l0))
    return LUX_TABLE[-1][0]


def _lerp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def capped(curve: Curve, highest: float, lowest: float) -> tuple[Curve, bool]:
    """Shift thresholds the effective sun cannot reach today into [lowest + 1°, highest - 1°], keeping ramp widths (design §3.3)."""
    hi, lo = highest - CAP_MARGIN, lowest + CAP_MARGIN
    evening = [curve.evening_start, curve.evening_full] + ([curve.hold_until, curve.settle_end] if curve.boost else [])
    if evening[0] > hi:
        shift = evening[0] - hi
        evening[0] -= shift
        evening[1] -= shift
    if evening[-1] < lo:
        shift = lo - evening[-1]
        evening[-1] += shift
        evening[-2] += shift
    evening = [min(max(v, lo), hi) for v in evening]
    for i in range(1, len(evening)):
        evening[i] = min(evening[i], evening[i - 1])
    morning = [curve.morning_start, curve.morning_off]
    if morning[1] > hi:
        shift = morning[1] - hi
        morning = [v - shift for v in morning]
    if morning[0] < lo:
        shift = lo - morning[0]
        morning = [v + shift for v in morning]
    morning = [min(max(v, lo), hi) for v in morning]
    morning[1] = max(morning[1], morning[0])
    result = replace(curve, evening_start=evening[0], evening_full=evening[1], morning_start=morning[0], morning_off=morning[1])
    if curve.boost:
        result = replace(result, hold_until=evening[2], settle_end=evening[3])
    return result, result != curve


def evening_level(curve: Curve, e: float) -> tuple[float, str]:
    peak = curve.peak if curve.boost else curve.cosy
    if e >= curve.evening_start:
        return 0.0, "day"
    if e >= curve.evening_full:
        return _lerp(e, curve.evening_start, curve.evening_full, 0.0, peak), "evening_ramp"
    if not curve.boost:
        return curve.cosy, "cosy"
    if e >= curve.hold_until:
        return curve.peak, "peak"
    if e >= curve.settle_end:
        return _lerp(e, curve.hold_until, curve.settle_end, curve.peak, curve.cosy), "settle"
    return curve.cosy, "cosy"


def morning_level(curve: Curve, e: float) -> tuple[float, str]:
    if e < curve.morning_start:
        return curve.cosy, "cosy"
    if e < curve.morning_off:
        return _lerp(e, curve.morning_start, curve.morning_off, curve.cosy, 0.0), "morning_ramp"
    return 0.0, "day"


def schedule_level(schedule: Schedule, now: datetime) -> tuple[float, str | None]:
    """Bedtime schedule. A night belongs to the evening it started; Friday and Saturday nights use the weekend times."""
    night = now.date() if now.time() >= NOON else now.date() - timedelta(days=1)
    weekend = night.weekday() in (4, 5)
    off_t, on_t = (schedule.off_weekend, schedule.on_weekend) if weekend else (schedule.off_weekday, schedule.on_weekday)
    off = datetime.combine(night + timedelta(days=1 if off_t < NOON else 0), off_t, now.tzinfo)
    on = datetime.combine(night + timedelta(days=1), on_t, now.tzinfo)
    fade = timedelta(minutes=schedule.fade)
    if now < off:
        return 100.0, None
    if now < off + fade:
        return 100.0 * (1 - (now - off) / fade), "bedtime_fade"
    if now < on:
        return 0.0, "night"
    if now < on + fade:
        return 100.0 * (now - on) / fade, "wake_fade"
    return 100.0, None


def base_level(curve_pct: float, curve_phase: str, schedule_pct: float, schedule_phase: str | None) -> tuple[float, str]:
    """Curve × schedule; the schedule phase wins while the schedule is dimming."""
    phase = schedule_phase if schedule_phase is not None and curve_pct > 0 else curve_phase
    return curve_pct * schedule_pct / 100.0, phase


def zone_level(base_pct: float, base_phase: str, *, mode: str, override_level: float, peak: float, sleep_on: bool, sleep_behaviour: str, sleep_level: float) -> tuple[float | None, str]:
    """Apply pause, sleep and overrides in priority order (design §6). None means: leave the lights alone."""
    if mode == "pause":
        return None, "paused"
    if sleep_on and sleep_behaviour != "ignore":
        return (0.0 if sleep_behaviour == "off" else sleep_level), "sleep"
    if mode == "boost":
        return peak, "boost"
    if mode == "max":
        return 100.0, "max"
    if mode == "level":
        return override_level, "override"
    return base_pct, base_phase


def light_brightness(level: float, max_pct: float, min_pct: float) -> float:
    """Brightness % for one light: level × max, raised to min when on, 0 when off."""
    if level <= 0:
        return 0.0
    return max(level * max_pct / 100.0, min_pct)
