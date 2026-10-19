"""Fixtures for the FYES integration tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Generator
from unittest.mock import patch

from homeassistant import block_async_io
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fyes import const
from custom_components.fyes.const import CONF_HOME_ID, CONF_TOKEN_ID, CONF_TOKEN_PREFIX, DOMAIN

from .fake_fyes import EMAIL, HOME_ID, TOKEN, FakeFyes


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/fyes in every test."""


@pytest.fixture(autouse=True)
async def detect_blocking_io(disable_block_async_io: None) -> AsyncGenerator[None]:
    """HA's own blocking-call detector, as in production: blocking I/O in the loop fails."""
    block_async_io.enable()
    yield


@pytest.fixture(autouse=True)
def fast_reconnect() -> Generator[None]:
    """Reconnect in tens of milliseconds instead of seconds."""
    with (
        patch.object(const, "BACKOFF_INITIAL", 0.05),
        patch.object(const, "BACKOFF_MAX", 0.2),
        patch.object(const, "WS_HANDSHAKE_TIMEOUT", 2.0),
        patch.object(const, "BACKOFF_RESET_AFTER", 0.15),
        patch.object(const, "RESYNC_RETRY_DELAYS", (0.02, 0.02, 0.02)),
    ):
        yield


@pytest.fixture
async def fake_fyes(hass: HomeAssistant, socket_enabled: None) -> AsyncGenerator[FakeFyes]:
    """A running fake FYES Cloud on 127.0.0.1 (pytest-socket still blocks other hosts)."""
    fake = FakeFyes()
    await fake.start()
    yield fake
    # Stop the integration first, so it does not reconnect into a closing server.
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    await fake.close()


@pytest.fixture
def entry_options() -> dict[str, bool]:
    """Options of the config entry (tests override this)."""
    return {}


@pytest.fixture
def mock_config_entry(fake_fyes: FakeFyes, entry_options: dict[str, bool]) -> MockConfigEntry:
    """A config entry for Villa Elia pointing at the fake server."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Villa Elia",
        unique_id=HOME_ID,
        data={
            CONF_URL: fake_fyes.url,
            CONF_TOKEN: TOKEN,
            CONF_HOME_ID: HOME_ID,
            CONF_TOKEN_ID: "tok_0",
            CONF_TOKEN_PREFIX: TOKEN[:9],
            CONF_EMAIL: EMAIL,
        },
        options=entry_options,
    )


def entity_id(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    """HA entity_id of a FYES entity (HA's naming scheme varies between versions)."""
    found = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert found is not None, f"no {platform} entity with unique_id {unique_id}"
    return found


def state_of(hass: HomeAssistant, platform: str, unique_id: str) -> State:
    """Current state object of a FYES entity."""
    state = hass.states.get(entity_id(hass, platform, unique_id))
    assert state is not None, f"{platform} {unique_id} has no state"
    return state


def get_device(hass: HomeAssistant, identifier: str) -> dr.DeviceEntry | None:
    """The HA device of a FYES identifier (works on every supported HA version)."""
    registry = dr.async_get(hass)
    for entry in hass.config_entries.async_entries(DOMAIN):
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            if (DOMAIN, identifier) in device.identifiers:
                return device
    return None


async def wait_for(
    hass: HomeAssistant, predicate: Callable[[], object], seconds: float = 5.0
) -> None:
    """Wait until predicate() is truthy (the websocket runs in the background)."""
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.01)
            await hass.async_block_till_done()


async def setup_entry(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeFyes
) -> MockConfigEntry:
    """Set up the entry and wait until the realtime stream is live."""
    if entry.entry_id not in {e.entry_id for e in hass.config_entries.async_entries(DOMAIN)}:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await wait_for(hass, lambda: entry.runtime_data.stats["connects"] >= 1)
    return entry


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_fyes: FakeFyes
) -> MockConfigEntry:
    """Villa Elia set up and live."""
    return await setup_entry(hass, mock_config_entry, fake_fyes)
