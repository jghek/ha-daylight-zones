"""The zone's two switches: auto (follow the sun) and override (a set level)."""

from datetime import datetime
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import DaylightZonesEntry
from .entity import ZoneEntity


async def async_setup_entry(hass: HomeAssistant, entry: DaylightZonesEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    for subentry_id, zone in entry.runtime_data.items():
        async_add_entities([AutoSwitch(zone), OverrideSwitch(zone), AutoBackOnSwitch(zone)], config_subentry_id=subentry_id)


class AutoSwitch(ZoneEntity, SwitchEntity, RestoreEntity):
    """On: follow the sun and manage the lights. Off: lights off, and the zone leaves them alone."""

    _attr_name = "Auto"
    _attr_icon = "mdi:theme-light-dark"

    def __init__(self, zone) -> None:
        super().__init__(zone, "auto")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self.zone.restore_auto(last.state == STATE_ON, last.last_changed)

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


class OverrideSwitch(ZoneEntity, SwitchEntity, RestoreEntity):
    """On: the zone's lights at the override level until switched off, at the latest the next solar noon. Survives restarts."""

    _attr_name = "Override"
    _attr_icon = "mdi:lightbulb-on"

    def __init__(self, zone) -> None:
        super().__init__(zone, "override")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and last.state == STATE_ON:
            until = dt_util.parse_datetime(last.attributes["until"]) if last.attributes.get("until") else None
            self.zone.restore_override(last.attributes.get("ends"), until, last.attributes.get("phase"), last.attributes.get("level"))

    @property
    def is_on(self) -> bool:
        return self.zone.override

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        end = self.zone.override_end
        until: datetime | None = end.get("deadline")
        return {"until": until.isoformat() if until else None, "ends": end.get("until"), "phase": end.get("phase"), "level": end.get("level")}

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.zone.set_override(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.zone.set_override(False)


class AutoBackOnSwitch(ZoneEntity, SwitchEntity, RestoreEntity):
    """Setting: after auto is switched off, switch it back on at noon or a set time (see the 'Auto back on at' select)."""

    _attr_name = "Auto back on"
    _attr_icon = "mdi:restore"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, zone) -> None:
        super().__init__(zone, "auto_back_on")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self.zone.set_setting("auto_back_on", last.state == STATE_ON)

    @property
    def is_on(self) -> bool:
        return self.zone.settings["auto_back_on"]

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.zone.set_setting("auto_back_on", True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.zone.set_setting("auto_back_on", False)
