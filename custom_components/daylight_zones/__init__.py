"""Daylight Zones: per-zone lighting driven by darkness."""

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SUBENTRY_ZONE, UNTIL
from .zone import ZoneController

PLATFORMS = [Platform.NUMBER, Platform.SENSOR, Platform.SWITCH, Platform.TIME]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type DaylightZonesEntry = ConfigEntry[dict[str, ZoneController]]

ZONES = vol.All(cv.ensure_list, [cv.string])
SCHEMAS = {
    "turn_on": vol.Schema({vol.Required("zone"): ZONES}),
    "turn_off": vol.Schema({vol.Required("zone"): ZONES, vol.Optional("until"): vol.In(["noon"]), vol.Optional("transition"): vol.All(vol.Coerce(float), vol.Range(min=0, max=300))}),
    "toggle": vol.Schema({vol.Required("zone"): ZONES}),
    "set_override": vol.Schema({vol.Required("zone"): ZONES, vol.Optional("level"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)), vol.Optional("until", default="noon"): vol.In(UNTIL), vol.Optional("duration"): cv.positive_time_period}),
    "clear_override": vol.Schema({vol.Required("zone"): ZONES}),
    "toggle_override": vol.Schema({vol.Required("zone"): ZONES}),
}
HANDLERS = {
    "turn_on": lambda zone, data: zone.set_auto(True),
    "turn_off": lambda zone, data: zone.set_auto(False, data.get("until"), data.get("transition")),
    "toggle": lambda zone, data: zone.toggle_auto(),
    "set_override": lambda zone, data: zone.set_override(True, data.get("level"), data["until"], data.get("duration")),
    "clear_override": lambda zone, data: zone.set_override(False),
    "toggle_override": lambda zone, data: zone.set_override(not zone.override),
}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def handle(call: ServiceCall) -> None:
        for zone in _zones(hass, call.data["zone"]):
            HANDLERS[call.service](zone, call.data)

    for service, schema in SCHEMAS.items():
        hass.services.async_register(DOMAIN, service, handle, schema=schema)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DaylightZonesEntry) -> bool:
    entry.runtime_data = {
        subentry_id: ZoneController(hass, subentry_id, subentry.title, dict(subentry.data))
        for subentry_id, subentry in entry.subentries.items() if subentry.subentry_type == SUBENTRY_ZONE
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    for zone in entry.runtime_data.values():
        zone.async_start()
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DaylightZonesEntry) -> bool:
    for zone in entry.runtime_data.values():
        zone.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _reload(hass: HomeAssistant, entry: DaylightZonesEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _zones(hass: HomeAssistant, device_ids: list[str]) -> list[ZoneController]:
    registry = dr.async_get(hass)
    controllers = {sid: zone for entry in hass.config_entries.async_loaded_entries(DOMAIN) for sid, zone in entry.runtime_data.items()}
    zones = []
    for device_id in device_ids:
        device = registry.async_get(device_id)
        subentry_id = next((ident for domain, ident in (device.identifiers if device else ()) if domain == DOMAIN), None)
        if subentry_id not in controllers:
            raise ServiceValidationError(f"{device_id} is not a Daylight Zones zone")
        zones.append(controllers[subentry_id])
    return zones
