"""Config flow: one entry; zones and lights are subentries. A light belongs to one zone."""

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, ConfigSubentryFlow, SubentryFlowResult
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    BRIGHTNESS_DAYLIGHT, BRIGHTNESS_FIXED, CONF_BRIGHTNESS, CONF_ENTITY, CONF_OFF_HOURS, CONF_OFF_SCHEDULE, CONF_USE_BOOST, CONF_USE_COLOR_TEMP, CONF_ZONE,
    DOMAIN, LIGHT_DIMMER, LIGHT_ON_OFF, OFF_HOURS_NONE, OFF_HOURS_SWITCH_OFF, OFF_HOURS_WINDOW, SUBENTRY_LIGHT, SUBENTRY_ZONE, light_title,
)

PERCENT = selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX))


class DaylightZonesConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Daylight Zones", data={})
        return self.async_show_form(step_id="user")

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_ZONE: ZoneSubentryFlow, SUBENTRY_LIGHT: LightSubentryFlow}


class ZoneSubentryFlow(ConfigSubentryFlow):
    """The zone's behaviour; its lights are added as separate light subentries."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._zone_step("user", user_input, "", {})

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        return await self._zone_step("reconfigure", user_input, subentry.title, dict(subentry.data))

    async def _zone_step(self, step_id: str, user_input: dict[str, Any] | None, title: str, current: dict[str, Any]) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_OFF_HOURS] != OFF_HOURS_NONE and not user_input.get(CONF_OFF_SCHEDULE):
                errors[CONF_OFF_SCHEDULE] = "schedule_required"
            else:
                name = user_input.pop("name")
                if self.source == "reconfigure":
                    entry, subentry = self._get_entry(), self._get_reconfigure_subentry()
                    self._retitle_lights(entry, subentry.subentry_id, name)
                    return self.async_update_and_abort(entry, subentry, title=name, data=user_input)
                return self.async_create_entry(title=name, data=user_input)
        schema = vol.Schema({
            vol.Required("name", default=title): str,
            vol.Required(CONF_BRIGHTNESS, default=current.get(CONF_BRIGHTNESS, BRIGHTNESS_DAYLIGHT)): selector.SelectSelector(selector.SelectSelectorConfig(options=[BRIGHTNESS_DAYLIGHT, BRIGHTNESS_FIXED], translation_key="brightness")),
            vol.Required(CONF_USE_BOOST, default=current.get(CONF_USE_BOOST, False)): bool,
            vol.Required(CONF_USE_COLOR_TEMP, default=current.get(CONF_USE_COLOR_TEMP, False)): bool,
            vol.Required(CONF_OFF_HOURS, default=current.get(CONF_OFF_HOURS, OFF_HOURS_WINDOW)): selector.SelectSelector(selector.SelectSelectorConfig(options=[OFF_HOURS_NONE, OFF_HOURS_WINDOW, OFF_HOURS_SWITCH_OFF], translation_key="off_hours")),
            vol.Optional(CONF_OFF_SCHEDULE, description={"suggested_value": current.get(CONF_OFF_SCHEDULE)}): selector.EntitySelector(selector.EntitySelectorConfig(domain="schedule")),
        })
        return self.async_show_form(step_id=step_id, data_schema=self.add_suggested_values_to_schema(schema, user_input), errors=errors)

    def _retitle_lights(self, entry: ConfigEntry, zone_id: str, zone_title: str) -> None:
        """Light titles start with their zone's name; keep them in step when the zone is renamed."""
        for sub in entry.subentries.values():
            if sub.subentry_type == SUBENTRY_LIGHT and sub.data.get(CONF_ZONE) == zone_id:
                title = light_title(self.hass, zone_title, sub.data[CONF_ENTITY])
                if title != sub.title:
                    self.hass.config_entries.async_update_subentry(entry, sub, title=title)


class LightSubentryFlow(ConfigSubentryFlow):
    """One light or switch: its zone, and how it follows the zone level."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._light_step("user", user_input, {})

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._light_step("reconfigure", user_input, dict(self._get_reconfigure_subentry().data))

    async def _light_step(self, step_id: str, user_input: dict[str, Any] | None, current: dict[str, Any]) -> SubentryFlowResult:
        entry = self._get_entry()
        zones = {sid: sub.title for sid, sub in entry.subentries.items() if sub.subentry_type == SUBENTRY_ZONE}
        if not zones:
            return self.async_abort(reason="no_zones")
        errors: dict[str, str] = {}
        if user_input is not None:
            entity = user_input[CONF_ENTITY]
            own = self._reconfigure_subentry_id if self.source == "reconfigure" else None
            taken = {sub.data.get(CONF_ENTITY): zones.get(sub.data.get(CONF_ZONE), "?") for sid, sub in entry.subentries.items() if sub.subentry_type == SUBENTRY_LIGHT and sid != own}
            if entity in taken:
                errors[CONF_ENTITY] = "light_in_other_zone"
            else:
                data = user_input | ({"type": LIGHT_ON_OFF} if not entity.startswith("light.") else {})  # switches are always on/off
                title = light_title(self.hass, zones[data[CONF_ZONE]], entity)
                if self.source == "reconfigure":
                    return self.async_update_and_abort(entry, self._get_reconfigure_subentry(), title=title, data=data, unique_id=f"light:{entity}")
                return self.async_create_entry(title=title, data=data, unique_id=f"light:{entity}")
        defaults = {"type": self._default_type(current.get(CONF_ENTITY)), "scale": 100, "min": 0, "threshold": 50} | current
        schema = vol.Schema({
            vol.Required(CONF_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain=["light", "switch"])),
            vol.Required(CONF_ZONE): selector.SelectSelector(selector.SelectSelectorConfig(options=[{"value": sid, "label": title} for sid, title in zones.items()])),
            vol.Required("type", default=defaults["type"]): selector.SelectSelector(selector.SelectSelectorConfig(options=[LIGHT_DIMMER, LIGHT_ON_OFF], translation_key="light_type")),
            vol.Required("scale", default=defaults["scale"]): PERCENT,
            vol.Required("min", default=defaults["min"]): PERCENT,
            vol.Required("threshold", default=defaults["threshold"]): PERCENT,
        })
        suggested = user_input or {k: current[k] for k in (CONF_ENTITY, CONF_ZONE) if k in current}
        return self.async_show_form(step_id=step_id, data_schema=self.add_suggested_values_to_schema(schema, suggested), errors=errors)

    def _default_type(self, entity: str | None) -> str:
        """Dimmer when the light can dim, otherwise on/off."""
        state = self.hass.states.get(entity) if entity else None
        modes = (state.attributes.get("supported_color_modes") if state else None) or []
        return LIGHT_ON_OFF if (entity and not entity.startswith("light.")) or (modes and set(modes) <= {"onoff"}) else LIGHT_DIMMER
