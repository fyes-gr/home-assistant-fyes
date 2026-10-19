"""The FYES integration: every FYES device, scenario and the alarm, live in HA.

FYES Cloud stays the source of truth. Commands go through the same closed loop as
the FYES app (command -> gateway -> CAN bus -> echo confirms the state), and HA
only ever shows confirmed states.
"""

from __future__ import annotations

import logging

from homeassistant import config_entries
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FyesApi, FyesError
from .const import DOMAIN, PLATFORMS
from .coordinator import FyesConfigEntry, FyesCoordinator
from .models import enabled_by_default

_LOGGER = logging.getLogger(__name__)

# HA >= 2025.8 reloads the entry itself after an options change (OptionsFlowWithReload);
# older versions need the classic update listener.
HAS_OPTIONS_FLOW_WITH_RELOAD = hasattr(config_entries, "OptionsFlowWithReload")


# Per entry, filled at unload: the entities that are enabled only because an option says so
# (wall-button presses, hidden devices). Removing the integration puts those back to
# integration-disabled before HA archives them, so adding it again starts from FYES's own
# defaults; HA still restores what the user chose in its entity settings.
_OPTION_ENABLED = "option_enabled"


async def async_setup_entry(hass: HomeAssistant, entry: FyesConfigEntry) -> bool:
    """Set up one FYES home."""
    hass.data.get(DOMAIN, {}).get(_OPTION_ENABLED, {}).pop(entry.entry_id, None)
    api = FyesApi(async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN])
    coordinator = FyesCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    # The snapshot is fresh; the websocket takes over from here.
    coordinator.connected = True
    coordinator.async_register_hub_device()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start_listener()
    if not HAS_OPTIONS_FLOW_WITH_RELOAD:
        entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))
    return True


async def _async_reload_on_update(hass: HomeAssistant, entry: FyesConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: FyesConfigEntry) -> bool:
    """Unload a FYES home (the websocket closes with the coordinator)."""
    _remember_option_enabled(hass, entry)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _remember_option_enabled(hass: HomeAssistant, entry: FyesConfigEntry) -> None:
    """Note which enabled entities owe that only to an option (see _OPTION_ENABLED)."""
    coordinator: FyesCoordinator | None = getattr(entry, "runtime_data", None)
    if coordinator is None or coordinator.data is None:
        return
    ents = coordinator.data.entities
    ent_reg = er.async_get(hass)
    hass.data.setdefault(DOMAIN, {}).setdefault(_OPTION_ENABLED, {})[entry.entry_id] = {
        reg.entity_id
        for reg in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
        if reg.disabled_by is None
        and (ent := ents.get(reg.unique_id)) is not None
        and enabled_by_default(entry.options, ent)
        and not enabled_by_default({}, ent)
    }


def _reset_option_enabled(hass: HomeAssistant, entry: FyesConfigEntry) -> None:
    """Removal: what an option enabled goes back to integration-disabled."""
    remembered = hass.data.get(DOMAIN, {}).get(_OPTION_ENABLED, {}).pop(entry.entry_id, set())
    ent_reg = er.async_get(hass)
    for entity_id in remembered:
        reg = ent_reg.async_get(entity_id)
        if reg is not None and reg.config_entry_id == entry.entry_id and reg.disabled_by is None:
            ent_reg.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION)


async def async_remove_entry(hass: HomeAssistant, entry: FyesConfigEntry) -> None:
    """The integration was deleted: its token revokes itself in FYES (best effort), so a
    removed Home Assistant never leaves a live connection behind on the account."""
    _reset_option_enabled(hass, entry)
    token = entry.data.get(CONF_TOKEN)
    if not token:
        return
    api = FyesApi(async_get_clientsession(hass), entry.data[CONF_URL], token)
    try:
        await api.async_revoke_self()
    except FyesError as err:
        _LOGGER.debug("Could not revoke the FYES token on removal: %s", err)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: FyesConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting a device only once FYES no longer has it."""
    coordinator: FyesCoordinator = entry.runtime_data
    current = set(coordinator.data.entities) | {coordinator.home_id}
    return not any(domain == DOMAIN and ident in current for domain, ident in device.identifiers)
