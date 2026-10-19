"""FYES scenarios as HA scenes (attached to the FYES home device)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.scene import Scene
from homeassistant.core import HomeAssistant, callback

from .coordinator import FyesConfigEntry, FyesCoordinator
from .entity import FyesBaseEntity, hub_device_info

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

PARALLEL_UPDATES = 0


def scene_unique_id(home_id: str, scene_id: str) -> str:
    """Scenario unique_id, namespaced by home like the alarm panel's."""
    return f"{home_id}_scene_{scene_id}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FyesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up FYES scenarios, and add new ones when the list changes."""
    coordinator = entry.runtime_data
    alive: set[str] = set()

    @callback
    def _discover() -> None:
        new = [
            FyesScene(coordinator, scene_id, alive)
            for scene_id in coordinator.data.scenes
            if scene_id not in alive
        ]
        alive.update(scene.scene_id for scene in new)
        if new:
            async_add_entities(new)

    _discover()
    entry.async_on_unload(coordinator.async_add_listener(_discover))


class FyesScene(FyesBaseEntity, Scene):
    """Activate = run the FYES scenario (each device confirms on its own)."""

    def __init__(self, coordinator: FyesCoordinator, scene_id: str, alive: set[str]) -> None:
        """Initialize the scene."""
        super().__init__(coordinator)
        self.scene_id = scene_id
        self._alive = alive
        self._last_name = coordinator.data.scenes[scene_id].display_name(coordinator.greek)
        self._attr_unique_id = scene_unique_id(coordinator.home_id, scene_id)
        self._attr_device_info = hub_device_info(coordinator)

    @property
    def name(self) -> str:
        """The scenario's current FYES name (renames in the app show up live)."""
        scene = self.coordinator.data.scenes.get(self.scene_id)
        if scene is not None:
            self._last_name = scene.display_name(self.coordinator.greek)
        return self._last_name

    def _watched(self) -> object:
        return self.coordinator.data.scenes.get(self.scene_id)

    @property
    def available(self) -> bool:
        """Scenarios run on the hardware: they need the gateway."""
        coordinator = self.coordinator
        return (
            coordinator.connected
            and coordinator.data.home.gateway_online
            and self.scene_id in coordinator.data.scenes
        )

    async def async_activate(self, **kwargs: Any) -> None:
        """Run the scenario."""
        await self.coordinator.async_activate_scene(self.scene_id)

    async def async_will_remove_from_hass(self) -> None:
        """Let the platform re-add the scene if it comes back."""
        await super().async_will_remove_from_hass()
        self._alive.discard(self.scene_id)
