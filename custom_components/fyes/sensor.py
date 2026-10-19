"""FYES analog inputs: temperature, humidity, power, illuminance, raw values."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.sensor.const import DEVICE_CLASS_UNITS
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant

try:  # HA 2026.x
    from homeassistant.const import UnitOfRatio

    PARTS_PER_MILLION: str = UnitOfRatio.PARTS_PER_MILLION
except ImportError:  # older HA
    from homeassistant.const import CONCENTRATION_PARTS_PER_MILLION as PARTS_PER_MILLION

from .coordinator import FyesConfigEntry, FyesCoordinator
from .entity import FyesDeviceEntity, async_setup_device_platform
from .models import FyesEntity

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

PARALLEL_UPDATES = 0

UNITS: dict[str, str] = {
    "°C": UnitOfTemperature.CELSIUS,
    "C": UnitOfTemperature.CELSIUS,
    "°F": UnitOfTemperature.FAHRENHEIT,
    "%": PERCENTAGE,
    "W": UnitOfPower.WATT,
    "kW": UnitOfPower.KILO_WATT,
    "Wh": UnitOfEnergy.WATT_HOUR,
    "kWh": UnitOfEnergy.KILO_WATT_HOUR,
    "lx": LIGHT_LUX,
    "lux": LIGHT_LUX,
    "V": UnitOfElectricPotential.VOLT,
    "Vac": UnitOfElectricPotential.VOLT,
    "A": UnitOfElectricCurrent.AMPERE,
    "ppm": PARTS_PER_MILLION,
    "hPa": UnitOfPressure.HPA,
    "kPa": UnitOfPressure.KPA,
    "mm": UnitOfLength.MILLIMETERS,
    "cm": UnitOfLength.CENTIMETERS,
    "m": UnitOfLength.METERS,
}

DEVICE_CLASSES: dict[str, SensorDeviceClass] = {
    "temperature": SensorDeviceClass.TEMPERATURE,
    "humidity": SensorDeviceClass.HUMIDITY,
    "power": SensorDeviceClass.POWER,
    "illuminance": SensorDeviceClass.ILLUMINANCE,
    "energy": SensorDeviceClass.ENERGY,
    "voltage": SensorDeviceClass.VOLTAGE,
    "current": SensorDeviceClass.CURRENT,
    "co2": SensorDeviceClass.CO2,
    "pressure": SensorDeviceClass.PRESSURE,
    "distance": SensorDeviceClass.DISTANCE,
}

DEFAULT_UNITS: dict[SensorDeviceClass, str] = {
    SensorDeviceClass.TEMPERATURE: UnitOfTemperature.CELSIUS,
    SensorDeviceClass.HUMIDITY: PERCENTAGE,
    SensorDeviceClass.POWER: UnitOfPower.WATT,
    SensorDeviceClass.ILLUMINANCE: LIGHT_LUX,
    SensorDeviceClass.ENERGY: UnitOfEnergy.KILO_WATT_HOUR,
    SensorDeviceClass.VOLTAGE: UnitOfElectricPotential.VOLT,
    SensorDeviceClass.CURRENT: UnitOfElectricCurrent.AMPERE,
    SensorDeviceClass.CO2: PARTS_PER_MILLION,
    SensorDeviceClass.PRESSURE: UnitOfPressure.HPA,
    SensorDeviceClass.DISTANCE: UnitOfLength.CENTIMETERS,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FyesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up FYES sensors."""
    async_setup_device_platform(entry, "sensor", FyesSensor, async_add_entities)


def describe(
    ent: FyesEntity,
) -> tuple[SensorDeviceClass | None, str | None, SensorStateClass | None]:
    """Device class, unit and state class HA will accept for this input."""
    device_class = DEVICE_CLASSES.get((ent.device_class or "").lower())
    raw_unit = ent.attr("unit")
    unit = UNITS.get(raw_unit, raw_unit) if isinstance(raw_unit, str) and raw_unit else None
    if device_class is not None:
        if unit is None:
            unit = DEFAULT_UNITS.get(device_class)
        if unit not in DEVICE_CLASS_UNITS.get(device_class, {unit}):
            device_class = None  # a unit HA would reject for that class: keep the number
    raw_state_class = ent.attr("state_class")
    try:
        state_class = SensorStateClass(raw_state_class) if raw_state_class else None
    except ValueError:
        state_class = None
    if state_class is None:
        state_class = (
            SensorStateClass.TOTAL_INCREASING
            if device_class is SensorDeviceClass.ENERGY
            else SensorStateClass.MEASUREMENT
        )
    return device_class, unit, state_class


class FyesSensor(FyesDeviceEntity, SensorEntity):
    """A FYES analog input, converted the way the app shows it."""

    def __init__(self, coordinator: FyesCoordinator, ent: FyesEntity, alive: set[str]) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, ent, alive)
        device_class, unit, state_class = describe(ent)
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class

    @property
    def native_value(self) -> float | None:
        """The converted reading."""
        ent = self.fyes
        if ent is None:
            return None
        value = ent.state
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return None
