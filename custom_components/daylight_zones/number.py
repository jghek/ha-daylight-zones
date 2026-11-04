"""Zone settings as number entities; values survive restarts."""

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DaylightZonesEntry
from .const import NUMBER_SETTINGS, NumberSetting, setting_used
from .entity import ZoneEntity


async def async_setup_entry(hass: HomeAssistant, entry: DaylightZonesEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    for subentry_id, zone in entry.runtime_data.items():
        async_add_entities([SettingNumber(zone, s) for s in NUMBER_SETTINGS if setting_used(s.needs, zone.config)], config_subentry_id=subentry_id)


class SettingNumber(ZoneEntity, RestoreNumber):
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, zone, setting: NumberSetting) -> None:
        super().__init__(zone, setting.key)
        self._key = setting.key
        self._attr_name = setting.name
        self._attr_native_unit_of_measurement = setting.unit
        self._attr_native_min_value = setting.minimum
        self._attr_native_max_value = setting.maximum
        self._attr_native_step = setting.step

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_number_data()) is not None and last.native_value is not None:
            self.zone.set_setting(self._key, last.native_value)

    @property
    def native_value(self) -> float:
        return float(self.zone.settings[self._key])

    async def async_set_native_value(self, value: float) -> None:
        self.zone.set_setting(self._key, value)
