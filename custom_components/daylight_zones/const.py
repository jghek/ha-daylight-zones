"""Constants for Daylight Zones."""

from dataclasses import dataclass
from datetime import time

DOMAIN = "daylight_zones"
SUBENTRY_ZONE = "zone"
SUBENTRY_LIGHT = "light"
CONF_ENTITY = "entity"
CONF_ZONE = "zone"
EVENT_PHASE_CHANGED = "daylight_zones_phase_changed"

CONF_LIGHTS = "lights"
CONF_BRIGHTNESS = "brightness"
CONF_USE_BOOST = "use_boost"
CONF_USE_COLOR_TEMP = "use_color_temp"
CONF_OFF_HOURS = "off_hours"
CONF_OFF_SCHEDULE = "off_schedule"

BRIGHTNESS_DAYLIGHT, BRIGHTNESS_FIXED = "daylight", "fixed"
OFF_HOURS_NONE, OFF_HOURS_WINDOW, OFF_HOURS_SWITCH_OFF = "none", "window", "switch_off"
LIGHT_DIMMER, LIGHT_ON_OFF = "dimmer", "on_off"
UNTIL = ["next_phase", "noon", "duration"]
BACK_ON_AT = ["noon", "time"]
# Settings that are neither numbers nor times: (key, default)
OTHER_SETTINGS = (("auto_back_on", False), ("auto_back_on_at", "noon"))
PHASES = ["day", "evening_ramp", "peak", "settle", "cosy", "morning_ramp", "on", "off_fade", "off_hours", "on_fade", "override", "auto_off"]

UPDATE_SECONDS = 60
MANUAL_TOLERANCE = 3  # % brightness difference that still counts as our own command
ON_OFF_MARGIN = 2  # % below the threshold before an on/off light switches off again
MANUAL_TOLERANCE_K = 150  # kelvin
COLOR_TEMP_STEP = 100  # most kelvin a light's colour temperature moves per minute
COLOR_TEMP_SEND = 50  # least kelvin change worth a command


@dataclass(frozen=True)
class NumberSetting:
    key: str
    name: str
    unit: str
    minimum: float
    maximum: float
    step: float
    default: float
    needs: str | None = None  # see setting_used()


NUMBER_SETTINGS = (
    NumberSetting("evening_start", "Evening ramp start", "°", -20, 40, 0.1, 15, "daylight"),
    NumberSetting("evening_full", "Evening ramp full", "°", -20, 40, 0.1, 12, "daylight"),
    NumberSetting("peak", "Boost peak", "%", 0, 100, 1, 100, "boost"),
    NumberSetting("hold_until", "Boost hold until", "°", -20, 40, 0.1, -6, "boost"),
    NumberSetting("settle_end", "Settle to cosy by", "°", -20, 40, 0.1, -12, "boost"),
    NumberSetting("cosy", "Cosy level", "%", 0, 100, 1, 60, "daylight"),
    NumberSetting("morning_start", "Morning ramp start", "°", -20, 40, 0.1, 12, "daylight"),
    NumberSetting("morning_off", "Morning off", "°", -20, 40, 0.1, 15, "daylight"),
    NumberSetting("level", "Level", "%", 0, 100, 1, 80, "fixed"),
    NumberSetting("winter_bias", "Winter bias", "°", 0, 10, 0.5, 0, "elevation"),
    NumberSetting("max_step", "Maximum step", "%/min", 1, 100, 1, 5),
    NumberSetting("transition", "Transition", "s", 0, 10, 0.1, 1),
    NumberSetting("ct_day", "Colour temperature day", "K", 2000, 6500, 50, 5000, "color_temp"),
    NumberSetting("ct_dusk", "Colour temperature dusk", "K", 2000, 6500, 50, 3500, "color_temp"),
    NumberSetting("ct_night", "Colour temperature night", "K", 2000, 6500, 50, 2400, "color_temp"),
    NumberSetting("ct_day_elevation", "Day colour from", "°", 1, 60, 0.5, 20, "color_temp"),
    NumberSetting("ct_night_elevation", "Night colour from", "°", -18, -1, 0.5, -8, "color_temp"),
    NumberSetting("off_fade", "Off hours fade", "min", 0, 120, 1, 30, "off_hours"),
    NumberSetting("override_level", "Override level", "%", 0, 100, 1, 80),
)

TIME_SETTINGS = (
    ("auto_back_on_time", "Auto back on time", time(12), None),
)


def setting_used(needs: str | None, zone: dict) -> bool:
    """Whether a setting exists for this zone's configuration."""
    daylight = zone[CONF_BRIGHTNESS] == BRIGHTNESS_DAYLIGHT
    return {
        None: True,
        "daylight": daylight,
        "boost": daylight and zone[CONF_USE_BOOST],
        "fixed": not daylight,
        "elevation": daylight or zone[CONF_USE_COLOR_TEMP],
        "color_temp": zone[CONF_USE_COLOR_TEMP],
        "off_hours": zone[CONF_OFF_HOURS] != OFF_HOURS_NONE,
    }[needs]


def light_title(hass, zone_title: str, entity_id: str) -> str:
    """'Kantoor: Kantoor Voor' — the zone's name and the light's friendly name."""
    state = hass.states.get(entity_id)
    return f"{zone_title}: {state.name if state else entity_id}"


def describe_light(limits: dict) -> str:
    """'dimmer 0-60 %, override 100 %' or 'on/off from 50 %', for the level sensor's attributes."""
    kind, scale, minimum, threshold, override_scale = light_settings(limits)
    return f"on/off from {threshold:g} %" if kind == LIGHT_ON_OFF else f"dimmer {minimum:g}-{scale:g} %, override {override_scale:g} %"


def light_settings(limits: dict) -> tuple[str, float, float, float, float]:
    """(type, scale, min, threshold, override scale) for one light; reads the older max/min format too. Override scale defaults to twice the scale, at most 100 %."""
    scale = limits.get("scale", limits.get("max", 100))
    return limits.get("type", LIGHT_DIMMER), scale, limits.get("min", 0), limits.get("threshold", 50), limits.get("override_scale", min(100, scale * 2))
