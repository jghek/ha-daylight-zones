"""Config flow: one entry, one subentry per zone."""

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, ConfigSubentryFlow, SubentryFlowResult
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    BRIGHTNESS_DAYLIGHT, BRIGHTNESS_FIXED, CONF_BRIGHTNESS, CONF_LIGHTS, CONF_LUX_SENSOR, CONF_LUX_SMOOTHING, CONF_OFF_HOURS, CONF_SOURCE, CONF_USE_BOOST,
    CONF_USE_COLOR_TEMP, DOMAIN, OFF_HOURS_NONE, OFF_HOURS_SWITCH_OFF, OFF_HOURS_WINDOW, SOURCE_LUX, SOURCE_SUN, SUBENTRY_ZONE,
    CONF_OFF_SCHEDULE, LIGHT_DIMMER, LIGHT_ON_OFF, light_settings,
)

PERCENT = selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX))


class NightCompensationConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Daylight Zones", data={})
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
        self._types: dict[str, str] = {}

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
            elif user_input[CONF_OFF_HOURS] != OFF_HOURS_NONE and not user_input.get(CONF_OFF_SCHEDULE):
                errors[CONF_OFF_SCHEDULE] = "schedule_required"
            else:
                self._title = user_input.pop("name")
                self._zone = user_input | {CONF_LIGHTS: lights}
                return await self.async_step_light_types()
            user_input[CONF_LIGHTS] = lights
        current = self._current
        schema = vol.Schema({
            vol.Required("name", default=title): str,
            vol.Required(CONF_LIGHTS, default=list(current.get(CONF_LIGHTS, {}))): selector.EntitySelector(selector.EntitySelectorConfig(domain=["light", "switch"], multiple=True)),
            vol.Required(CONF_SOURCE, default=current.get(CONF_SOURCE, SOURCE_SUN)): selector.SelectSelector(selector.SelectSelectorConfig(options=[SOURCE_SUN, SOURCE_LUX], translation_key="source")),
            vol.Optional(CONF_LUX_SENSOR, description={"suggested_value": current.get(CONF_LUX_SENSOR)}): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="illuminance")),
            vol.Required(CONF_LUX_SMOOTHING, default=current.get(CONF_LUX_SMOOTHING, 10)): selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=60, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)),
            vol.Required(CONF_BRIGHTNESS, default=current.get(CONF_BRIGHTNESS, BRIGHTNESS_DAYLIGHT)): selector.SelectSelector(selector.SelectSelectorConfig(options=[BRIGHTNESS_DAYLIGHT, BRIGHTNESS_FIXED], translation_key="brightness")),
            vol.Required(CONF_USE_BOOST, default=current.get(CONF_USE_BOOST, False)): bool,
            vol.Required(CONF_USE_COLOR_TEMP, default=current.get(CONF_USE_COLOR_TEMP, False)): bool,
            vol.Required(CONF_OFF_HOURS, default=current.get(CONF_OFF_HOURS, OFF_HOURS_WINDOW)): selector.SelectSelector(selector.SelectSelectorConfig(options=[OFF_HOURS_NONE, OFF_HOURS_WINDOW, OFF_HOURS_SWITCH_OFF], translation_key="off_hours")),
            vol.Optional(CONF_OFF_SCHEDULE, description={"suggested_value": current.get(CONF_OFF_SCHEDULE)}): selector.EntitySelector(selector.EntitySelectorConfig(domain="schedule")),
        })
        return self.async_show_form(step_id=step_id, data_schema=self.add_suggested_values_to_schema(schema, user_input), errors=errors)

    async def async_step_light_types(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Dimmer or on/off per light; switches are always on/off, so they are not asked."""
        lights = [light for light in self._zone[CONF_LIGHTS] if light.startswith("light.")]
        labels = self._labels(self._zone[CONF_LIGHTS])
        if user_input is not None or not lights:
            self._types = {light: (user_input or {}).get(labels[light], LIGHT_ON_OFF) if light in lights else LIGHT_ON_OFF for light in self._zone[CONF_LIGHTS]}
            return await self.async_step_lights()
        previous = self._current.get(CONF_LIGHTS, {})
        types = selector.SelectSelector(selector.SelectSelectorConfig(options=[LIGHT_DIMMER, LIGHT_ON_OFF], translation_key="light_type"))
        schema = {vol.Required(labels[light], default=light_settings(previous[light])[0] if light in previous else self._default_type(light)): types for light in lights}
        return self.async_show_form(step_id="light_types", data_schema=vol.Schema(schema))

    async def async_step_lights(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Dimmers: scale and minimum (1-100 % zone level maps onto minimum-scale). On/off: the zone level at which it switches on."""
        lights = self._zone[CONF_LIGHTS]
        labels = self._labels(lights)
        fields = {light: (["scale", "min"] if self._types[light] == LIGHT_DIMMER else ["threshold"]) for light in lights}
        if user_input is not None:
            limits = {light: {"type": self._types[light]} | {f: user_input[f"{labels[light]} – {f} %"] for f in fields[light]} for light in lights}
            data = self._zone | {CONF_LIGHTS: limits}
            if self.source == "reconfigure":
                return self.async_update_and_abort(self._get_entry(), self._get_reconfigure_subentry(), title=self._title, data=data)
            return self.async_create_entry(title=self._title, data=data)
        previous = self._current.get(CONF_LIGHTS, {})
        schema: dict[Any, Any] = {}
        for light in lights:
            _, scale, minimum, threshold = light_settings(previous.get(light, {}))
            for field in fields[light]:
                schema[vol.Required(f"{labels[light]} – {field} %", default={"scale": scale, "min": minimum, "threshold": threshold}[field])] = PERCENT
        return self.async_show_form(step_id="lights", data_schema=vol.Schema(schema))

    def _default_type(self, light: str) -> str:
        """Dimmer when the light can dim, otherwise on/off."""
        state = self.hass.states.get(light)
        modes = (state.attributes.get("supported_color_modes") if state else None) or []
        return LIGHT_ON_OFF if modes and set(modes) <= {"onoff"} else LIGHT_DIMMER

    def _labels(self, lights: list[str]) -> dict[str, str]:
        """Field labels from the lights' friendly names; the entity ID is added when two lights share a name."""
        names = {light: (state.name if (state := self.hass.states.get(light)) else light) for light in lights}
        return {light: name if list(names.values()).count(name) == 1 else f"{name} ({light})" for light, name in names.items()}

    def _lights_in_other_zones(self) -> set[str]:
        own = self._reconfigure_subentry_id if self.source == "reconfigure" else None
        return {light for sid, sub in self._get_entry().subentries.items() if sid != own for light in sub.data.get(CONF_LIGHTS, {})}
