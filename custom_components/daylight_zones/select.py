"""Setting: when auto comes back on by itself."""

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import DaylightZonesEntry
from .const import BACK_ON_AT
from .entity import ZoneEntity


async def async_setup_entry(hass: HomeAssistant, entry: DaylightZonesEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    for subentry_id, zone in entry.runtime_data.items():
        async_add_entities([AutoBackOnAt(zone)], config_subentry_id=subentry_id)


class AutoBackOnAt(ZoneEntity, SelectEntity, RestoreEntity):
    """noon: the next solar noon; time: the 'Auto back on time'."""

    _attr_name = "Auto back on at"
    _attr_options = BACK_ON_AT
    _attr_icon = "mdi:clock-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, zone) -> None:
        super().__init__(zone, "auto_back_on_at")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and last.state in BACK_ON_AT:
            self.zone.set_setting("auto_back_on_at", last.state)

    @property
    def current_option(self) -> str:
        return self.zone.settings["auto_back_on_at"]

    async def async_select_option(self, option: str) -> None:
        self.zone.set_setting("auto_back_on_at", option)
