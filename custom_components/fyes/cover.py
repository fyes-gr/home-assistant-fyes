"""FYES shutters and awnings (an up/down relay pair, open/close/stop)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant

from .const import ACTION_CLOSE, ACTION_OPEN, ACTION_STOP
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
    """Set up FYES covers."""
    async_setup_device_platform(entry, "cover", FyesCover, async_add_entities)


class FyesCover(FyesDeviceEntity, CoverEntity):
    """A FYES shutter or awning."""

    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(self, coordinator: FyesCoordinator, ent: FyesEntity, alive: set[str]) -> None:
        """Initialize the cover."""
        super().__init__(coordinator, ent, alive)
        try:
            self._attr_device_class = CoverDeviceClass(ent.device_class or "shutter")
        except ValueError:
            self._attr_device_class = CoverDeviceClass.SHUTTER

    @property
    def _state(self) -> str | None:
        ent = self.fyes
        return ent.state.lower() if ent is not None and isinstance(ent.state, str) else None

    @property
    def assumed_state(self) -> bool:
        """FYES drives the motors but does not know where a shutter stopped: with no
        position, HA must keep both Open and Close usable (an idle shutter shown as
        "open" would grey out Open in the HA frontend)."""
        return self.current_cover_position is None

    @property
    def is_closed(self) -> bool | None:
        """Only what FYES knows: "closed"/"open" when it says so. "stopped" (the only
        idle state the backend sends today) means the position is unknown."""
        state = self._state
        if state == "closed":
            return True
        if state in ("open", "opening", "closing"):
            return False
        return None

    @property
    def is_opening(self) -> bool:
        """Moving up."""
        return self._state == "opening"

    @property
    def is_closing(self) -> bool:
        """Moving down."""
        return self._state == "closing"

    @property
    def current_cover_position(self) -> int | None:
        """Position when the backend knows it (0 closed .. 100 open)."""
        ent = self.fyes
        position = ent.attr("position") if ent is not None else None
        if isinstance(position, (int, float)) and not isinstance(position, bool):
            return max(0, min(100, round(position)))
        return None

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open. The echo updates the state."""
        await self._command(ACTION_OPEN)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close. The echo updates the state."""
        await self._command(ACTION_CLOSE)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop. The echo updates the state."""
        await self._command(ACTION_STOP)
