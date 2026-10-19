"""FYES contacts, motion, smoke, tamper and siren inputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant

from .coordinator import FyesConfigEntry, FyesCoordinator
from .entity import FyesDeviceEntity, async_setup_device_platform
from .models import FyesEntity

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FyesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up FYES binary sensors."""
    async_setup_device_platform(entry, "binary_sensor", FyesBinarySensor, async_add_entities)


class FyesBinarySensor(FyesDeviceEntity, BinarySensorEntity):
    """A FYES digital input. For contacts "on" = open, as the app reads it."""

    def __init__(self, coordinator: FyesCoordinator, ent: FyesEntity, alive: set[str]) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, ent, alive)
        try:
            self._attr_device_class = (
                BinarySensorDeviceClass(ent.device_class) if ent.device_class else None
            )
        except ValueError:
            self._attr_device_class = None

    @property
    def is_on(self) -> bool | None:
        """Confirmed input state."""
        ent = self.fyes
        return ent.is_on if ent is not None else None
