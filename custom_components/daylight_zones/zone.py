"""One controller per zone: computes the zone's level and colour temperature and drives its lights."""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from typing import Any

from astral import Observer
from astral.sun import elevation, midnight, noon

from homeassistant.const import ATTR_ENTITY_ID, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Context, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from . import curve
from .const import (
    BRIGHTNESS_DAYLIGHT, COLOR_TEMP_SEND, COLOR_TEMP_STEP, CONF_BRIGHTNESS, CONF_LIGHTS, CONF_OFF_HOURS,
    CONF_USE_BOOST, CONF_USE_COLOR_TEMP, EVENT_PHASE_CHANGED, MANUAL_TOLERANCE, MANUAL_TOLERANCE_K, NUMBER_SETTINGS, OTHER_SETTINGS,
    OFF_HOURS_SWITCH_OFF, OFF_HOURS_WINDOW, OFF_TRANSITION, STEP_TRANSITION, TIME_SETTINGS, UPDATE_SECONDS,
    CONF_OFF_SCHEDULE, LIGHT_ON_OFF, ON_OFF_MARGIN, light_settings,
)

_LOGGER = logging.getLogger(__name__)
CURVE_KEYS = ("evening_start", "evening_full", "peak", "hold_until", "settle_end", "cosy", "morning_start", "morning_off")


@dataclass
class Sent:
    on: bool
    pct: float
    kelvin: float | None
    at: datetime
    transition: float


@dataclass
class Snapshot:
    level: float | None = None
    color_temp: float | None = None
    phase: str = "day"
    effective_elevation: float | None = None
    curve_level: float | None = None
    off_hours_level: float | None = None
    caps_active: bool = False


class ZoneController:
    def __init__(self, hass: HomeAssistant, subentry_id: str, name: str, config: dict[str, Any]) -> None:
        self.hass = hass
        self.subentry_id = subentry_id
        self.name = name
        self.config = config
        self.lights: dict[str, dict[str, float]] = config[CONF_LIGHTS]
        self.settings: dict[str, Any] = {s.key: s.default for s in NUMBER_SETTINGS} | {key: default for key, _, default, _ in TIME_SETTINGS} | dict(OTHER_SETTINGS)
        self.auto = True
        self.auto_since = dt_util.now()
        self.override = False
        self._override_end: dict[str, Any] = {}
        self.snapshot = Snapshot()
        self._paused: dict[str, str] = {}  # light -> base phase when paused
        self._sent: dict[str, Sent] = {}
        self._contexts: deque[str] = deque(maxlen=100)
        self._level: float | None = None
        self._kelvin: float | None = None
        self._last_kind: str | None = None
        self._last_run: datetime | None = None
        self._base_phase: str | None = None
        self._day_cache: tuple[date, float, float, datetime] | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._listeners: list[Callable[[], None]] = []
        self._started = False
        self._week: dict[str, list[dict]] = {}

    # --- lifecycle -----------------------------------------------------------------------------------------------

    @callback
    async def async_start(self) -> None:
        """Load the off-hours schedule first, so a restart at night never switches lights on for a moment."""
        self._started = True
        if schedule := self.config.get(CONF_OFF_SCHEDULE):
            await self._async_load_schedule()
            self._unsubs.append(async_track_state_change_event(self.hass, [schedule], self._schedule_changed))
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=UPDATE_SECONDS)))
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
    def restore_auto(self, on: bool, since: datetime) -> None:
        """Restore the auto switch after a restart, without touching the lights."""
        self.auto, self.auto_since = on, since

    @callback
    def restore_override(self, until: str | None, deadline: datetime | None, phase: str | None, level: float | None = None) -> None:
        """Restore an active override after a restart; an end that passed meanwhile ends it on the first update."""
        self.override = True
        self._override_end = {"until": until, "deadline": deadline, "phase": phase, "level": level}

    @callback
    def set_setting(self, key: str, value: Any) -> None:
        self.settings[key] = value
        if self._started:
            self.async_update()

    @callback
    def set_auto(self, on: bool, transition: float | None = None) -> None:
        """Auto on: follow the sun and manage the lights. Auto off: lights off, then leave them alone (until auto back on, if set)."""
        now = dt_util.now()
        if on != self.auto:
            self.auto, self.auto_since = on, now
            self._paused.clear()
            self._last_kind = None
            if not on and not self.override:
                self._all_off(OFF_TRANSITION if transition is None else transition)
        self.async_update()

    @callback
    def toggle_auto(self) -> None:
        self.set_auto(not self.auto)

    @callback
    def set_override(self, on: bool, level: float | None = None, until: str = "noon", duration: timedelta | None = None) -> None:
        """Override on: the zone's lights at the override level, also when auto is off."""
        now = dt_util.now()
        was = self.override
        self.override = on
        self._override_end = {}
        if on:
            deadline = self._next_noon(now) if until == "noon" else now + (duration or timedelta(hours=1)) if until == "duration" else None
            self._override_end = {"until": until, "deadline": deadline, "phase": self._base_phase, "level": level}  # a level here is for this override only
        self._paused.clear()
        self._last_kind = None
        if was and not on and not self.auto:
            self._all_off(OFF_TRANSITION)
        self.async_update()

    @callback
    def _tick(self, _now: datetime) -> None:
        self.async_update()

    async def _async_load_schedule(self) -> None:
        """The schedule's blocks per day, through schedule.get_schedule; an unreadable schedule means no off hours."""
        entity_id = self.config[CONF_OFF_SCHEDULE]
        try:
            response = await self.hass.services.async_call("schedule", "get_schedule", {ATTR_ENTITY_ID: entity_id}, blocking=True, return_response=True)
            self._week = dict((response or {}).get(entity_id) or {})
        except HomeAssistantError as err:
            _LOGGER.warning("%s: cannot read off-hours schedule %s: %s", self.name, entity_id, err)
            self._week = {}

    @callback
    def _schedule_changed(self, _event: Event[EventStateChangedData]) -> None:
        """Also fires when the schedule is edited (its next_event changes)."""
        self.hass.async_create_task(self._async_reload_schedule(), eager_start=True)

    async def _async_reload_schedule(self) -> None:
        await self._async_load_schedule()
        self.async_update()

    @callback
    def _light_changed(self, event: Event[EventStateChangedData]) -> None:
        """Pause a light that somebody else changed, until the zone's next phase (or until auto or override toggles)."""
        entity_id, old, new = event.data["entity_id"], event.data["old_state"], event.data["new_state"]
        if old is None or new is None:
            return
        if new.state in (STATE_UNAVAILABLE, STATE_UNKNOWN) or old.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._sent.pop(entity_id, None)
            return
        if new.context.id in self._contexts or new.context.parent_id in self._contexts:
            return
        if _look(old) == _look(new) or not (self.auto or self.override):
            return
        sent = self._sent.get(entity_id)
        if sent is not None:
            if dt_util.now() - sent.at < timedelta(seconds=sent.transition + 5):
                return
            if sent.on == (new.state == STATE_ON) and (not sent.on or _matches(sent, new)):
                return
        if entity_id not in self._paused:
            _LOGGER.debug("%s: %s changed by hand, paused", self.name, entity_id)
            self._paused[entity_id] = self._base_phase or ""
            self._notify()

    # --- computation ---------------------------------------------------------------------------------------------

    @callback
    def async_update(self) -> None:
        now = dt_util.now()
        if not self.auto and (back_on := self.auto_until) is not None and now >= back_on:
            self.auto, self.auto_since = True, now
            self._last_kind = None
        if self.override and self._override_expired(now):
            self.override, self._override_end = False, {}
            self._last_kind = None
            if not self.auto:
                self._all_off(OFF_TRANSITION)

        e, caps, curve_pct, curve_phase = self._sun(now)
        hours_pct, hours_phase = 100.0, None
        blocks = curve.off_blocks(self._week, now)
        if self.config[CONF_OFF_HOURS] == OFF_HOURS_WINDOW:
            hours_pct, hours_phase = curve.off_window_level(blocks, now, self.settings["off_fade"])
        elif self.config[CONF_OFF_HOURS] == OFF_HOURS_SWITCH_OFF and self.auto:
            hours_pct, hours_phase, switch_off = curve.switch_off_level(blocks, now, self.auto_since, self.settings["off_fade"])
            if switch_off:
                self.auto, self.auto_since = False, now
                self._paused.clear()
                if not self.override:
                    self._all_off(STEP_TRANSITION)
        base_pct, base_phase = curve.base_level(curve_pct, curve_phase, hours_pct, hours_phase)
        if self._base_phase is not None and base_phase != self._base_phase:
            self._paused.clear()
        self._base_phase = base_phase

        if self.override:
            target, phase = (self._override_end.get("level") if self._override_end.get("level") is not None else self.settings["override_level"]), "override"
        elif self.auto:
            target, phase = base_pct, base_phase
        else:
            target, phase = None, "auto_off"
        kelvin = curve.color_temp(self._color_temp(), e) if self.config[CONF_USE_COLOR_TEMP] else None
        self._rate_limit(target, kelvin, phase, now)
        if target is not None:
            self._drive(self._level, self._kelvin)

        old_phase = self.snapshot.phase
        self.snapshot = Snapshot(self._level if target is not None else None, self._kelvin, phase, round(e, 2), round(curve_pct, 1), round(hours_pct, 1), caps)
        if old_phase != phase and self._last_run is not None:
            self.hass.bus.async_fire(EVENT_PHASE_CHANGED, {"zone": self.name, "zone_id": self.subentry_id, "from": old_phase, "to": phase})
        self._last_run = now
        self._notify()

    def _sun(self, now: datetime) -> tuple[float, bool, float, str]:
        """Effective elevation, whether caps are active, and the brightness curve level and phase."""
        day = now.date()
        observer = self._observer()
        noon_at, highest, lowest = self._day_extremes(observer, day)
        bias = self.settings["winter_bias"]
        e = curve.effective_elevation(elevation(observer, now), bias, day)
        if self.config[CONF_BRIGHTNESS] != BRIGHTNESS_DAYLIGHT:
            return e, False, self.settings["level"], "on"
        zone_curve = curve.Curve(**{k: self.settings[k] for k in CURVE_KEYS}, boost=self.config[CONF_USE_BOOST])
        zone_curve, caps = curve.capped(zone_curve, curve.effective_elevation(highest, bias, day), curve.effective_elevation(lowest, bias, day))
        pct, phase = (curve.morning_level if now < noon_at else curve.evening_level)(zone_curve, e)
        return e, caps, pct, phase

    def _rate_limit(self, target: float | None, kelvin: float | None, phase: str, now: datetime) -> None:
        """Move level and colour temperature gradually; jump on start, override and auto changes."""
        kind = "override" if phase == "override" else "auto"
        jump = self._level is None or kind != self._last_kind
        self._last_kind = kind
        minutes = max((now - self._last_run).total_seconds() / 60, 1 / 60) if self._last_run else 1
        if target is not None:
            step = self.settings["max_step"] * minutes
            self._level = target if jump or kind == "override" else self._level + max(-step, min(step, target - self._level))
        if kelvin is not None:
            step = COLOR_TEMP_STEP * minutes
            self._kelvin = kelvin if self._kelvin is None or jump else self._kelvin + max(-step, min(step, kelvin - self._kelvin))

    def _override_expired(self, now: datetime) -> bool:
        until, deadline = self._override_end.get("until"), self._override_end.get("deadline")
        return (deadline is not None and now >= deadline) or (until == "next_phase" and self._base_phase != self._override_end.get("phase"))

    def _drive(self, level: float, kelvin: float | None) -> None:
        for entity_id, limits in self.lights.items():
            if entity_id in self._paused:
                continue
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                continue
            kind, scale, minimum, threshold = light_settings(limits)
            is_light = entity_id.startswith("light.")
            sent = self._sent.get(entity_id) or Sent(state.state == STATE_ON, _brightness_pct(state), state.attributes.get("color_temp_kelvin"), dt_util.utcnow(), 0)
            light_k = _clamp_kelvin(state, kelvin) if is_light else None
            data: dict[str, Any] = {ATTR_ENTITY_ID: entity_id}
            if kind == LIGHT_ON_OFF or not is_light:
                on = curve.on_off(level, threshold, sent.on, ON_OFF_MARGIN)
                pct, same_level = (100.0 if on else 0.0), sent.on == on
            else:
                pct = curve.dimmer_brightness(level, scale, minimum)
                brightness = round(pct * 2.55)
                on = brightness > 0  # off until the light would get at least 1 of 255; `min` raises it above that
                pct, same_level = brightness / 2.55, sent.on == on and (not on or max(1, round(sent.pct * 2.55)) == brightness)
                if on:
                    data["brightness"] = brightness
            same_kelvin = not on or light_k is None or (sent.kelvin is not None and abs(sent.kelvin - light_k) < COLOR_TEMP_SEND)
            if same_level and same_kelvin:
                continue  # send only when on/off, the 0-255 brightness value, or the colour temperature changes
            if is_light:
                data["transition"] = STEP_TRANSITION
                if on and light_k is not None:
                    data["color_temp_kelvin"] = round(light_k)
            self._send(entity_id, on, pct, light_k if on else sent.kelvin, data)

    def _all_off(self, transition: float) -> None:
        for entity_id in self.lights:
            state = self.hass.states.get(entity_id)
            if state is not None and state.state == STATE_ON:
                data: dict[str, Any] = {ATTR_ENTITY_ID: entity_id} | ({"transition": transition} if entity_id.startswith("light.") else {})
                self._send(entity_id, False, 0, None, data)

    def _send(self, entity_id: str, on: bool, pct: float, kelvin: float | None, data: dict[str, Any]) -> None:
        context = Context()
        self._contexts.append(context.id)
        self._sent[entity_id] = Sent(on, pct, kelvin, dt_util.now(), data.get("transition", 0))
        domain = entity_id.split(".", 1)[0]
        self.hass.async_create_task(self.hass.services.async_call(domain, "turn_on" if on else "turn_off", data, context=context), eager_start=True)

    # --- helpers -------------------------------------------------------------------------------------------------

    def _observer(self) -> Observer:
        return Observer(self.hass.config.latitude, self.hass.config.longitude)

    def _color_temp(self) -> curve.ColorTemp:
        s = self.settings
        return curve.ColorTemp(day=s["ct_day"], dusk=s["ct_dusk"], night=s["ct_night"], day_elevation=s["ct_day_elevation"], night_elevation=s["ct_night_elevation"])

    def _day_extremes(self, observer: Observer, day: date) -> tuple[datetime, float, float]:
        if self._day_cache is None or self._day_cache[0] != day:
            tz = dt_util.get_default_time_zone()
            noon_at = noon(observer, day, tz)
            self._day_cache = (day, elevation(observer, noon_at), elevation(observer, midnight(observer, day, tz)), noon_at)
        _, highest, lowest, noon_at = self._day_cache
        return noon_at, highest, lowest

    def _next_noon(self, after: datetime) -> datetime:
        """First solar noon after `after`."""
        tz = dt_util.get_default_time_zone()
        today = noon(self._observer(), after.date(), tz)
        return today if after < today else noon(self._observer(), after.date() + timedelta(days=1), tz)

    @property
    def auto_until(self) -> datetime | None:
        """When auto switches back on by itself: the first noon or set time after it went off, if 'auto back on' is set."""
        if self.auto or not self.settings["auto_back_on"]:
            return None
        if self.settings["auto_back_on_at"] == "noon":
            return self._next_noon(self.auto_since)
        at = datetime.combine(self.auto_since.date(), self.settings["auto_back_on_time"], self.auto_since.tzinfo)
        return at if at > self.auto_since else at + timedelta(days=1)

    @property
    def paused_lights(self) -> list[str]:
        return sorted(self._paused)

    @property
    def override_end(self) -> dict[str, Any]:
        """How the override ends: until (next_phase / noon / duration), deadline, and the phase it started in."""
        return dict(self._override_end)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()


def _brightness_pct(state: State) -> float:
    if state.state != STATE_ON:
        return 0.0
    return (state.attributes.get("brightness") or 255) / 2.55


def _look(state: State) -> tuple:
    return state.state, state.attributes.get("brightness"), state.attributes.get("color_temp_kelvin")


def _matches(sent: Sent, state: State) -> bool:
    """A state report that matches what we sent (within tolerance) is our own command arriving late."""
    if abs(sent.pct - _brightness_pct(state)) > MANUAL_TOLERANCE:
        return False
    reported = state.attributes.get("color_temp_kelvin")
    return sent.kelvin is None or reported is None or abs(sent.kelvin - reported) <= MANUAL_TOLERANCE_K


def _clamp_kelvin(state: State, kelvin: float | None) -> float | None:
    """The light's own colour temperature range, or None when it has no colour temperature."""
    if kelvin is None or "color_temp" not in (state.attributes.get("supported_color_modes") or []):
        return None
    low, high = state.attributes.get("min_color_temp_kelvin"), state.attributes.get("max_color_temp_kelvin")
    return min(max(kelvin, low or kelvin), high or kelvin)
