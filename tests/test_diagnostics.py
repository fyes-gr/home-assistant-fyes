"""Diagnostics never leak the token or the email."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.fyes.diagnostics import async_get_config_entry_diagnostics

from .fake_fyes import EMAIL, TOKEN

try:  # the HA download path (JSON over HTTP); older harness releases lack the helper
    from pytest_homeassistant_custom_component.components.diagnostics import (
        get_diagnostics_for_config_entry,
    )
except ImportError:  # pragma: no cover - HA < 2025.2 harness
    get_diagnostics_for_config_entry = None


async def test_diagnostics_redacts_secrets(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """The token and the email are redacted; the useful facts are there."""
    diag: dict[str, Any]
    if get_diagnostics_for_config_entry is not None:
        diag = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)
    else:
        diag = await async_get_config_entry_diagnostics(hass, init_integration)
    text = str(diag)
    assert TOKEN not in text
    assert "fyes_" not in text  # not even the token's display prefix
    assert EMAIL not in text
    assert diag["entry"]["data"]["token"] == "**REDACTED**"
    assert diag["entry"]["data"]["email"] == "**REDACTED**"
    assert diag["entry"]["data"]["token_prefix"] == "**REDACTED**"
    assert diag["connection"]["connected"] is True
    assert diag["connection"]["gateway_online"] is True
    assert diag["connection"]["connects"] == 1
    assert diag["counts"]["entities_by_platform"]["sensor"] == 5
    assert diag["counts"]["scenes"] == 2
    assert diag["counts"]["security"] == "OFF"
    assert len(diag["snapshot"]["entities"]) == 18
