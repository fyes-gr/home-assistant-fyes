"""FYES thermostats with a °C setpoint (canRtype 503).

Off = the thermostat card's frost park (5 °C + heating relay off); FYES does that
server-side, HA only sends turn_off / turn_on / set_temperature.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant

from .const import ACTION_SET_TEMPERATURE, ACTION_TURN_OFF, ACTION_TURN_ON
from .coordinator import FyesConfigEntry
from .entity import FyesDeviceEntity, async_setup_device_platform

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

PARALLEL_UPDATES = 0

DEFAULT_MIN = 5.0
DEFAULT_MAX = 30.0
DEFAULT_STEP = 0.5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FyesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up FYES thermostats."""
    async_setup_device_platform(entry, "climate", FyesThermostat, async_add_entities)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


class FyesThermostat(FyesDeviceEntity, ClimateEntity):
    """A FYES heating thermostat."""

    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def _num(self, key: str) -> float | None:
        ent = self.fyes
        return _number(ent.attr(key)) if ent is not None else None

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Heat or off, as FYES reports it."""
        ent = self.fyes
        if ent is None:
            return None
        raw = ent.attr("hvac_mode", ent.state)
        if isinstance(raw, str):
            lowered = raw.lower()
            if lowered in ("heat", "on"):
                return HVACMode.HEAT
            if lowered == "off":
                return HVACMode.OFF
        return None

    @property
    def hvac_action(self) -> HVACAction | None:
        """Whether the heating relay is on right now (heating / idle / off), as FYES confirms it."""
        ent = self.fyes
        raw = ent.attr("hvac_action") if ent is not None else None
        if not isinstance(raw, str):
            return None
        return {
            "heating": HVACAction.HEATING,
            "idle": HVACAction.IDLE,
            "off": HVACAction.OFF,
        }.get(raw.lower())

    @property
    def target_temperature(self) -> float | None:
        """Setpoint in °C."""
        return self._num("target_temperature")

    @property
    def current_temperature(self) -> float | None:
        """The room's main temperature sensor."""
        return self._num("current_temperature")

    @property
    def min_temp(self) -> float:
        """Lowest setpoint."""
        return self._num("min_temp") or DEFAULT_MIN

    @property
    def max_temp(self) -> float:
        """Highest setpoint."""
        return self._num("max_temp") or DEFAULT_MAX

    @property
    def target_temperature_step(self) -> float:
        """Setpoint step."""
        return self._num("target_temp_step") or DEFAULT_STEP

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the setpoint (and the mode, when given)."""
        mode = kwargs.get(ATTR_HVAC_MODE)
        if mode == HVACMode.OFF:
            await self._command(ACTION_TURN_OFF)
            return
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            if mode == HVACMode.HEAT:
                await self._command(ACTION_TURN_ON)
            return
        await self._command(ACTION_SET_TEMPERATURE, float(temperature))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Heat = turn on, off = frost park."""
        await self._command(ACTION_TURN_OFF if hvac_mode == HVACMode.OFF else ACTION_TURN_ON)

    async def async_turn_on(self) -> None:
        """Turn heating on."""
        await self._command(ACTION_TURN_ON)

    async def async_turn_off(self) -> None:
        """Turn heating off (5 °C frost park)."""
        await self._command(ACTION_TURN_OFF)
