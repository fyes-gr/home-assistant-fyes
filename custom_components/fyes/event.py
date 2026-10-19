"""FYES wall buttons: a `press` event from the realtime stream (disabled by default)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback

from .coordinator import FyesConfigEntry
from .entity import FyesDeviceEntity, async_setup_device_platform

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

PARALLEL_UPDATES = 0

EVENT_PRESS = "press"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FyesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up FYES wall-button events."""
    async_setup_device_platform(entry, "event", FyesWallButton, async_add_entities)


class FyesWallButton(FyesDeviceEntity, EventEntity):
    """A physical wall button; automations can trigger on its presses."""

    _attr_translation_key = "wall_button"
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = [EVENT_PRESS]

    async def async_added_to_hass(self) -> None:
        """Listen for presses of this button."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_event_listener(self._fyes_id, self._async_pressed)
        )

    @callback
    def _async_pressed(self, event_type: str) -> None:
        if event_type not in self.event_types:
            return
        self._trigger_event(event_type)
        self.async_write_ha_state()
