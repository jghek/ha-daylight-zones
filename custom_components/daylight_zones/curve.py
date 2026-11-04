"""Pure calculations for Daylight Zones: no Home Assistant imports.

Levels are percentages (0-100), elevations are degrees, colour temperatures are kelvin.
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
import math

CAP_MARGIN = 1.0


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
class ColorTemp:
    day: float = 5000.0      # K at day_elevation and above
    dusk: float = 3500.0     # K at 0°
    night: float = 2400.0    # K at night_elevation and below
    day_elevation: float = 20.0
    night_elevation: float = -8.0


def season(day: date) -> float:
    """-1 at the June solstice, 0 at the equinoxes, +1 at the December solstice (from the solar declination)."""
    declination = -23.44 * math.cos(2 * math.pi / 365 * (day.timetuple().tm_yday + 10))
    return -declination / 23.44


def effective_elevation(elevation: float, winter_bias: float, day: date) -> float:
    return elevation - winter_bias * season(day)


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


def color_temp(c: ColorTemp, e: float) -> float:
    """Kelvin for an effective elevation: night below night_elevation, dusk at 0°, day above day_elevation."""
    if e >= c.day_elevation:
        return c.day
    if e >= 0:
        return _lerp(e, 0.0, c.day_elevation, c.dusk, c.day)
    if e > c.night_elevation:
        return _lerp(e, c.night_elevation, 0.0, c.night, c.dusk)
    return c.night


DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _as_time(value) -> time | None:
    """A schedule time: a time object, or 'HH:MM[:SS]' where '24:00' means end of day (None)."""
    if isinstance(value, time):
        return None if value == time.max else value
    text = str(value)
    return None if text.startswith("24:00") else time.fromisoformat(text)


def off_blocks(week: dict[str, list[dict]], around: datetime, days: int = 8) -> list[tuple[datetime, datetime]]:
    """The schedule's blocks from `days` before to `days` after `around`, as datetimes; blocks that touch (24:00 → 00:00) are merged."""
    blocks: list[tuple[datetime, datetime]] = []
    first = around.date() - timedelta(days=days)
    for offset in range(2 * days + 1):
        day = first + timedelta(days=offset)
        for block in sorted(week.get(DAYS[day.weekday()]) or [], key=lambda b: str(b["from"])):
            start = datetime.combine(day, _as_time(block["from"]) or time(0), around.tzinfo)
            to = _as_time(block["to"])
            end = datetime.combine(day + timedelta(days=1), time(0), around.tzinfo) if to is None else datetime.combine(day, to, around.tzinfo)
            if blocks and blocks[-1][1] >= start:
                blocks[-1] = (blocks[-1][0], max(blocks[-1][1], end))
            else:
                blocks.append((start, end))
    return blocks


def off_window_level(blocks: list[tuple[datetime, datetime]], now: datetime, fade_minutes: float) -> tuple[float, str | None]:
    """Off hours with an end: fade to 0 from the start of a block, stay 0, fade back up from its end."""
    fade = timedelta(minutes=fade_minutes)
    for start, end in blocks:
        if start <= now < end:
            return (100.0 * (1 - (now - start) / fade), "off_fade") if now - start < fade else (0.0, "off_hours")
    ended = [end for _, end in blocks if end <= now]
    if ended and now - max(ended) < fade:
        return 100.0 * (now - max(ended)) / fade, "on_fade"
    return 100.0, None


def next_off(blocks: list[tuple[datetime, datetime]], after: datetime) -> datetime | None:
    """First block start strictly after `after` (off hours without an end: the zone switches off then)."""
    return next((start for start, _ in blocks if start > after), None)


def switch_off_level(blocks: list[tuple[datetime, datetime]], now: datetime, auto_on_since: datetime, fade_minutes: float) -> tuple[float, str | None, bool]:
    """Off hours without an end: from the first block start after the zone was switched on, fade out, then switch off.

    Like the window mode, the fade starts at the block start. Returns (level, phase, switch_off_now).
    """
    moment = next_off(blocks, auto_on_since)
    fade = timedelta(minutes=fade_minutes)
    if moment is None or now < moment:
        return 100.0, None, False
    if now < moment + fade:
        return 100.0 * (1 - (now - moment) / fade), "off_fade", False
    return 0.0, "off_fade", True


def base_level(curve_pct: float, curve_phase: str, hours_pct: float, hours_phase: str | None) -> tuple[float, str]:
    """Curve (or fixed level) × off hours; the off-hours phase wins while it is dimming."""
    phase = hours_phase if hours_phase is not None and curve_pct > 0 else curve_phase
    return curve_pct * hours_pct / 100.0, phase


def dimmer_brightness(level: float, scale: float, minimum: float) -> float:
    """Brightness % for a dimmer: 0 stays off, 1-100 % maps linearly onto minimum-scale (y = minimum + level × (scale − minimum) / 100)."""
    if level <= 0:
        return 0.0
    return minimum + level * (scale - minimum) / 100.0


def on_off(level: float, threshold: float, was_on: bool, margin: float) -> bool:
    """An on/off light: on at or above the threshold, off below threshold − margin, unchanged in between."""
    if level <= 0:
        return False
    if level >= threshold:
        return True
    return was_on and level >= threshold - margin
