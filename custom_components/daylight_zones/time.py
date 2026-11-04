"""Off-hours times; values survive restarts."""

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import DaylightZonesEntry
from .const import TIME_SETTINGS, setting_used
from .entity import ZoneEntity


async def async_setup_entry(hass: HomeAssistant, entry: DaylightZonesEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    for subentry_id, zone in entry.runtime_data.items():
        async_add_entities([ScheduleTime(zone, key, name) for key, name, _, needs in TIME_SETTINGS if setting_used(needs, zone.config)], config_subentry_id=subentry_id)


class ScheduleTime(ZoneEntity, TimeEntity, RestoreEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, zone, key: str, name: str) -> None:
        super().__init__(zone, key)
        self._key = key
        self._attr_name = name

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            try:
                self.zone.set_setting(self._key, time.fromisoformat(last.state))
            except ValueError:
                pass

    @property
    def native_value(self) -> time:
        return self.zone.settings[self._key]

    async def async_set_value(self, value: time) -> None:
        self.zone.set_setting(self._key, value)
