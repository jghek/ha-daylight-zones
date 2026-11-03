"""Config flow: one entry, one subentry per zone."""

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, ConfigSubentryFlow, SubentryFlowResult
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_LIGHTS, CONF_LUX_SENSOR, CONF_LUX_SMOOTHING, CONF_SLEEP_BEHAVIOUR, CONF_SLEEP_ENTITY, CONF_SOURCE, CONF_USE_BOOST, CONF_USE_SCHEDULE,
    DOMAIN, SLEEP_IGNORE, SLEEP_LEVEL, SLEEP_OFF, SOURCE_LUX, SOURCE_SUN, SUBENTRY_ZONE,
)

PERCENT = selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX))


class NightCompensationConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Night Compensation", data={})
        return self.async_show_form(step_id="user")

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_ZONE: ZoneSubentryFlow}


class ZoneSubentryFlow(ConfigSubentryFlow):
    """Two steps: the zone itself, then max/min per light."""

    def __init__(self) -> None:
        self._zone: dict[str, Any] = {}
        self._title = ""
        self._current: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._zone_step("user", user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        self._current = dict(subentry.data)
        return await self._zone_step("reconfigure", user_input, subentry.title)

    async def _zone_step(self, step_id: str, user_input: dict[str, Any] | None, title: str = "") -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            lights = user_input.pop(CONF_LIGHTS)
            taken = self._lights_in_other_zones()
            if any(light in taken for light in lights):
                errors[CONF_LIGHTS] = "light_in_other_zone"
            elif user_input[CONF_SOURCE] == SOURCE_LUX and not user_input.get(CONF_LUX_SENSOR):
                errors[CONF_LUX_SENSOR] = "lux_sensor_required"
            else:
                self._title = user_input.pop("name")
                self._zone = user_input | {CONF_LIGHTS: lights}
                return await self.async_step_lights()
            user_input[CONF_LIGHTS] = lights
        current = self._current
        schema = vol.Schema({
            vol.Required("name", default=title): str,
            vol.Required(CONF_LIGHTS, default=list(current.get(CONF_LIGHTS, {}))): selector.EntitySelector(selector.EntitySelectorConfig(domain="light", multiple=True)),
            vol.Required(CONF_SOURCE, default=current.get(CONF_SOURCE, SOURCE_SUN)): selector.SelectSelector(selector.SelectSelectorConfig(options=[SOURCE_SUN, SOURCE_LUX], translation_key="source")),
            vol.Optional(CONF_LUX_SENSOR, description={"suggested_value": current.get(CONF_LUX_SENSOR)}): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="illuminance")),
            vol.Required(CONF_LUX_SMOOTHING, default=current.get(CONF_LUX_SMOOTHING, 10)): selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=60, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(CONF_SLEEP_ENTITY, description={"suggested_value": current.get(CONF_SLEEP_ENTITY)}): selector.EntitySelector(selector.EntitySelectorConfig(domain=["input_boolean", "switch", "binary_sensor"])),
            vol.Required(CONF_SLEEP_BEHAVIOUR, default=current.get(CONF_SLEEP_BEHAVIOUR, SLEEP_OFF)): selector.SelectSelector(selector.SelectSelectorConfig(options=[SLEEP_OFF, SLEEP_IGNORE, SLEEP_LEVEL], translation_key="sleep_behaviour")),
            vol.Required(CONF_USE_SCHEDULE, default=current.get(CONF_USE_SCHEDULE, True)): bool,
            vol.Required(CONF_USE_BOOST, default=current.get(CONF_USE_BOOST, False)): bool,
        })
        return self.async_show_form(step_id=step_id, data_schema=self.add_suggested_values_to_schema(schema, user_input), errors=errors)

    async def async_step_lights(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        lights = self._zone[CONF_LIGHTS]
        if user_input is not None:
            data = self._zone | {CONF_LIGHTS: {light: {"max": user_input[f"{light} max"], "min": user_input[f"{light} min"]} for light in lights}}
            if self.source == "reconfigure":
                return self.async_update_and_abort(self._get_entry(), self._get_reconfigure_subentry(), title=self._title, data=data)
            return self.async_create_entry(title=self._title, data=data)
        previous = self._current.get(CONF_LIGHTS, {})
        schema: dict[Any, Any] = {}
        for light in lights:
            schema[vol.Required(f"{light} max", default=previous.get(light, {}).get("max", 100))] = PERCENT
            schema[vol.Required(f"{light} min", default=previous.get(light, {}).get("min", 0))] = PERCENT
        return self.async_show_form(step_id="lights", data_schema=vol.Schema(schema))

    def _lights_in_other_zones(self) -> set[str]:
        own = self._reconfigure_subentry_id if self.source == "reconfigure" else None
        return {light for sid, sub in self._get_entry().subentries.items() if sid != own for light in sub.data.get(CONF_LIGHTS, {})}
