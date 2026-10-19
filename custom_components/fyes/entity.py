"""Base entities for FYES."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import FyesConfigEntry, FyesCoordinator
from .models import FyesEntity, enabled_by_default

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

# HA 2026.8 replaced DeviceInfo.via_device (identifier tuple) with via_device_id
# (registry id); older versions only know via_device.
_HAS_VIA_DEVICE_ID = "via_device_id" in getattr(DeviceInfo, "__annotations__", {})

_MODEL_BY_PLATFORM = {
    "light": "Light",
    "switch": "Relay",
    "cover": "Shutter",
    "climate": "Thermostat",
    "sensor": "Analog input",
    "binary_sensor": "Input",
    "button": "Door relay",
    "event": "Wall button",
    "siren": "Siren",
}


def hub_link(coordinator: FyesCoordinator) -> dict[str, Any]:
    """DeviceInfo fields that hang a device off the FYES home hub device."""
    if _HAS_VIA_DEVICE_ID and coordinator.hub_device_id is not None:
        return {"via_device_id": coordinator.hub_device_id}
    return {"via_device": (DOMAIN, coordinator.home_id)}


def hub_device_info(coordinator: FyesCoordinator) -> DeviceInfo:
    """Device info of the "FYES home" hub (already registered at setup)."""
    return DeviceInfo(identifiers={(DOMAIN, coordinator.home_id)})


def device_info_for(coordinator: FyesCoordinator, ent: FyesEntity) -> DeviceInfo:
    """One HA device per FYES device, in the FYES room."""
    greek = coordinator.greek
    info = DeviceInfo(
        identifiers={(DOMAIN, ent.id)},
        manufacturer=MANUFACTURER,
        model=_model(ent),
        name=ent.display_name(greek),
        **hub_link(coordinator),  # type: ignore[typeddict-item]
    )
    room = coordinator.data.rooms.get(ent.room_id) if ent.room_id else None
    if room is not None:
        info["suggested_area"] = room.display_name(greek)
    return info


def _model(ent: FyesEntity) -> str:
    if ent.kind:
        return ent.kind.replace("_", " ").capitalize()
    return _MODEL_BY_PLATFORM.get(ent.platform, "FYES device")


_NOTHING = object()


class FyesBaseEntity(CoordinatorEntity[FyesCoordinator]):
    """Common to every FYES entity."""

    _attr_has_entity_name = True
    _last_rendered: tuple[object, bool] = (_NOTHING, False)

    def _watched(self) -> object:
        """The piece of coordinator data this entity renders."""
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        """Remember what was rendered last."""
        await super().async_added_to_hass()
        self._last_rendered = (self._watched(), self.available)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write state only when this entity's data or availability changed."""
        rendered = (self._watched(), self.available)
        if rendered[0] is self._last_rendered[0] and rendered[1] == self._last_rendered[1]:
            return
        self._last_rendered = rendered
        self.async_write_ha_state()


class FyesDeviceEntity(FyesBaseEntity):
    """An entity backed by one FYES entity (one FYES device)."""

    # The device's main feature: the entity carries the device's name. Platforms whose
    # entity has its own translated name (the door button: "Open") turn this off.
    _main_feature = True

    def __init__(self, coordinator: FyesCoordinator, ent: FyesEntity, alive: set[str]) -> None:
        """Initialize from the snapshot entity."""
        super().__init__(coordinator)
        self._fyes_id = ent.id
        # The platform this entity was created on: if the FYES device moves to another
        # (renamed from a socket to a lamp), this one stops and the new platform takes over.
        self._fyes_platform = ent.platform
        self._alive = alive
        if self._main_feature:
            self._attr_name = None
        self._attr_unique_id = ent.id
        self._attr_device_info = device_info_for(coordinator, ent)
        self._attr_entity_registry_enabled_default = enabled_by_default(
            coordinator.config_entry.options, ent
        )

    @property
    def fyes(self) -> FyesEntity | None:
        """The current FYES data of this entity (None if the home dropped it)."""
        return self.coordinator.data.entities.get(self._fyes_id)

    def _watched(self) -> object:
        return self.fyes

    @property
    def available(self) -> bool:
        """Available when connected, the gateway is online and the device reports.

        Not gated on the last refresh succeeding: while the websocket is live the stream
        keeps every state current, and a failed push re-sync is retried (coordinator).
        """
        coordinator = self.coordinator
        ent = self.fyes
        return (
            coordinator.connected
            and coordinator.data.home.gateway_online
            and ent is not None
            and ent.platform == self._fyes_platform
            and ent.available
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Mark protected devices (the app asks before switching them)."""
        ent = self.fyes
        if ent is not None and ent.protected:
            return {"protected": True}
        return None

    async def async_will_remove_from_hass(self) -> None:
        """Let the platform re-add this entity if the device comes back."""
        await super().async_will_remove_from_hass()
        self._alive.discard(self._fyes_id)

    async def _command(self, action: str, value: float | None = None) -> None:
        await self.coordinator.async_command(self._fyes_id, action, value)


@callback
def async_setup_device_platform(
    entry: FyesConfigEntry,
    platform: str,
    factory: Callable[[FyesCoordinator, FyesEntity, set[str]], FyesDeviceEntity],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the platform's entities now and whenever the home gains a device."""
    coordinator = entry.runtime_data
    alive: set[str] = set()

    @callback
    def _discover() -> None:
        new: list[FyesDeviceEntity] = []
        for ent in coordinator.data.entities.values():
            if ent.platform == platform and ent.id not in alive:
                alive.add(ent.id)
                new.append(factory(coordinator, ent, alive))
        if new:
            async_add_entities(new)

    _discover()
    entry.async_on_unload(coordinator.async_add_listener(_discover))
