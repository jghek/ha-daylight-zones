"""Base entity: one device per zone, updated by the zone controller."""

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .zone import ZoneController


class ZoneEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, zone: ZoneController, key: str) -> None:
        self.zone = zone
        self._attr_unique_id = f"{zone.subentry_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, zone.subentry_id)}, name=zone.name, entry_type=DeviceEntryType.SERVICE, manufacturer="Daylight Zones")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.zone.async_add_listener(self.async_write_ha_state))
