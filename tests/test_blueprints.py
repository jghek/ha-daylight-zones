"""The shipped blueprints load through Home Assistant's own blueprint and automation validation, and call the right actions."""

from pathlib import Path
import shutil

from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

from .conftest import setup_zone
from .test_zone import device

SOURCE = Path(__file__).parent.parent / "blueprints" / "automation" / "daylight_zones"


def install(hass) -> None:
    target = Path(hass.config.path("blueprints", "automation", "daylight_zones"))
    target.mkdir(parents=True, exist_ok=True)
    for blueprint in SOURCE.glob("*.yaml"):
        shutil.copy(blueprint, target)


async def test_sleep_mode_blueprint(home, calls):
    entry = await setup_zone(home)
    install(home)
    turn_off = async_mock_service(home, "daylight_zones", "turn_off")
    home.states.async_set("input_boolean.nightmode", "off")
    assert await async_setup_component(home, "automation", {"automation": [{"use_blueprint": {"path": "daylight_zones/sleep_mode.yaml", "input": {
        "sleep_entity": "input_boolean.nightmode", "zones": [device(home, entry)], "transition": 10}}}]})
    await home.async_block_till_done()
    home.states.async_set("input_boolean.nightmode", "on")
    await home.async_block_till_done()
    assert [c.data for c in turn_off] == [{"zone": [device(home, entry)], "until": "noon", "transition": 10}]


async def test_trigger_blueprint_toggles_override_on_a_door_contact(home, calls):
    entry = await setup_zone(home)
    install(home)
    home.states.async_set("binary_sensor.office_door", "off")
    assert await async_setup_component(home, "automation", {"automation": [{"use_blueprint": {"path": "daylight_zones/trigger_zone_action.yaml", "input": {
        "triggers": [{"trigger": "state", "entity_id": "binary_sensor.office_door", "to": "on"}], "zones": [device(home, entry)], "zone_action": "toggle_override"}}}]})
    await home.async_block_till_done()
    home.states.async_set("binary_sensor.office_door", "on")
    await home.async_block_till_done()
    assert home.states.get("switch.living_override").state == "on"
