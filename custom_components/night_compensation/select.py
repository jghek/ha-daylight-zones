"""Zone mode: auto or an override."""

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NightCompensationEntry
from .const import MODES
from .entity import ZoneEntity


async def async_setup_entry(hass: HomeAssistant, entry: NightCompensationEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    for subentry_id, zone in entry.runtime_data.items():
        async_add_entities([ModeSelect(zone)], config_subentry_id=subentry_id)


class ModeSelect(ZoneEntity, SelectEntity):
    """Choosing an override here lasts until the next solar noon; use the set_override action for other end conditions."""

    _attr_name = "Mode"
    _attr_options = MODES
    _attr_icon = "mdi:lightbulb-auto"

    def __init__(self, zone) -> None:
        super().__init__(zone, "mode")

    @property
    def current_option(self) -> str:
        return self.zone.mode

    async def async_select_option(self, option: str) -> None:
        self.zone.set_override(option)
