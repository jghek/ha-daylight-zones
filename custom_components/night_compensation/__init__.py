"""Night Compensation: per-zone lighting driven by darkness."""

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, MODES, SUBENTRY_ZONE, UNTIL
from .zone import ZoneController

PLATFORMS = [Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.TIME]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type NightCompensationEntry = ConfigEntry[dict[str, ZoneController]]

SET_OVERRIDE_SCHEMA = vol.Schema({
    vol.Required("zone"): vol.All(cv.ensure_list, [cv.string]),
    vol.Required("mode"): vol.In(MODES),
    vol.Optional("level"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
    vol.Optional("until", default="noon"): vol.In(UNTIL),
    vol.Optional("duration"): cv.positive_time_period,
})
CLEAR_OVERRIDE_SCHEMA = vol.Schema({vol.Required("zone"): vol.All(cv.ensure_list, [cv.string])})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def set_override(call: ServiceCall) -> None:
        for zone in _zones(hass, call.data["zone"]):
            zone.set_override(call.data["mode"], call.data.get("level"), call.data["until"], call.data.get("duration"))

    async def clear_override(call: ServiceCall) -> None:
        for zone in _zones(hass, call.data["zone"]):
            zone.clear_override()

    hass.services.async_register(DOMAIN, "set_override", set_override, schema=SET_OVERRIDE_SCHEMA)
    hass.services.async_register(DOMAIN, "clear_override", clear_override, schema=CLEAR_OVERRIDE_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NightCompensationEntry) -> bool:
    entry.runtime_data = {
        subentry_id: ZoneController(hass, subentry_id, subentry.title, dict(subentry.data))
        for subentry_id, subentry in entry.subentries.items() if subentry.subentry_type == SUBENTRY_ZONE
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    for zone in entry.runtime_data.values():
        zone.async_start()
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NightCompensationEntry) -> bool:
    for zone in entry.runtime_data.values():
        zone.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _reload(hass: HomeAssistant, entry: NightCompensationEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _zones(hass: HomeAssistant, device_ids: list[str]) -> list[ZoneController]:
    registry = dr.async_get(hass)
    controllers = {sid: zone for entry in hass.config_entries.async_loaded_entries(DOMAIN) for sid, zone in entry.runtime_data.items()}
    zones = []
    for device_id in device_ids:
        device = registry.async_get(device_id)
        subentry_id = next((ident for domain, ident in (device.identifiers if device else ()) if domain == DOMAIN), None)
        if subentry_id not in controllers:
            raise ServiceValidationError(f"{device_id} is not a Night Compensation zone")
        zones.append(controllers[subentry_id])
    return zones
