"""Constants for Night Compensation."""

from dataclasses import dataclass
from datetime import time

DOMAIN = "night_compensation"
SUBENTRY_ZONE = "zone"
EVENT_PHASE_CHANGED = "night_compensation_phase_changed"

CONF_LIGHTS = "lights"
CONF_SOURCE = "source"
CONF_LUX_SENSOR = "lux_sensor"
CONF_LUX_SMOOTHING = "lux_smoothing"
CONF_SLEEP_ENTITY = "sleep_entity"
CONF_SLEEP_BEHAVIOUR = "sleep_behaviour"
CONF_USE_SCHEDULE = "use_schedule"
CONF_USE_BOOST = "use_boost"

SOURCE_SUN, SOURCE_LUX = "sun", "lux"
SLEEP_OFF, SLEEP_IGNORE, SLEEP_LEVEL = "off", "ignore", "level"
MODES = ["auto", "boost", "max", "level", "pause"]
UNTIL = ["next_phase", "noon", "duration"]
PHASES = ["day", "evening_ramp", "peak", "settle", "cosy", "bedtime_fade", "night", "wake_fade", "morning_ramp", "sleep", "boost", "max", "override", "paused"]

UPDATE_SECONDS = 60
STEP_TRANSITION = 2  # seconds, for normal level steps
MANUAL_TOLERANCE = 3  # % brightness difference that still counts as our own command
LUX_FLOOR = 1.0  # lx; below this the sun model is used
LUX_STALE_MINUTES = 10


@dataclass(frozen=True)
class NumberSetting:
    key: str
    name: str
    unit: str
    minimum: float
    maximum: float
    step: float
    default: float
    needs: str | None = None  # None, "boost", "schedule", "sleep", "sleep_level"


NUMBER_SETTINGS = (
    NumberSetting("evening_start", "Evening ramp start", "°", -20, 40, 0.1, 15),
    NumberSetting("evening_full", "Evening ramp full", "°", -20, 40, 0.1, 12),
    NumberSetting("peak", "Boost peak", "%", 0, 100, 1, 100, "boost"),
    NumberSetting("hold_until", "Boost hold until", "°", -20, 40, 0.1, -6, "boost"),
    NumberSetting("settle_end", "Settle to cosy by", "°", -20, 40, 0.1, -12, "boost"),
    NumberSetting("cosy", "Cosy level", "%", 0, 100, 1, 60),
    NumberSetting("morning_start", "Morning ramp start", "°", -20, 40, 0.1, 12),
    NumberSetting("morning_off", "Morning off", "°", -20, 40, 0.1, 15),
    NumberSetting("winter_bias", "Winter bias", "°", 0, 10, 0.5, 0),
    NumberSetting("max_step", "Maximum step", "%/min", 1, 100, 1, 5),
    NumberSetting("bedtime_fade", "Bedtime fade", "min", 0, 120, 1, 30, "schedule"),
    NumberSetting("sleep_level", "Sleep level", "%", 0, 100, 1, 0, "sleep_level"),
    NumberSetting("sleep_fade", "Sleep fade", "s", 0, 300, 1, 10, "sleep"),
    NumberSetting("override_level", "Override level", "%", 0, 100, 1, 80),
)

TIME_SETTINGS = (
    ("off_weekday", "Lights off weekday", time(0)),
    ("on_weekday", "Lights on weekday", time(7)),
    ("off_weekend", "Lights off weekend", time(2)),
    ("on_weekend", "Lights on weekend", time(10)),
)


def setting_used(needs: str | None, zone: dict) -> bool:
    """Whether a setting exists for this zone's configuration."""
    if needs is None:
        return True
    if needs == "boost":
        return zone[CONF_USE_BOOST]
    if needs == "schedule":
        return zone[CONF_USE_SCHEDULE]
    if needs == "sleep":
        return bool(zone.get(CONF_SLEEP_ENTITY)) and zone[CONF_SLEEP_BEHAVIOUR] != SLEEP_IGNORE
    return bool(zone.get(CONF_SLEEP_ENTITY)) and zone[CONF_SLEEP_BEHAVIOUR] == SLEEP_LEVEL
