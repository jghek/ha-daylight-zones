"""Zone controller behaviour with fake lights."""

from datetime import timedelta

from homeassistant.core import Context
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_time_changed

from custom_components.daylight_zones.const import DOMAIN, EVENT_PHASE_CHANGED

from .conftest import OFFICE, ZONE, dimmer, setup_zone


def last(calls, entity_id):
    return next((c for c in reversed(calls["all"]) if c.data["entity_id"] == entity_id), None)


def count(calls, entity_id):
    return len([c for c in calls["on"] + calls["off"] if c.data["entity_id"] == entity_id])


async def tick(hass, freezer, minutes=1):
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


def state(hass, entity_id):
    return hass.states.get(entity_id).state


def device(hass, entry):
    subentry_id = next(iter(entry.subentries))
    return dr.async_get(hass).async_get_device_by_identifier((DOMAIN, subentry_id), entry.entry_id).id


async def test_entities_and_cosy_evening(home, calls):
    await setup_zone(home)
    assert state(home, "sensor.living_level") == "60.0"
    assert state(home, "sensor.living_phase") == "cosy"
    assert state(home, "number.living_cosy_level") == "60.0"
    assert state(home, "switch.living_auto") == "on"
    assert state(home, "switch.living_override") == "off"
    assert calls["on"][-2].data["brightness"] == round(30 * 2.55)  # light.a: 60 % × max 50 %
    assert home.states.get("sensor.living_colour_temperature") is None
    assert home.states.get("number.living_level") is None  # fixed-brightness setting not used here


async def test_unused_settings_are_not_created(home, calls):
    await setup_zone(home, ZONE | {"use_boost": False, "off_hours": "none"})
    assert home.states.get("number.living_boost_peak") is None
    assert home.states.get("number.living_off_hours_fade") is None
    assert home.states.get("number.living_cosy_level") is not None


async def test_rate_limit_and_only_sending_changes(home, calls, freezer):
    await setup_zone(home)
    home.states.async_set("light.b", "on", {"brightness": dimmer(60, 100, 10)}, context=Context())  # device confirms our command
    sent = count(calls, "light.b")
    await tick(home, freezer)
    assert count(calls, "light.b") == sent
    await home.services.async_call("number", "set_value", {"entity_id": "number.living_cosy_level", "value": 20}, blocking=True)
    await tick(home, freezer, 2)
    assert 49 <= float(state(home, "sensor.living_level")) <= 51  # max 5 %/min
    await tick(home, freezer, 10)
    assert state(home, "sensor.living_level") == "20.0"


async def test_auto_off_turns_lights_off_and_leaves_them(home, calls, freezer):
    entry = await setup_zone(home)
    home.states.async_set("light.a", "on", {"brightness": 77}, context=Context())
    await home.async_block_till_done()
    await home.services.async_call("switch", "turn_on", {"entity_id": "switch.living_auto_back_on"}, blocking=True)
    await home.services.async_call(DOMAIN, "auto_off", {"zone": [device(home, entry)]}, blocking=True)
    assert last(calls, "light.a").service == "turn_off"
    assert state(home, "sensor.living_phase") == "auto_off"
    sent = count(calls, "light.a")
    home.states.async_set("light.a", "on", {"brightness": 200}, context=Context())  # someone switches it on: left alone
    await tick(home, freezer, 5)
    assert count(calls, "light.a") == sent
    freezer.move_to("2026-11-04 12:30:00+01:00")  # past solar noon: auto comes back
    async_fire_time_changed(home, dt_util.utcnow())
    await home.async_block_till_done()
    assert state(home, "switch.living_auto") == "on"


async def test_override_switch_also_works_when_auto_is_off(home, calls, freezer):
    await setup_zone(home)
    await home.services.async_call("switch", "turn_off", {"entity_id": "switch.living_auto"}, blocking=True)
    await home.services.async_call("switch", "turn_on", {"entity_id": "switch.living_override"}, blocking=True)
    assert state(home, "sensor.living_level") == "80.0"
    assert last(calls, "light.b").data["brightness"] == dimmer(80, 100, 10)
    home.states.async_set("light.b", "on", {"brightness": dimmer(80, 100, 10)}, context=Context())  # the light reports our command
    await home.services.async_call("switch", "turn_off", {"entity_id": "switch.living_override"}, blocking=True)
    assert last(calls, "light.b").service == "turn_off"  # auto is off, so the lights go off


async def test_override_until_duration(home, calls, freezer):
    entry = await setup_zone(home)
    await home.services.async_call(DOMAIN, "override_on", {"zone": [device(home, entry)], "level": 90, "until": "duration", "duration": {"minutes": 30}}, blocking=True)
    assert state(home, "sensor.living_level") == "90.0"
    await tick(home, freezer, 31)
    assert state(home, "switch.living_override") == "off"
    assert state(home, "sensor.living_phase") == "cosy"


async def test_manual_change_pauses_light_until_next_phase(home, calls, freezer):
    await setup_zone(home)
    freezer.tick(timedelta(seconds=30))  # past the transition window of our own command
    home.states.async_set("light.a", "on", {"brightness": 255}, context=Context())  # wall switch, not ours
    await home.async_block_till_done()
    assert home.states.get("sensor.living_level").attributes["paused_lights"] == ["light.a"]
    sent = count(calls, "light.a")
    await home.services.async_call("number", "set_value", {"entity_id": "number.living_cosy_level", "value": 40}, blocking=True)
    assert count(calls, "light.a") == sent
    freezer.move_to("2026-11-04 00:10:00+01:00")  # off hours start: next phase
    async_fire_time_changed(home, dt_util.utcnow())
    await home.async_block_till_done()
    assert state(home, "sensor.living_phase") == "off_fade"
    assert home.states.get("sensor.living_level").attributes["paused_lights"] == []


async def test_office_fixed_level_color_temp_and_switch_off(home, calls, freezer):
    freezer.move_to("2026-11-04 13:00:00+01:00")  # sun about 22°: day colour
    await setup_zone(home, OFFICE, title="Office")
    assert state(home, "sensor.office_level") == "80.0"
    assert state(home, "sensor.office_phase") == "on"
    assert state(home, "sensor.office_colour_temperature") == "5000"
    assert last(calls, "light.desk").data == {"entity_id": "light.desk", "transition": 2, "brightness": 204, "color_temp_kelvin": 5000}
    assert "color_temp_kelvin" not in last(calls, "light.plain").data  # no colour temperature support
    freezer.move_to("2026-11-04 17:30:00+01:00")  # after sunset: warmer, level unchanged
    async_fire_time_changed(home, dt_util.utcnow())
    await home.async_block_till_done()
    assert state(home, "sensor.office_level") == "80.0"
    assert int(state(home, "sensor.office_colour_temperature")) < 5000
    freezer.move_to("2026-11-04 22:20:00+01:00")  # off at 22:00, fading for 30 min
    async_fire_time_changed(home, dt_util.utcnow())
    await home.async_block_till_done()
    assert state(home, "sensor.office_phase") == "off_fade"
    home.states.async_set("light.desk", "on", {**home.states.get("light.desk").attributes, "brightness": 68}, context=Context())  # reports the fade
    await tick(home, freezer, 11)
    assert state(home, "switch.office_auto") == "off"
    assert last(calls, "light.desk").service == "turn_off"
    await home.services.async_call("switch", "turn_on", {"entity_id": "switch.office_auto"}, blocking=True)  # working late
    await tick(home, freezer, 30)
    assert state(home, "switch.office_auto") == "on"


async def test_color_temp_is_clamped_to_the_light(home, calls, freezer):
    freezer.move_to("2026-11-04 19:00:00+01:00")  # dark: night colour 2400 K, light minimum 2700 K
    await setup_zone(home, OFFICE, title="Office")
    assert state(home, "sensor.office_colour_temperature") == "2400"
    assert last(calls, "light.desk").data["color_temp_kelvin"] == 2700


async def test_manual_color_temp_change_pauses_light(home, calls, freezer):
    freezer.move_to("2026-11-04 13:00:00+01:00")
    await setup_zone(home, OFFICE, title="Office")
    freezer.tick(timedelta(seconds=30))
    home.states.async_set("light.desk", "on", {**home.states.get("light.desk").attributes, "brightness": 204, "color_temp_kelvin": 3000}, context=Context())
    await home.async_block_till_done()
    assert home.states.get("sensor.office_level").attributes["paused_lights"] == ["light.desk"]


async def test_phase_changed_event(home, calls, freezer):
    events = async_capture_events(home, EVENT_PHASE_CHANGED)
    await setup_zone(home)
    await home.services.async_call("switch", "turn_on", {"entity_id": "switch.living_override"}, blocking=True)
    assert events[-1].data | {"zone_id": None} == {"zone": "Living", "zone_id": None, "from": "cosy", "to": "override"}


async def test_settings_and_auto_survive_reload(home, calls):
    entry = await setup_zone(home)
    await home.services.async_call("number", "set_value", {"entity_id": "number.living_cosy_level", "value": 35}, blocking=True)
    await home.services.async_call("switch", "turn_on", {"entity_id": "switch.living_auto_back_on"}, blocking=True)
    await home.services.async_call(DOMAIN, "auto_off", {"zone": [device(home, entry)]}, blocking=True)
    await home.config_entries.async_reload(entry.entry_id)
    await home.async_block_till_done()
    assert state(home, "number.living_cosy_level") == "35.0"
    assert state(home, "switch.living_auto") == "off"
    assert home.states.get("switch.living_auto").attributes["until"] is not None
    assert state(home, "switch.living_auto_back_on") == "on"


async def test_override_survives_reload(home, calls, freezer):
    entry = await setup_zone(home)
    await home.services.async_call(DOMAIN, "override_on", {"zone": [device(home, entry)], "level": 90, "until": "duration", "duration": {"minutes": 30}}, blocking=True)
    until = home.states.get("switch.living_override").attributes["until"]
    await home.config_entries.async_reload(entry.entry_id)
    await home.async_block_till_done()
    assert state(home, "switch.living_override") == "on"
    assert state(home, "sensor.living_level") == "90.0"
    assert home.states.get("switch.living_override").attributes == home.states.get("switch.living_override").attributes | {"until": until, "ends": "duration"}
    await tick(home, freezer, 31)
    assert state(home, "switch.living_override") == "off"


async def test_override_that_ended_during_a_restart_is_off_afterwards(home, calls, freezer):
    entry = await setup_zone(home)
    await home.services.async_call(DOMAIN, "override_on", {"zone": [device(home, entry)], "until": "duration", "duration": {"minutes": 30}}, blocking=True)
    await home.config_entries.async_unload(entry.entry_id)
    await home.async_block_till_done()
    freezer.tick(timedelta(hours=1))  # Home Assistant down past the override's end
    assert await home.config_entries.async_setup(entry.entry_id)
    await home.async_block_till_done()
    assert state(home, "switch.living_override") == "off"
    assert state(home, "sensor.living_phase") == "cosy"


async def move(hass, freezer, moment):
    freezer.move_to(moment)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def test_auto_stays_off_without_auto_back_on(home, calls, freezer):
    await setup_zone(home)
    assert state(home, "switch.living_auto_back_on") == "off"  # default
    await home.services.async_call("switch", "turn_off", {"entity_id": "switch.living_auto"}, blocking=True)
    assert home.states.get("switch.living_auto").attributes["until"] is None
    await move(home, freezer, "2026-11-05 14:00:00+01:00")
    assert state(home, "switch.living_auto") == "off"


async def test_auto_back_on_at_a_set_time(home, calls, freezer):
    await setup_zone(home)
    await home.services.async_call("switch", "turn_on", {"entity_id": "switch.living_auto_back_on"}, blocking=True)
    await home.services.async_call("select", "select_option", {"entity_id": "select.living_auto_back_on_at", "option": "time"}, blocking=True)
    await home.services.async_call("time", "set_value", {"entity_id": "time.living_auto_back_on_time", "time": "10:00:00"}, blocking=True)
    await home.services.async_call("switch", "turn_off", {"entity_id": "switch.living_auto"}, blocking=True)  # 19:00
    assert home.states.get("switch.living_auto").attributes["until"] == "2026-11-04T10:00:00+01:00"
    await move(home, freezer, "2026-11-04 09:59:00+01:00")
    assert state(home, "switch.living_auto") == "off"
    await move(home, freezer, "2026-11-04 10:00:30+01:00")
    assert state(home, "switch.living_auto") == "on"


async def test_changing_auto_back_on_while_off_applies_at_once(home, calls, freezer):
    await setup_zone(home)
    await home.services.async_call("switch", "turn_off", {"entity_id": "switch.living_auto"}, blocking=True)
    await move(home, freezer, "2026-11-04 13:00:00+01:00")  # past solar noon, but auto back on was off
    assert state(home, "switch.living_auto") == "off"
    await home.services.async_call("switch", "turn_on", {"entity_id": "switch.living_auto_back_on"}, blocking=True)
    assert state(home, "switch.living_auto") == "on"  # the first noon after it went off has passed


async def test_office_switch_off_stays_off_without_auto_back_on(home, calls, freezer):
    freezer.move_to("2026-11-04 21:00:00+01:00")
    await setup_zone(home, OFFICE, title="Office")
    await move(home, freezer, "2026-11-04 22:31:00+01:00")
    assert state(home, "switch.office_auto") == "off"
    await move(home, freezer, "2026-11-05 13:00:00+01:00")
    assert state(home, "switch.office_auto") == "off"


async def test_restart_at_night_does_not_switch_lights_on(home, calls, freezer):
    freezer.move_to("2026-11-04 03:00:00+01:00")  # inside the night block
    await setup_zone(home)
    assert state(home, "sensor.living_phase") == "off_hours"
    assert [c for c in calls["on"] if c.data.get("brightness")] == []


async def test_on_off_light_and_switch_follow_the_threshold(home, calls, freezer):
    events = async_capture_events(home, "call_service")
    home.states.async_set("light.porch", "off", {"supported_color_modes": ["onoff"]})
    home.states.async_set("switch.relay", "off")
    zone = ZONE | {"use_boost": False, "lights": {"light.porch": {"type": "on_off", "threshold": 50}, "switch.relay": {"type": "on_off", "threshold": 50}}}
    await setup_zone(home, zone)  # cosy 60 % >= 50: both on
    assert last(calls, "light.porch").data == {"entity_id": "light.porch", "transition": 2}
    relay = [e.data for e in events if e.data["domain"] == "switch" and e.data["service_data"].get("entity_id") == "switch.relay"]
    assert relay[-1]["service"] == "turn_on"
    home.states.async_set("light.porch", "on", {"supported_color_modes": ["onoff"]}, context=Context())
    home.states.async_set("switch.relay", "on", context=Context())
    await home.services.async_call("number", "set_value", {"entity_id": "number.living_cosy_level", "value": 49}, blocking=True)
    await tick(home, freezer, 5)
    assert last(calls, "light.porch").service == "turn_on"  # 49 % is within the 2 % margin: stays on
    await home.services.async_call("number", "set_value", {"entity_id": "number.living_cosy_level", "value": 40}, blocking=True)
    await tick(home, freezer, 5)
    assert last(calls, "light.porch").service == "turn_off"
    relay = [e.data for e in events if e.data["domain"] == "switch" and e.data["service_data"].get("entity_id") == "switch.relay"]
    assert relay[-1]["service"] == "turn_off"


async def test_dim_light_stays_off_until_it_gets_one_brightness_step(home, calls, freezer):
    """A light with a 4 % scale at 3 % zone level would get 0.3 of 255: it stays off, as the replaced script did."""
    await setup_zone(home, ZONE | {"use_boost": False, "lights": {"light.a": {"type": "dimmer", "scale": 4, "min": 0}}})
    await home.services.async_call("number", "set_value", {"entity_id": "number.living_cosy_level", "value": 3}, blocking=True)
    await home.services.async_call("number", "set_value", {"entity_id": "number.living_maximum_step", "value": 100}, blocking=True)
    await tick(home, freezer)
    assert count(calls, "light.a") == 0 or last(calls, "light.a").service == "turn_off"
    await home.services.async_call("number", "set_value", {"entity_id": "number.living_cosy_level", "value": 20}, blocking=True)
    await tick(home, freezer)
    assert last(calls, "light.a").data["brightness"] == 2  # 20 % × 4 % = 0.8 % → 2 of 255


async def test_override_level_from_the_action_does_not_change_the_setting(home, calls, freezer):
    entry = await setup_zone(home)
    await home.services.async_call(DOMAIN, "override_on", {"zone": [device(home, entry)], "level": 50}, blocking=True)
    assert state(home, "sensor.living_level") == "50.0"
    assert state(home, "number.living_override_level") == "80.0"
    await home.config_entries.async_reload(entry.entry_id)
    await home.async_block_till_done()
    assert state(home, "sensor.living_level") == "50.0"  # survives a restart
    await home.services.async_call(DOMAIN, "override_off", {"zone": [device(home, entry)]}, blocking=True)
    await home.services.async_call("switch", "turn_on", {"entity_id": "switch.living_override"}, blocking=True)
    assert state(home, "sensor.living_level") == "80.0"  # the switch uses the setting
