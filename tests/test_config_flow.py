from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.daylight_zones.const import DOMAIN, SUBENTRY_LIGHT, SUBENTRY_ZONE

from .conftest import setup_zone

ZONE_FORM = {"name": "Hallway", "brightness": "daylight", "use_boost": False, "use_color_temp": False, "off_hours": "window", "off_schedule": "schedule.nights"}


def zone_id(entry, title="Living"):
    return next(sid for sid, sub in entry.subentries.items() if sub.subentry_type == SUBENTRY_ZONE and sub.title == title)


def lights(entry):
    return {sub.data["entity"]: (sub.title, dict(sub.data)) for sub in entry.subentries.values() if sub.subentry_type == SUBENTRY_LIGHT}


async def configure(home, entry, kind, form, source=SOURCE_USER, subentry_id=None):
    context = {"source": source} | ({"subentry_id": subentry_id} if subentry_id else {})
    result = await home.config_entries.subentries.async_init((entry.entry_id, kind), context=context)
    if result["type"] is not FlowResultType.FORM:
        return result
    return await home.config_entries.subentries.async_configure(result["flow_id"], form)


async def test_create_entry(home):
    result = await home.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await home.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Daylight Zones"


async def test_migration_makes_one_light_subentry_per_light(home, calls):
    entry = await setup_zone(home)
    assert entry.version == 2
    assert "lights" not in entry.subentries[zone_id(entry)].data
    assert lights(entry) == {
        "light.a": ("Living: a", {"entity": "light.a", "zone": zone_id(entry), "type": "dimmer", "scale": 50, "min": 0, "threshold": 50}),
        "light.b": ("Living: b", {"entity": "light.b", "zone": zone_id(entry), "type": "dimmer", "scale": 100, "min": 10, "threshold": 50}),
    }
    assert home.states.get("sensor.living_level").attributes["lights"] == {"light.a": "dimmer 0-50 %, override 100 %", "light.b": "dimmer 10-100 %, override 100 %"}


async def test_add_zone_then_a_light(home, calls):
    entry = await setup_zone(home)
    result = await configure(home, entry, SUBENTRY_ZONE, ZONE_FORM)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await home.async_block_till_done()
    home.states.async_set("light.c", "off", {"friendly_name": "Hall ceiling", "supported_color_modes": ["brightness"]})
    result = await configure(home, entry, SUBENTRY_LIGHT, {"entity": "light.c", "zone": zone_id(entry, "Hallway"), "type": "dimmer", "scale": 40, "min": 5, "threshold": 50})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await home.async_block_till_done()
    assert lights(entry)["light.c"][0] == "Hallway: Hall ceiling"
    assert home.states.get("sensor.hallway_level").attributes["lights"] == {"light.c": "dimmer 5-40 %, override 80 %"}


async def test_light_override_scale_is_optional(home, calls):
    entry = await setup_zone(home)
    form = {"entity": "light.c", "zone": zone_id(entry), "type": "dimmer", "scale": 4, "min": 0, "threshold": 50}
    await configure(home, entry, SUBENTRY_LIGHT, form | {"override_scale": 80})
    assert lights(entry)["light.c"][1]["override_scale"] == 80
    light_id = next(sid for sid, sub in entry.subentries.items() if sub.data.get("entity") == "light.c")
    await configure(home, entry, SUBENTRY_LIGHT, form, SOURCE_RECONFIGURE, light_id)
    assert "override_scale" not in lights(entry)["light.c"][1]  # emptied: back to the default, twice the scale


async def test_light_can_only_be_in_one_zone(home, calls):
    entry = await setup_zone(home)
    result = await configure(home, entry, SUBENTRY_LIGHT, {"entity": "light.a", "zone": zone_id(entry), "type": "dimmer", "scale": 100, "min": 0, "threshold": 50})
    assert result["errors"] == {"entity": "light_in_other_zone"}


async def test_switch_is_always_on_off(home, calls):
    entry = await setup_zone(home)
    home.states.async_set("switch.relay", "off", {"friendly_name": "Relay"})
    result = await configure(home, entry, SUBENTRY_LIGHT, {"entity": "switch.relay", "zone": zone_id(entry), "type": "dimmer", "scale": 100, "min": 0, "threshold": 25})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert lights(entry)["switch.relay"][1]["type"] == "on_off"


async def test_reconfigure_a_light(home, calls):
    entry = await setup_zone(home)
    light_id = next(sid for sid, sub in entry.subentries.items() if sub.subentry_type == SUBENTRY_LIGHT and sub.data["entity"] == "light.a")
    result = await configure(home, entry, SUBENTRY_LIGHT, {"entity": "light.a", "zone": zone_id(entry), "type": "on_off", "scale": 100, "min": 0, "threshold": 30}, SOURCE_RECONFIGURE, light_id)
    assert result["type"] is FlowResultType.ABORT
    await home.async_block_till_done()
    assert lights(entry)["light.a"][1] | {"zone": None} == {"entity": "light.a", "zone": None, "type": "on_off", "scale": 100, "min": 0, "threshold": 30}
    assert home.states.get("sensor.living_level").attributes["lights"]["light.a"] == "on/off from 30 %"


async def test_no_zones_no_lights(home):
    result = await home.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await home.config_entries.flow.async_configure(result["flow_id"], {})
    entry = result["result"]
    result = await configure(home, entry, SUBENTRY_LIGHT, {})
    assert result == result | {"type": FlowResultType.ABORT, "reason": "no_zones"}


async def test_off_hours_need_a_schedule(home, calls):
    entry = await setup_zone(home)
    result = await configure(home, entry, SUBENTRY_ZONE, {k: v for k, v in ZONE_FORM.items() if k != "off_schedule"})
    assert result["errors"] == {"off_schedule": "schedule_required"}


async def test_renaming_a_zone_retitles_its_lights(home, calls):
    entry = await setup_zone(home)
    result = await configure(home, entry, SUBENTRY_ZONE, ZONE_FORM | {"name": "Lounge", "use_boost": True}, SOURCE_RECONFIGURE, zone_id(entry))
    assert result["type"] is FlowResultType.ABORT
    await home.async_block_till_done()
    assert {title for title, _ in lights(entry).values()} == {"Lounge: a", "Lounge: b"}
