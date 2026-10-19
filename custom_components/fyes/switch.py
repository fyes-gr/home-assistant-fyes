"""FYES switches: sockets, appliances, pumps, fans, boilers, AC relays."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant

from .const import ACTION_TURN_OFF, ACTION_TURN_ON
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
    """Set up FYES switches."""
    async_setup_device_platform(entry, "switch", FyesSwitch, async_add_entities)


class FyesSwitch(FyesDeviceEntity, SwitchEntity):
    """A FYES relay that is not a light."""

    def __init__(self, coordinator: FyesCoordinator, ent: FyesEntity, alive: set[str]) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, ent, alive)
        self._attr_device_class = (
            SwitchDeviceClass.OUTLET if ent.device_class == "outlet" else SwitchDeviceClass.SWITCH
        )
        if ent.attr("role") == "gate":
            # A gate / garage relay: on/off like the app (never a pulse), with a gate icon.
            self._attr_translation_key = "gate"

    @property
    def is_on(self) -> bool | None:
        """Confirmed on/off state."""
        ent = self.fyes
        return ent.is_on if ent is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on. The echo updates the state."""
        await self._command(ACTION_TURN_ON)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off. The echo updates the state."""
        await self._command(ACTION_TURN_OFF)
