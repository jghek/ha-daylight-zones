from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.daylight_zones.const import DOMAIN, SUBENTRY_ZONE

from .conftest import setup_zone

ZONE_FORM = {"name": "Hallway", "lights": ["light.c"], "brightness": "daylight", "use_boost": False, "use_color_temp": False, "off_hours": "window",
             "off_schedule": "schedule.nights"}


async def test_create_entry(home):
    result = await home.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await home.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Daylight Zones"


async def test_add_zone_with_light_limits(home, calls):
    entry = await setup_zone(home)
    home.states.async_set("light.c", "off", {"friendly_name": "Hall ceiling"})
    result = await home.config_entries.subentries.async_init((entry.entry_id, SUBENTRY_ZONE), context={"source": SOURCE_USER})
    result = await home.config_entries.subentries.async_configure(result["flow_id"], ZONE_FORM)
    assert result["step_id"] == "light_types"
    result = await home.config_entries.subentries.async_configure(result["flow_id"], {"Hall ceiling": "dimmer"})
    assert result["step_id"] == "lights"
    result = await home.config_entries.subentries.async_configure(result["flow_id"], {"Hall ceiling – scale %": 40, "Hall ceiling – min %": 5})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await home.async_block_till_done()
    hallway = next(s for s in entry.subentries.values() if s.title == "Hallway")
    assert hallway.data["lights"] == {"light.c": {"type": "dimmer", "scale": 40, "min": 5}}
    assert home.states.get("sensor.hallway_level") is not None


async def test_light_can_only_be_in_one_zone(home, calls):
    entry = await setup_zone(home)
    result = await home.config_entries.subentries.async_init((entry.entry_id, SUBENTRY_ZONE), context={"source": SOURCE_USER})
    result = await home.config_entries.subentries.async_configure(result["flow_id"], ZONE_FORM | {"lights": ["light.a"]})
    assert result["errors"] == {"lights": "light_in_other_zone"}


async def test_reconfigure_keeps_own_lights(home, calls):
    entry = await setup_zone(home)
    subentry_id = next(iter(entry.subentries))
    result = await home.config_entries.subentries.async_init((entry.entry_id, SUBENTRY_ZONE), context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id})
    assert result["step_id"] == "reconfigure"
    result = await home.config_entries.subentries.async_configure(result["flow_id"], ZONE_FORM | {"name": "Living", "lights": ["light.a"], "use_boost": True})
    assert result["step_id"] == "light_types"
    result = await home.config_entries.subentries.async_configure(result["flow_id"], {"a": "on_off"})
    assert result["step_id"] == "lights"
    result = await home.config_entries.subentries.async_configure(result["flow_id"], {"a – threshold %": 30})
    assert result["type"] is FlowResultType.ABORT
    await home.async_block_till_done()
    assert entry.subentries[subentry_id].data["lights"] == {"light.a": {"type": "on_off", "threshold": 30}}
    assert home.states.get("sensor.living_level").state == "60.0"


async def test_off_hours_need_a_schedule(home, calls):
    entry = await setup_zone(home)
    result = await home.config_entries.subentries.async_init((entry.entry_id, SUBENTRY_ZONE), context={"source": SOURCE_USER})
    result = await home.config_entries.subentries.async_configure(result["flow_id"], {k: v for k, v in ZONE_FORM.items() if k != "off_schedule"})
    assert result["errors"] == {"off_schedule": "schedule_required"}


async def test_switches_skip_the_type_step(home, calls):
    entry = await setup_zone(home)
    home.states.async_set("switch.relay", "off", {"friendly_name": "Relay"})
    result = await home.config_entries.subentries.async_init((entry.entry_id, SUBENTRY_ZONE), context={"source": SOURCE_USER})
    result = await home.config_entries.subentries.async_configure(result["flow_id"], ZONE_FORM | {"lights": ["switch.relay"]})
    assert result["step_id"] == "lights"
    result = await home.config_entries.subentries.async_configure(result["flow_id"], {"Relay – threshold %": 25})
    assert result["type"] is FlowResultType.CREATE_ENTRY
