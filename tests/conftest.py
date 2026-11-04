"""Fixtures: a zone with fake lights at a generic Dutch location, time frozen."""

from typing import Any

from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daylight_zones.const import DOMAIN, SUBENTRY_ZONE

LIGHTS = {"light.a": {"type": "dimmer", "scale": 50, "min": 0}, "light.b": {"type": "dimmer", "scale": 100, "min": 10}}
ZONE = {"lights": LIGHTS, "brightness": "daylight", "use_boost": True, "use_color_temp": False, "off_hours": "window", "off_schedule": "schedule.nights"}
OFFICE = {"lights": {"light.desk": {"type": "dimmer", "scale": 100, "min": 0}, "light.plain": {"type": "dimmer", "scale": 100, "min": 0}},
          "brightness": "fixed", "use_boost": False, "use_color_temp": True, "off_hours": "switch_off", "off_schedule": "schedule.office_off"}
WEEK = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
SCHEDULES = {
    # the current setup: weekdays 00:00-07:00, weekend 02:00-10:00
    "nights": {"name": "Nights"} | {day: [{"from": "00:00:00", "to": "07:00:00"}] for day in WEEK[:5]} | {day: [{"from": "02:00:00", "to": "10:00:00"}] for day in WEEK[5:]},
    "office_off": {"name": "Office off"} | {day: [{"from": "22:00:00", "to": "24:00:00"}] for day in WEEK},
}


def dimmer(level: float, scale: float, minimum: float) -> int:
    """The brightness value a dimmer gets for a zone level."""
    return round((minimum + level * (scale - minimum) / 100) * 2.55)
CT_LIGHT = {"supported_color_modes": ["color_temp"], "min_color_temp_kelvin": 2700, "max_color_temp_kelvin": 6500}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def expected_lingering_timers() -> bool:
    """The schedule helper used for off hours keeps its own timers for its next block."""
    return True


@pytest.fixture
async def home(hass, freezer):
    hass.config.latitude, hass.config.longitude = 52.0, 5.0
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    freezer.move_to("2026-11-03 19:00:00+01:00")  # Tuesday evening, sun about -21°: cosy
    assert await async_setup_component(hass, "schedule", {"schedule": SCHEDULES})
    for light in LIGHTS:
        hass.states.async_set(light, "off")
    hass.states.async_set("light.desk", "off", CT_LIGHT)
    hass.states.async_set("light.plain", "off", {"supported_color_modes": ["brightness"]})
    return hass


@pytest.fixture
def calls(home):
    """Light calls: "on" and "off" lists, and "all" in the order they were made."""
    ordered: list = []

    async def record(call) -> None:
        ordered.append(call)
        {"turn_on": on, "turn_off": off}[call.service].append(call)

    on, off = [], []
    home.services.async_register("light", "turn_on", record)
    home.services.async_register("light", "turn_off", record)
    return {"on": on, "off": off, "all": ordered}


async def setup_zone(hass, zone: dict[str, Any] | None = None, title: str = "Living") -> MockConfigEntry:
    """An entry in the version-1 format (lights inside the zone), so every test also runs the migration to light subentries."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="Daylight Zones", version=1, subentries_data=[{"data": zone or ZONE, "subentry_type": SUBENTRY_ZONE, "title": title, "unique_id": None}])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
