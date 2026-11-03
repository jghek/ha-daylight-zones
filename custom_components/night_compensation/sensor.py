"""Zone level, phase and effective elevation."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import DEGREE, PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NightCompensationEntry
from .const import PHASES
from .entity import ZoneEntity


async def async_setup_entry(hass: HomeAssistant, entry: NightCompensationEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    for subentry_id, zone in entry.runtime_data.items():
        async_add_entities([LevelSensor(zone), PhaseSensor(zone), ElevationSensor(zone)], config_subentry_id=subentry_id)


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
        return {"phase": s.phase, "curve_level": s.curve_level, "schedule_level": s.schedule_level, "effective_elevation": s.effective_elevation, "source": s.source,
                "caps_active": s.caps_active, "mode": self.zone.mode, "override": self.zone.override, "paused_lights": self.zone.paused_lights}


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
