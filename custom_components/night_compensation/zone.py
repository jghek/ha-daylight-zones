"""One controller per zone: computes the zone level and drives its lights."""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
import math
from typing import Any

from astral import Observer
from astral.sun import elevation, midnight, noon

from homeassistant.const import ATTR_ENTITY_ID, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Context, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from . import curve
from .const import (
    CONF_LIGHTS, CONF_LUX_SENSOR, CONF_LUX_SMOOTHING, CONF_SLEEP_BEHAVIOUR, CONF_SLEEP_ENTITY, CONF_SOURCE, CONF_USE_BOOST, CONF_USE_SCHEDULE,
    EVENT_PHASE_CHANGED, LUX_FLOOR, LUX_STALE_MINUTES, MANUAL_TOLERANCE, NUMBER_SETTINGS, SLEEP_IGNORE, SOURCE_LUX, STEP_TRANSITION, TIME_SETTINGS, UPDATE_SECONDS,
)

_LOGGER = logging.getLogger(__name__)
RATE_LIMITED_PHASES = {"day", "evening_ramp", "peak", "settle", "cosy", "bedtime_fade", "night", "wake_fade", "morning_ramp"}


@dataclass
class Sent:
    on: bool
    pct: float
    at: datetime
    transition: float


@dataclass
class Snapshot:
    level: float | None = None
    phase: str = "day"
    effective_elevation: float | None = None
    curve_level: float | None = None
    schedule_level: float | None = None
    source: str = "sun"
    caps_active: bool = False


class ZoneController:
    def __init__(self, hass: HomeAssistant, subentry_id: str, name: str, config: dict[str, Any]) -> None:
        self.hass = hass
        self.subentry_id = subentry_id
        self.name = name
        self.config = config
        self.lights: dict[str, dict[str, float]] = config[CONF_LIGHTS]
        self.settings: dict[str, Any] = {s.key: s.default for s in NUMBER_SETTINGS} | {key: default for key, _, default in TIME_SETTINGS}
        self.mode = "auto"
        self.snapshot = Snapshot()
        self._override: dict[str, Any] = {}
        self._paused: dict[str, str] = {}  # light -> base phase when paused
        self._sent: dict[str, Sent] = {}
        self._contexts: deque[str] = deque(maxlen=100)
        self._lux: deque[tuple[datetime, float]] = deque()
        self._output: float | None = None
        self._last_kind: str | None = None
        self._last_run: datetime | None = None
        self._base_phase: str | None = None
        self._day_cache: tuple[date, float, float, datetime] | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._listeners: list[Callable[[], None]] = []
        self._started = False

    # --- lifecycle -----------------------------------------------------------------------------------------------

    @callback
    def async_start(self) -> None:
        self._started = True
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=UPDATE_SECONDS)))
        if sleep := self.config.get(CONF_SLEEP_ENTITY):
            self._unsubs.append(async_track_state_change_event(self.hass, [sleep], self._input_changed))
        if self.config[CONF_SOURCE] == SOURCE_LUX and (lux := self.config.get(CONF_LUX_SENSOR)):
            self._unsubs.append(async_track_state_change_event(self.hass, [lux], self._lux_changed))
        self._unsubs.append(async_track_state_change_event(self.hass, list(self.lights), self._light_changed))
        self.async_update()

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # --- inputs from entities and actions ------------------------------------------------------------------------

    @callback
    def set_setting(self, key: str, value: Any) -> None:
        self.settings[key] = value
        if self._started:
            self.async_update()

    @callback
    def set_override(self, mode: str, level: float | None = None, until: str = "noon", duration: timedelta | None = None) -> None:
        if mode == "auto":
            self.clear_override()
            return
        if level is not None:
            self.settings["override_level"] = level
        now = dt_util.now()
        deadline = None
        if until == "noon":
            deadline = self._next_noon(now)
        elif until == "duration":
            deadline = now + (duration or timedelta(hours=1))
        self.mode = mode
        self._override = {"until": until, "deadline": deadline, "phase": self._base_phase}
        self.async_update()

    @callback
    def clear_override(self) -> None:
        self.mode = "auto"
        self._override = {}
        self._paused.clear()
        self.async_update()

    @callback
    def _tick(self, _now: datetime) -> None:
        self.async_update()

    @callback
    def _input_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.async_update()

    @callback
    def _lux_changed(self, event: Event[EventStateChangedData]) -> None:
        if (value := _number(event.data["new_state"])) is not None:
            self._lux.append((dt_util.now(), value))
        self.async_update()

    @callback
    def _light_changed(self, event: Event[EventStateChangedData]) -> None:
        """Pause a light that somebody else changed, until the zone's next phase."""
        entity_id, old, new = event.data["entity_id"], event.data["old_state"], event.data["new_state"]
        if old is None or new is None:
            return
        if new.state in (STATE_UNAVAILABLE, STATE_UNKNOWN) or old.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._sent.pop(entity_id, None)
            return
        if new.context.id in self._contexts or new.context.parent_id in self._contexts:
            return
        if (old.state, old.attributes.get("brightness")) == (new.state, new.attributes.get("brightness")):
            return
        sent = self._sent.get(entity_id)
        if sent is not None:
            if dt_util.now() - sent.at < timedelta(seconds=sent.transition + 5):
                return
            if sent.on == (new.state == STATE_ON) and (not sent.on or abs(sent.pct - _brightness_pct(new)) <= MANUAL_TOLERANCE):
                return
        if entity_id not in self._paused and self.mode != "pause":
            _LOGGER.debug("%s: %s changed by hand, paused until the next phase", self.name, entity_id)
            self._paused[entity_id] = self._base_phase or ""
            self._notify()

    # --- computation ---------------------------------------------------------------------------------------------

    @callback
    def async_update(self) -> None:
        now = dt_util.now()
        day = now.date()
        observer = Observer(self.hass.config.latitude, self.hass.config.longitude)
        noon_at, highest, lowest = self._day_extremes(observer, day)
        bias = self.settings["winter_bias"]
        e_model = curve.effective_elevation(elevation(observer, now), bias, day)
        e, source = e_model, "sun"
        if self.config[CONF_SOURCE] == SOURCE_LUX and (lux := self._lux_mean(now)) is not None:
            e, source = min(curve.effective_elevation(curve.elevation_from_lux(lux), bias, day), e_model), "lux"

        zone_curve = curve.Curve(**{k: self.settings[k] for k in ("evening_start", "evening_full", "peak", "hold_until", "settle_end", "cosy", "morning_start", "morning_off")}, boost=self.config[CONF_USE_BOOST])
        zone_curve, caps = curve.capped(zone_curve, curve.effective_elevation(highest, bias, day), curve.effective_elevation(lowest, bias, day))
        curve_pct, curve_phase = (curve.morning_level if now < noon_at else curve.evening_level)(zone_curve, e)
        if self.config[CONF_USE_SCHEDULE]:
            schedule = curve.Schedule(**{k: self.settings[k] for k, _, _ in TIME_SETTINGS}, fade=self.settings["bedtime_fade"])
            schedule_pct, schedule_phase = curve.schedule_level(schedule, now)
        else:
            schedule_pct, schedule_phase = 100.0, None
        base_pct, base_phase = curve.base_level(curve_pct, curve_phase, schedule_pct, schedule_phase)
        previous_base = self._base_phase
        self._base_phase = base_phase

        sleep_on = self._sleep_on()
        sleep_behaviour = self.config.get(CONF_SLEEP_BEHAVIOUR, SLEEP_IGNORE)
        self._expire_override(now, base_phase, sleep_on and sleep_behaviour != SLEEP_IGNORE)
        if previous_base is not None and base_phase != previous_base:
            self._paused.clear()

        level, phase = curve.zone_level(base_pct, base_phase, mode=self.mode, override_level=self.settings["override_level"], peak=zone_curve.peak if zone_curve.boost else 100.0,
                                        sleep_on=sleep_on, sleep_behaviour=sleep_behaviour, sleep_level=self.settings["sleep_level"])
        if phase == "sleep":
            self._paused.clear()  # sleep mode wins over manual changes
        self._output = self._rate_limited(level, phase, now)
        if self._output is not None and level is not None:
            self._drive(self._output, phase)

        old_phase = self.snapshot.phase
        self.snapshot = Snapshot(self._output, phase, round(e, 2), round(curve_pct, 1), round(schedule_pct, 1), source, caps)
        if old_phase != phase and self._last_run is not None:
            self.hass.bus.async_fire(EVENT_PHASE_CHANGED, {"zone": self.name, "zone_id": self.subentry_id, "from": old_phase, "to": phase})
        self._last_run = now
        self._notify()

    def _rate_limited(self, level: float | None, phase: str, now: datetime) -> float | None:
        kind = "auto" if phase in RATE_LIMITED_PHASES else phase
        bypass = self._output is None or kind != self._last_kind or kind != "auto"
        self._last_kind = kind
        if level is None:
            return self._output
        if bypass:
            return level
        minutes = max((now - self._last_run).total_seconds() / 60, 0) if self._last_run else 1
        step = self.settings["max_step"] * max(minutes, 1 / 60)
        return self._output + max(-step, min(step, level - self._output))

    def _expire_override(self, now: datetime, base_phase: str, sleeping: bool) -> None:
        if self.mode == "auto":
            return
        if sleeping and self.mode != "pause":
            self.mode, self._override = "auto", {}
            return
        until, deadline = self._override.get("until"), self._override.get("deadline")
        if (until == "next_phase" and base_phase != self._override.get("phase")) or (deadline is not None and now >= deadline):
            self.mode, self._override = "auto", {}

    def _drive(self, level: float, phase: str) -> None:
        transition = float(self.settings["sleep_fade"]) if phase == "sleep" else float(STEP_TRANSITION)
        for entity_id, limits in self.lights.items():
            if entity_id in self._paused:
                continue
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                continue
            pct = curve.light_brightness(level, limits["max"], limits.get("min", 0))
            on = pct > 0
            brightness = max(1, round(pct * 2.55)) if on else 0
            sent = self._sent.get(entity_id)
            current_on = sent.on if sent is not None else state.state == STATE_ON
            current_brightness = round(sent.pct * 2.55) if sent is not None else round(_brightness_pct(state) * 2.55)
            if current_on == on and (not on or max(1, current_brightness) == brightness):
                continue  # send only when on/off or the 0-255 brightness value changes
            context = Context()
            self._contexts.append(context.id)
            self._sent[entity_id] = Sent(on, brightness / 2.55, dt_util.now(), transition)
            data: dict[str, Any] = {ATTR_ENTITY_ID: entity_id, "transition": transition}
            if on:
                data["brightness"] = brightness
            self.hass.async_create_task(self.hass.services.async_call("light", "turn_on" if on else "turn_off", data, context=context), eager_start=True)

    # --- helpers -------------------------------------------------------------------------------------------------

    def _day_extremes(self, observer: Observer, day: date) -> tuple[datetime, float, float]:
        if self._day_cache is None or self._day_cache[0] != day:
            tz = dt_util.get_default_time_zone()
            noon_at = noon(observer, day, tz)
            self._day_cache = (day, elevation(observer, noon_at), elevation(observer, midnight(observer, day, tz)), noon_at)
        _, highest, lowest, noon_at = self._day_cache
        return noon_at, highest, lowest

    def _next_noon(self, now: datetime) -> datetime:
        observer = Observer(self.hass.config.latitude, self.hass.config.longitude)
        today = noon(observer, now.date(), dt_util.get_default_time_zone())
        return today if now < today else noon(observer, now.date() + timedelta(days=1), dt_util.get_default_time_zone())

    def _sleep_on(self) -> bool:
        entity = self.config.get(CONF_SLEEP_ENTITY)
        return bool(entity) and (state := self.hass.states.get(entity)) is not None and state.state == STATE_ON

    def _lux_mean(self, now: datetime) -> float | None:
        """Smoothed lux (mean of log10 over the smoothing window), or None to fall back to the sun model."""
        if (value := _number(self.hass.states.get(self.config.get(CONF_LUX_SENSOR) or ""))) is not None:
            self._lux.append((now, value))
        window = timedelta(minutes=self.config.get(CONF_LUX_SMOOTHING, 10))
        while self._lux and now - self._lux[0][0] > max(window, timedelta(minutes=LUX_STALE_MINUTES)):
            self._lux.popleft()
        recent = [v for t, v in self._lux if now - t <= window]
        if not recent or now - self._lux[-1][0] > timedelta(minutes=LUX_STALE_MINUTES):
            return None
        mean = 10 ** (sum(math.log10(max(v, 1e-4)) for v in recent) / len(recent))
        return mean if mean >= LUX_FLOOR else None

    @property
    def paused_lights(self) -> list[str]:
        return sorted(self._paused)

    @property
    def override(self) -> dict[str, Any]:
        return dict(self._override)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()


def _number(state: State | None) -> float | None:
    try:
        return float(state.state) if state is not None else None
    except ValueError:
        return None


def _brightness_pct(state: State) -> float:
    if state.state != STATE_ON:
        return 0.0
    return (state.attributes.get("brightness") or 255) / 2.55
