"""FYES lights: relays that drive a light (on/off) and dimmers (brightness)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.util.color import brightness_to_value, value_to_brightness

from .const import ACTION_SET_BRIGHTNESS, ACTION_TURN_OFF, ACTION_TURN_ON
from .coordinator import FyesConfigEntry, FyesCoordinator
from .entity import FyesDeviceEntity, async_setup_device_platform
from .models import FyesEntity

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

PARALLEL_UPDATES = 0

# FYES brightness is a 1-100 percentage; HA's is 1-255.
BRIGHTNESS_SCALE = (1, 100)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FyesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up FYES lights."""
    async_setup_device_platform(entry, "light", FyesLight, async_add_entities)


def is_dimmable(ent: FyesEntity) -> bool:
    """A dimmer reports a brightness; an on/off relay does not."""
    dimmable = ent.attr("dimmable")
    if isinstance(dimmable, bool):
        return dimmable
    if ent.kind is not None:
        return ent.kind.upper() == "DIMMER"
    return "brightness" in ent.attributes


class FyesLight(FyesDeviceEntity, LightEntity):
    """A FYES light."""

    def __init__(self, coordinator: FyesCoordinator, ent: FyesEntity, alive: set[str]) -> None:
        """Initialize the light."""
        super().__init__(coordinator, ent, alive)
        mode = ColorMode.BRIGHTNESS if is_dimmable(ent) else ColorMode.ONOFF
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}

    @property
    def is_on(self) -> bool | None:
        """Confirmed on/off state."""
        ent = self.fyes
        return ent.is_on if ent is not None else None

    @property
    def brightness(self) -> int | None:
        """Confirmed brightness, scaled 0-100 -> 0-255."""
        ent = self.fyes
        if ent is None or self._attr_color_mode is not ColorMode.BRIGHTNESS:
            return None
        level = ent.attr("brightness")
        if not isinstance(level, (int, float)) or isinstance(level, bool):
            return None
        if level <= 0:
            return 0
        return value_to_brightness(BRIGHTNESS_SCALE, min(float(level), 100.0))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, optionally at a brightness. The echo updates the state."""
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is not None and self._attr_color_mode is ColorMode.BRIGHTNESS:
            level = round(brightness_to_value(BRIGHTNESS_SCALE, brightness))
            await self._command(ACTION_SET_BRIGHTNESS, max(1, min(100, level)))
            return
        await self._command(ACTION_TURN_ON)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off. The echo updates the state."""
        await self._command(ACTION_TURN_OFF)
