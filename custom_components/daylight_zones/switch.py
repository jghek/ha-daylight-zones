"""The zone's two switches: auto (follow the sun) and override (a set level)."""

from datetime import datetime
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import DaylightZonesEntry
from .entity import ZoneEntity


async def async_setup_entry(hass: HomeAssistant, entry: DaylightZonesEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    for subentry_id, zone in entry.runtime_data.items():
        async_add_entities([AutoSwitch(zone), OverrideSwitch(zone)], config_subentry_id=subentry_id)


class AutoSwitch(ZoneEntity, SwitchEntity, RestoreEntity):
    """On: follow the sun and manage the lights. Off: lights off, and the zone leaves them alone."""

    _attr_name = "Auto"
    _attr_icon = "mdi:theme-light-dark"

    def __init__(self, zone) -> None:
        super().__init__(zone, "auto")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            until = dt_util.parse_datetime(last.attributes["until"]) if last.attributes.get("until") else None
            self.zone.restore_auto(last.state == STATE_ON, last.last_changed, until)

    @property
    def is_on(self) -> bool:
        return self.zone.auto

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        until: datetime | None = self.zone.auto_until
        return {"until": until.isoformat() if until else None}

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.zone.set_auto(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.zone.set_auto(False)


class OverrideSwitch(ZoneEntity, SwitchEntity):
    """On: the zone's lights at the override level until switched off, at the latest the next solar noon."""

    _attr_name = "Override"
    _attr_icon = "mdi:lightbulb-on"

    def __init__(self, zone) -> None:
        super().__init__(zone, "override")

    @property
    def is_on(self) -> bool:
        return self.zone.override

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        until = self.zone.override_until
        return {"until": until.isoformat() if until else None}

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.zone.set_override(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.zone.set_override(False)
