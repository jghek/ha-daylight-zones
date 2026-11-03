"""Zone controller behaviour with fake lights."""

from datetime import timedelta

from homeassistant.core import Context
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_time_changed

from custom_components.night_compensation.const import DOMAIN, EVENT_PHASE_CHANGED

from .conftest import setup_zone


def last(calls, entity_id):
    return next(c.data for c in reversed(calls) if c.data["entity_id"] == entity_id)


async def tick(hass, freezer, minutes=1):
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def test_entities_and_cosy_evening(home, calls):
    await setup_zone(home)
    assert home.states.get("sensor.living_level").state == "60.0"
    assert home.states.get("sensor.living_phase").state == "cosy"
    assert home.states.get("number.living_cosy_level").state == "60.0"
    assert home.states.get("time.living_lights_off_weekday").state == "00:00:00"
    assert home.states.get("select.living_mode").state == "auto"
    assert last(calls["on"], "light.a")["brightness"] == round(30 * 2.55)   # 60 % × max 50 %
    assert last(calls["on"], "light.b")["brightness"] == round(60 * 2.55)


async def test_unused_settings_are_not_created(home, calls):
    from .conftest import ZONE
    await setup_zone(home, ZONE | {"use_boost": False, "use_schedule": False, "sleep_behaviour": "ignore"})
    assert home.states.get("number.living_boost_peak") is None
    assert home.states.get("time.living_lights_off_weekday") is None
    assert home.states.get("number.living_sleep_fade") is None
    assert home.states.get("number.living_cosy_level") is not None


async def test_rate_limit_and_only_sending_changes(home, calls, freezer):
    await setup_zone(home)
    home.states.async_set("light.b", "on", {"brightness": round(60 * 2.55)}, context=Context())  # device confirms our command
    sent = len(calls["on"])
    await tick(home, freezer)
    assert len(calls["on"]) == sent  # nothing changed, nothing sent
    await home.services.async_call("number", "set_value", {"entity_id": "number.living_cosy_level", "value": 20}, blocking=True)
    assert float(home.states.get("sensor.living_level").state) > 59  # no time elapsed yet: hardly any step
    await tick(home, freezer, 2)
    assert 49 <= float(home.states.get("sensor.living_level").state) <= 51  # max 5 %/min
    await tick(home, freezer, 10)
    assert home.states.get("sensor.living_level").state == "20.0"


async def test_sleep_turns_off_with_fade_and_clears_boost(home, calls, freezer):
    entry = await setup_zone(home)
    zone_device = _device(home, entry)
    await home.services.async_call(DOMAIN, "set_override", {"zone": [zone_device], "mode": "boost"}, blocking=True)
    assert home.states.get("sensor.living_level").state == "100.0"
    home.states.async_set("input_boolean.sleep", "on")
    await home.async_block_till_done()
    assert home.states.get("sensor.living_phase").state == "sleep"
    assert last(calls["off"], "light.a")["transition"] == 10
    assert home.states.get("select.living_mode").state == "auto"


async def test_manual_change_pauses_light_until_next_phase(home, calls, freezer):
    await setup_zone(home)
    freezer.tick(timedelta(seconds=30))  # past the transition window of our own command
    home.states.async_set("light.a", "on", {"brightness": 255}, context=Context())  # wall switch, not ours
    await home.async_block_till_done()
    assert home.states.get("sensor.living_level").attributes["paused_lights"] == ["light.a"]
    sent = len([c for c in calls["on"] if c.data["entity_id"] == "light.a"])
    await home.services.async_call("number", "set_value", {"entity_id": "number.living_cosy_level", "value": 40}, blocking=True)
    assert len([c for c in calls["on"] if c.data["entity_id"] == "light.a"]) == sent
    freezer.move_to("2026-11-04 00:10:00+01:00")  # bedtime fade starts: next phase
    async_fire_time_changed(home, dt_util.utcnow())
    await home.async_block_till_done()
    assert home.states.get("sensor.living_phase").state == "bedtime_fade"
    assert home.states.get("sensor.living_level").attributes["paused_lights"] == []


async def test_pause_override_leaves_lights_alone(home, calls, freezer):
    await setup_zone(home)
    await home.services.async_call("select", "select_option", {"entity_id": "select.living_mode", "option": "pause"}, blocking=True)
    sent = len(calls["on"]) + len(calls["off"])
    home.states.async_set("input_boolean.sleep", "on")
    await home.async_block_till_done()
    assert home.states.get("sensor.living_phase").state == "paused"
    assert len(calls["on"]) + len(calls["off"]) == sent


async def test_level_override_until_duration(home, calls, freezer):
    entry = await setup_zone(home)
    await home.services.async_call(DOMAIN, "set_override", {"zone": [_device(home, entry)], "mode": "level", "level": 90, "until": "duration", "duration": {"minutes": 30}}, blocking=True)
    assert home.states.get("sensor.living_level").state == "90.0"
    await tick(home, freezer, 31)
    assert home.states.get("select.living_mode").state == "auto"


async def test_phase_changed_event(home, calls, freezer):
    events = async_capture_events(home, EVENT_PHASE_CHANGED)
    await setup_zone(home)
    home.states.async_set("input_boolean.sleep", "on")
    await home.async_block_till_done()
    assert events[-1].data | {"zone_id": None} == {"zone": "Living", "zone_id": None, "from": "cosy", "to": "sleep"}


async def test_settings_survive_reload(home, calls):
    entry = await setup_zone(home)
    await home.services.async_call("number", "set_value", {"entity_id": "number.living_cosy_level", "value": 35}, blocking=True)
    await home.config_entries.async_reload(entry.entry_id)
    await home.async_block_till_done()
    assert home.states.get("number.living_cosy_level").state == "35.0"


def _device(hass, entry):
    subentry_id = next(iter(entry.subentries))
    return dr.async_get(hass).async_get_device_by_identifier((DOMAIN, subentry_id), entry.entry_id).id
