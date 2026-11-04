"""Zone level, phase and effective elevation."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import DEGREE, PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DaylightZonesEntry
from .const import CONF_USE_COLOR_TEMP, PHASES, describe_light
from .entity import ZoneEntity


async def async_setup_entry(hass: HomeAssistant, entry: DaylightZonesEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    for subentry_id, zone in entry.runtime_data.items():
        entities = [LevelSensor(zone), PhaseSensor(zone), ElevationSensor(zone)]
        if zone.config[CONF_USE_COLOR_TEMP]:
            entities.append(ColorTempSensor(zone))
        async_add_entities(entities, config_subentry_id=subentry_id)


class LevelSensor(ZoneEntity, SensorEntity):
    _attr_name = "Level"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, zone) -> None:
        super().__init__(zone, "level")

    @property
    def native_value(self) -> float | None:
        return None if self.zone.snapshot.level is None else round(float(self.zone.snapshot.level), 1)

    @property
    def extra_state_attributes(self) -> dict:
        s = self.zone.snapshot
        return {"phase": s.phase, "curve_level": s.curve_level, "off_hours_level": s.off_hours_level, "effective_elevation": s.effective_elevation,
                "caps_active": s.caps_active, "auto": self.zone.auto, "override": self.zone.override, "paused_lights": self.zone.paused_lights,
                "lights": {entity: describe_light(limits) for entity, limits in self.zone.lights.items()}}


class PhaseSensor(ZoneEntity, SensorEntity):
    _attr_name = "Phase"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = PHASES

    def __init__(self, zone) -> None:
        super().__init__(zone, "phase")

    @property
    def native_value(self) -> str:
        return self.zone.snapshot.phase


class ElevationSensor(ZoneEntity, SensorEntity):
    _attr_name = "Effective elevation"
    _attr_native_unit_of_measurement = DEGREE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, zone) -> None:
        super().__init__(zone, "effective_elevation")

    @property
    def native_value(self) -> float | None:
        return self.zone.snapshot.effective_elevation


class ColorTempSensor(ZoneEntity, SensorEntity):
    _attr_name = "Colour temperature"
    _attr_native_unit_of_measurement = UnitOfTemperature.KELVIN
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:temperature-kelvin"

    def __init__(self, zone) -> None:
        super().__init__(zone, "color_temp")

    @property
    def native_value(self) -> float | None:
        return None if self.zone.snapshot.color_temp is None else round(self.zone.snapshot.color_temp)
