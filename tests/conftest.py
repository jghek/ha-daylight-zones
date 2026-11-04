"""Fixtures: a zone with fake lights at a generic Dutch location, time frozen."""

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.daylight_zones.const import DOMAIN, SUBENTRY_ZONE

LIGHTS = {"light.a": {"max": 50, "min": 0}, "light.b": {"max": 100, "min": 10}}
ZONE = {"lights": LIGHTS, "source": "sun", "lux_smoothing": 10, "brightness": "daylight", "use_boost": True, "use_color_temp": False, "off_hours": "window"}
OFFICE = {"lights": {"light.desk": {"max": 100, "min": 0}, "light.plain": {"max": 100, "min": 0}}, "source": "sun", "lux_smoothing": 10, "brightness": "fixed",
          "use_boost": False, "use_color_temp": True, "off_hours": "switch_off"}
CT_LIGHT = {"supported_color_modes": ["color_temp"], "min_color_temp_kelvin": 2700, "max_color_temp_kelvin": 6500}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def home(hass, freezer):
    hass.config.latitude, hass.config.longitude = 52.0, 5.0
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    freezer.move_to("2026-11-03 19:00:00+01:00")  # Tuesday evening, sun about -21°: cosy
    for light in LIGHTS:
        hass.states.async_set(light, "off")
    hass.states.async_set("light.desk", "off", CT_LIGHT)
    hass.states.async_set("light.plain", "off", {"supported_color_modes": ["brightness"]})
    return hass


@pytest.fixture
def calls(home):
    return {"on": async_mock_service(home, "light", "turn_on"), "off": async_mock_service(home, "light", "turn_off")}


async def setup_zone(hass, zone: dict[str, Any] | None = None, title: str = "Living") -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="Daylight Zones", subentries_data=[{"data": zone or ZONE, "subentry_type": SUBENTRY_ZONE, "title": title, "unique_id": None}])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
