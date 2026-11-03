from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.night_compensation.const import DOMAIN, SUBENTRY_ZONE

from .conftest import setup_zone

ZONE_FORM = {"name": "Hallway", "lights": ["light.c"], "source": "sun", "lux_smoothing": 10, "sleep_entity": "input_boolean.sleep", "sleep_behaviour": "off", "use_schedule": True, "use_boost": False}


async def test_create_entry(home):
    result = await home.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await home.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Night Compensation"


async def test_add_zone_with_light_limits(home, calls):
    entry = await setup_zone(home)
    result = await home.config_entries.subentries.async_init((entry.entry_id, SUBENTRY_ZONE), context={"source": SOURCE_USER})
    result = await home.config_entries.subentries.async_configure(result["flow_id"], ZONE_FORM)
    assert result["step_id"] == "lights"
    result = await home.config_entries.subentries.async_configure(result["flow_id"], {"light.c max": 40, "light.c min": 5})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await home.async_block_till_done()
    hallway = next(s for s in entry.subentries.values() if s.title == "Hallway")
    assert hallway.data["lights"] == {"light.c": {"max": 40, "min": 5}}
    assert home.states.get("sensor.hallway_level") is not None


async def test_light_can_only_be_in_one_zone(home, calls):
    entry = await setup_zone(home)
    result = await home.config_entries.subentries.async_init((entry.entry_id, SUBENTRY_ZONE), context={"source": SOURCE_USER})
    result = await home.config_entries.subentries.async_configure(result["flow_id"], ZONE_FORM | {"lights": ["light.a"]})
    assert result["errors"] == {"lights": "light_in_other_zone"}


async def test_lux_source_needs_a_sensor(home, calls):
    entry = await setup_zone(home)
    result = await home.config_entries.subentries.async_init((entry.entry_id, SUBENTRY_ZONE), context={"source": SOURCE_USER})
    result = await home.config_entries.subentries.async_configure(result["flow_id"], ZONE_FORM | {"source": "lux"})
    assert result["errors"] == {"lux_sensor": "lux_sensor_required"}


async def test_reconfigure_keeps_own_lights(home, calls):
    entry = await setup_zone(home)
    subentry_id = next(iter(entry.subentries))
    result = await home.config_entries.subentries.async_init((entry.entry_id, SUBENTRY_ZONE), context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id})
    assert result["step_id"] == "reconfigure"
    result = await home.config_entries.subentries.async_configure(result["flow_id"], ZONE_FORM | {"name": "Living", "lights": ["light.a"], "use_boost": True})
    assert result["step_id"] == "lights"
    result = await home.config_entries.subentries.async_configure(result["flow_id"], {"light.a max": 70, "light.a min": 0})
    assert result["type"] is FlowResultType.ABORT
    await home.async_block_till_done()
    assert entry.subentries[subentry_id].data["lights"] == {"light.a": {"max": 70, "min": 0}}
    assert home.states.get("sensor.living_level").state == "60.0"
