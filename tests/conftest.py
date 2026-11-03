"""Fixtures: a zone with fake lights at a generic Dutch location, time frozen."""

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.night_compensation.const import DOMAIN, SUBENTRY_ZONE

LIGHTS = {"light.a": {"max": 50, "min": 0}, "light.b": {"max": 100, "min": 10}}
ZONE = {"lights": LIGHTS, "source": "sun", "lux_smoothing": 10, "sleep_entity": "input_boolean.sleep", "sleep_behaviour": "off", "use_schedule": True, "use_boost": True}


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
    hass.states.async_set("input_boolean.sleep", "off")
    return hass


@pytest.fixture
def calls(home):
    return {"on": async_mock_service(home, "light", "turn_on"), "off": async_mock_service(home, "light", "turn_off")}


async def setup_zone(hass, zone: dict[str, Any] | None = None, title: str = "Living") -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="Night Compensation", subentries_data=[{"data": zone or ZONE, "subentry_type": SUBENTRY_ZONE, "title": title, "unique_id": None}])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
