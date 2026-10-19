"""FYES doors, gates and garages: an explicit "Open" press (never a lock state)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant

from .const import ACTION_PRESS
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
    """Set up FYES door buttons."""
    async_setup_device_platform(entry, "button", FyesDoorButton, async_add_entities)


class FyesDoorButton(FyesDeviceEntity, ButtonEntity):
    """Press = the same door pulse as the app's Open."""

    _main_feature = False
    _attr_translation_key = "open"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The door relay's confirmed state ("on" = energized: unlocked / released), so a
        door left open after a press shows in HA; plus the protected flag."""
        attrs = dict(super().extra_state_attributes or {})
        ent = self.fyes
        relay = ent.attr("relay_state") if ent is not None else None
        if relay in ("on", "off"):
            attrs["relay_state"] = relay
        return attrs or None

    async def async_press(self) -> None:
        """Open the door (pulse the relay)."""
        await self._command(ACTION_PRESS)
