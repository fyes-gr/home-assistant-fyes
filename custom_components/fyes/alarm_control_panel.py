"""The FYES security mode as one alarm panel per home (no code, as in the app)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, MODE_AWAY, MODE_HOME, MODE_NIGHT, MODE_OFF
from .coordinator import FyesConfigEntry, FyesCoordinator
from .entity import FyesBaseEntity, hub_device_info

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

PARALLEL_UPDATES = 0

STATE_BY_MODE = {
    MODE_OFF: AlarmControlPanelState.DISARMED,
    MODE_HOME: AlarmControlPanelState.ARMED_HOME,
    MODE_NIGHT: AlarmControlPanelState.ARMED_NIGHT,
    MODE_AWAY: AlarmControlPanelState.ARMED_AWAY,
}
FEATURE_BY_MODE = {
    MODE_HOME: AlarmControlPanelEntityFeature.ARM_HOME,
    MODE_NIGHT: AlarmControlPanelEntityFeature.ARM_NIGHT,
    MODE_AWAY: AlarmControlPanelEntityFeature.ARM_AWAY,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FyesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the security panel when the home has security."""
    coordinator = entry.runtime_data
    alive: set[str] = set()

    @callback
    def _discover() -> None:
        if not alive and coordinator.data.security is not None:
            alive.add(coordinator.home_id)
            async_add_entities([FyesSecurityPanel(coordinator, alive)])

    _discover()
    entry.async_on_unload(coordinator.async_add_listener(_discover))


class FyesSecurityPanel(FyesBaseEntity, AlarmControlPanelEntity):
    """Disarmed / armed home / armed night / armed away."""

    _attr_translation_key = "security"
    _attr_code_arm_required = False
    _attr_code_format = None

    def __init__(self, coordinator: FyesCoordinator, alive: set[str]) -> None:
        """Initialize the panel."""
        super().__init__(coordinator)
        self._alive = alive
        self._attr_unique_id = f"{coordinator.home_id}_security"
        self._attr_device_info = hub_device_info(coordinator)

    def _watched(self) -> object:
        return self.coordinator.data.security

    async def async_will_remove_from_hass(self) -> None:
        """Let the platform re-add the panel if security comes back."""
        await super().async_will_remove_from_hass()
        self._alive.clear()

    @property
    def available(self) -> bool:
        """Security mode is FYES Cloud state: it needs the connection, not the gateway."""
        coordinator = self.coordinator
        return coordinator.connected and coordinator.data.security is not None

    @property
    def supported_features(self) -> AlarmControlPanelEntityFeature:
        """The modes this home offers (none when the user may not change it)."""
        security = self.coordinator.data.security
        features = AlarmControlPanelEntityFeature(0)
        if security is None or not security.can_set:
            return features
        for mode in security.modes:
            features |= FEATURE_BY_MODE.get(mode, AlarmControlPanelEntityFeature(0))
        return features

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Confirmed security mode."""
        security = self.coordinator.data.security
        return STATE_BY_MODE.get(security.mode) if security is not None else None

    async def _set_mode(self, mode: str) -> None:
        security = self.coordinator.data.security
        if security is None or not security.can_set:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="security_not_allowed"
            )
        await self.coordinator.async_set_security(mode)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm. The new mode arrives on the stream."""
        await self._set_mode(MODE_OFF)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm home."""
        await self._set_mode(MODE_HOME)

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        """Arm night."""
        await self._set_mode(MODE_NIGHT)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm away."""
        await self._set_mode(MODE_AWAY)
