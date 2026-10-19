"""FYES siren outputs (disabled by default, contract §5)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.siren import SirenEntity, SirenEntityFeature
from homeassistant.core import HomeAssistant

from .const import ACTION_TURN_OFF, ACTION_TURN_ON
from .coordinator import FyesConfigEntry
from .entity import FyesDeviceEntity, async_setup_device_platform

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FyesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up FYES sirens."""
    async_setup_device_platform(entry, "siren", FyesSiren, async_add_entities)


class FyesSiren(FyesDeviceEntity, SirenEntity):
    """A relay that drives a siren."""

    _attr_supported_features = SirenEntityFeature.TURN_ON | SirenEntityFeature.TURN_OFF

    @property
    def is_on(self) -> bool | None:
        """Confirmed on/off state."""
        ent = self.fyes
        return ent.is_on if ent is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Sound the siren. The echo updates the state."""
        await self._command(ACTION_TURN_ON)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Silence the siren. The echo updates the state."""
        await self._command(ACTION_TURN_OFF)
