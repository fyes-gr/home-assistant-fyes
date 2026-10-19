"""Diagnostics for FYES (the token and the email are redacted)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_TOKEN
from homeassistant.core import HomeAssistant

from .const import CONF_TOKEN_PREFIX
from .coordinator import FyesConfigEntry

# The token, the email, and even the token's display prefix (diagnostics end up in public issues).
TO_REDACT = {CONF_TOKEN, CONF_TOKEN_PREFIX, CONF_EMAIL, "accessToken", "refreshToken", "email"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FyesConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "connection": {
            "server": coordinator.api.base_url,
            "connected": coordinator.connected,
            "gateway_online": data.home.gateway_online,
            "last_update_success": coordinator.last_update_success,
            **coordinator.stats,
        },
        "counts": {
            "entities_by_platform": dict(Counter(e.platform for e in data.entities.values())),
            "rooms": len(data.rooms),
            "scenes": len(data.scenes),
            "security": data.security.mode if data.security else None,
        },
        "snapshot": async_redact_data(coordinator.last_snapshot or {}, TO_REDACT),
    }
