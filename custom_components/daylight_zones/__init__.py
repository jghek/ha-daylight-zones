"""Daylight Zones: per-zone lighting driven by darkness."""

from types import MappingProxyType

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import CONF_ENTITY, CONF_LIGHTS, CONF_ZONE, DOMAIN, SUBENTRY_LIGHT, SUBENTRY_ZONE, UNTIL, light_title
from .zone import ZoneController

PLATFORMS = [Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH, Platform.TIME]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type DaylightZonesEntry = ConfigEntry[dict[str, ZoneController]]

ZONES = vol.All(cv.ensure_list, [cv.string])
SCHEMAS = {
    "auto_on": vol.Schema({vol.Required("zone"): ZONES}),
    "auto_off": vol.Schema({vol.Required("zone"): ZONES, vol.Optional("transition"): vol.All(vol.Coerce(float), vol.Range(min=0, max=300))}),
    "auto_toggle": vol.Schema({vol.Required("zone"): ZONES}),
    "override_on": vol.Schema({vol.Required("zone"): ZONES, vol.Optional("level"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)), vol.Optional("until", default="noon"): vol.In(UNTIL), vol.Optional("duration"): cv.positive_time_period}),
    "override_off": vol.Schema({vol.Required("zone"): ZONES}),
    "override_toggle": vol.Schema({vol.Required("zone"): ZONES}),
}
HANDLERS = {
    "auto_on": lambda zone, data: zone.set_auto(True),
    "auto_off": lambda zone, data: zone.set_auto(False, data.get("transition")),
    "auto_toggle": lambda zone, data: zone.toggle_auto(),
    "override_on": lambda zone, data: zone.set_override(True, data.get("level"), data["until"], data.get("duration")),
    "override_off": lambda zone, data: zone.set_override(False),
    "override_toggle": lambda zone, data: zone.set_override(not zone.override),
}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def handle(call: ServiceCall) -> None:
        for zone in _zones(hass, call.data["zone"]):
            HANDLERS[call.service](zone, call.data)

    for service, schema in SCHEMAS.items():
        hass.services.async_register(DOMAIN, service, handle, schema=schema)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: DaylightZonesEntry) -> bool:
    """Version 1 kept a zone's lights inside the zone; version 2 has one light subentry per light."""
    if entry.version == 1:
        for zone_id, zone in list(entry.subentries.items()):
            if zone.subentry_type != SUBENTRY_ZONE:
                continue
            for entity, limits in dict(zone.data.get(CONF_LIGHTS) or {}).items():
                data = {CONF_ENTITY: entity, CONF_ZONE: zone_id, "type": limits.get("type", "dimmer"), "scale": limits.get("scale", limits.get("max", 100)),
                        "min": limits.get("min", 0), "threshold": limits.get("threshold", 50)}
                hass.config_entries.async_add_subentry(entry, ConfigSubentry(data=MappingProxyType(data), subentry_type=SUBENTRY_LIGHT, title=light_title(hass, zone.title, entity), unique_id=f"light:{entity}"))
            hass.config_entries.async_update_subentry(entry, zone, data={k: v for k, v in zone.data.items() if k != CONF_LIGHTS})
        hass.config_entries.async_update_entry(entry, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DaylightZonesEntry) -> bool:
    lights = [sub.data for sub in entry.subentries.values() if sub.subentry_type == SUBENTRY_LIGHT]
    entry.runtime_data = {
        subentry_id: ZoneController(hass, subentry_id, subentry.title, dict(subentry.data), {l[CONF_ENTITY]: dict(l) for l in lights if l.get(CONF_ZONE) == subentry_id})
        for subentry_id, subentry in entry.subentries.items() if subentry.subentry_type == SUBENTRY_ZONE
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    for zone in entry.runtime_data.values():
        await zone.async_start()
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
