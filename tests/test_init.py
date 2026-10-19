"""Setup, unload, connection handling, reconnect and re-sync."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import time
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fyes import const
from custom_components.fyes.const import DOMAIN
from custom_components.fyes.models import FyesData

from .conftest import entity_id, get_device, setup_entry, state_of, wait_for
from .fake_fyes import HOME_ID, FakeFyes, _ent


def _reauth_flows(hass: HomeAssistant) -> list[dict]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN and flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """The entry loads, subscribes in the ha1 format and unloads cleanly."""
    assert init_integration.state is ConfigEntryState.LOADED
    assert fake_fyes.ws_formats == ["ha1"]
    assert state_of(hass, "light", "dev_ceiling").state == STATE_ON

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED
    await wait_for(hass, lambda: not fake_fyes.subscribers)


async def test_setup_auth_failure_starts_reauth(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """A 401 on the snapshot = the token is dead: setup fails and reauth starts."""
    fake_fyes.snapshot_status = 401
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert len(_reauth_flows(hass)) == 1


async def test_setup_server_error_retries(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """A 5xx or an unreachable server = try again later."""
    fake_fyes.snapshot_status = 502
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_gateway_offline_makes_devices_unavailable(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Gateway offline: every device and scenario is unavailable; back online: re-sync."""
    light = entity_id(hass, "light", "dev_ceiling")
    scene = entity_id(hass, "scene", "home_villa_scene_sc_movie")
    panel = entity_id(hass, "alarm_control_panel", f"{HOME_ID}_security")

    await fake_fyes.set_gateway(False)
    await wait_for(hass, lambda: hass.states.get(light).state == STATE_UNAVAILABLE)
    assert hass.states.get(scene).state == STATE_UNAVAILABLE
    assert hass.states.get(entity_id(hass, "sensor", "dev_temp")).state == STATE_UNAVAILABLE
    # The security mode is FYES Cloud state; it stays usable.
    assert hass.states.get(panel).state == "disarmed"

    # While offline the light was switched at the wall; the re-sync must pick it up.
    fake_fyes.entities["dev_ceiling"]["state"] = "off"
    before = fake_fyes.snapshot_requests
    await fake_fyes.set_gateway(True)
    await wait_for(hass, lambda: fake_fyes.snapshot_requests > before)
    await wait_for(hass, lambda: hass.states.get(light).state == STATE_OFF)
    assert hass.states.get(scene).state != STATE_UNAVAILABLE


async def test_device_unavailable_on_its_own(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """A device the backend marks unavailable is unavailable; the rest are not."""
    fake_fyes.entities["dev_pump"]["available"] = False
    await fake_fyes.push_entity("dev_pump")
    pump = entity_id(hass, "switch", "dev_pump")
    await wait_for(hass, lambda: hass.states.get(pump).state == STATE_UNAVAILABLE)
    assert state_of(hass, "switch", "dev_socket").state == STATE_ON


async def test_reconnect_resyncs_snapshot(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """A dropped socket: unavailable, reconnect, re-fetch, and missed changes appear."""
    dimmer = entity_id(hass, "light", "dev_dimmer")
    coordinator = init_integration.runtime_data
    snapshots = fake_fyes.snapshot_requests

    # Make the reconnect wait so the "disconnected" state is observable.
    fake_fyes.refuse_ws = True
    await fake_fyes.drop_connections()
    await wait_for(hass, lambda: hass.states.get(dimmer).state == STATE_UNAVAILABLE)
    assert not coordinator.connected

    # Changed while HA was not listening (no push).
    fake_fyes.entities["dev_dimmer"]["attributes"]["brightness"] = 100
    fake_fyes.refuse_ws = False
    await wait_for(hass, lambda: coordinator.stats["connects"] >= 2)
    await wait_for(hass, lambda: hass.states.get(dimmer).state == STATE_ON)
    assert hass.states.get(dimmer).attributes["brightness"] == 255
    assert fake_fyes.snapshot_requests > snapshots
    assert fake_fyes.ws_formats == ["ha1", "ha1"]


async def test_backoff_doubles_and_resets(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Failed reconnects wait 1x, 2x, 4x ... capped; a good session resets it."""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        await asyncio.sleep(0.01)

    coordinator = init_integration.runtime_data
    with patch.object(coordinator, "_async_backoff_sleep", fake_sleep):
        await asyncio.sleep(0.2)  # the first session stays up past BACKOFF_RESET_AFTER
        fake_fyes.refuse_ws = True
        await fake_fyes.drop_connections()
        await wait_for(hass, lambda: len(delays) >= 5)
        fake_fyes.refuse_ws = False
        connects = coordinator.stats["connects"]
        await wait_for(hass, lambda: coordinator.stats["connects"] > connects)
        await asyncio.sleep(0.2)  # a good session: up long enough to count
        count = len(delays)
        await fake_fyes.drop_connections()
        await wait_for(hass, lambda: len(delays) > count)

    # Jitter is +-20 %: 0.05 (after a good session) -> 0.1 -> 0.2 (cap) -> 0.2 ...
    assert 0.04 <= delays[0] <= 0.06
    assert 0.08 <= delays[1] <= 0.12
    assert 0.16 <= delays[2] <= 0.24
    assert all(d <= 0.24 for d in delays)
    assert 0.04 <= delays[count] <= 0.06  # reset after the successful session


async def test_backoff_not_reset_by_a_session_dropped_right_after_subscribing(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """A server that closes the socket just after 'subscribed' must not cause a
    reconnect plus a full snapshot every second: the backoff keeps growing."""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        await asyncio.sleep(0.01)

    coordinator = init_integration.runtime_data
    with patch.object(coordinator, "_async_backoff_sleep", fake_sleep):
        fake_fyes.drop_after_subscribe = True
        await fake_fyes.drop_connections()
        await wait_for(hass, lambda: len(delays) >= 4)
        fake_fyes.drop_after_subscribe = False
    assert coordinator.stats["connects"] >= 3, "it did subscribe each time"
    # 0.05 (first, jitter) -> 0.1 -> 0.2 -> 0.2: never back to 0.05 between the drops.
    assert 0.08 <= delays[1] <= 0.12
    assert 0.16 <= delays[2] <= 0.24
    assert 0.16 <= delays[3] <= 0.24


async def test_keepalive_pings_and_drops_a_silent_socket(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Pings keep a healthy socket; a socket that stops answering is replaced."""
    with (
        patch.object(const, "WS_PING_INTERVAL", 0.05),
        patch.object(const, "WS_PONG_TIMEOUT", 0.05),
    ):
        await setup_entry(hass, mock_config_entry, fake_fyes)
        coordinator = mock_config_entry.runtime_data
        await wait_for(hass, lambda: fake_fyes.pings >= 4)
        assert coordinator.stats["connects"] == 1  # answered pings: same socket

        fake_fyes.ignore_pings = True
        await wait_for(hass, lambda: fake_fyes.ws_connections >= 2)
        fake_fyes.ignore_pings = False
        await wait_for(hass, lambda: coordinator.stats["connects"] >= 2)


async def test_ws_auth_rejected_starts_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """The token is revoked: the socket gets auth_err and reauth starts."""
    fake_fyes.reject_ws_auth = True
    await fake_fyes.drop_connections()
    await wait_for(hass, lambda: len(_reauth_flows(hass)) == 1)
    assert state_of(hass, "light", "dev_ceiling").state == STATE_UNAVAILABLE


async def test_auth_err_mid_stream_starts_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Membership removed while connected: the server sends auth_err."""
    await fake_fyes.push({"t": "auth_err"})
    await wait_for(hass, lambda: len(_reauth_flows(hass)) == 1)


async def test_new_device_appears_live(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """A device added in the app shows up without a restart."""
    fake_fyes.entities["dev_new"] = _ent(
        "dev_new", "switch", "Garden lights", "Φώτα κήπου", None, "off"
    )
    await fake_fyes.push_entity("dev_new")
    registry = er.async_get(hass)
    await wait_for(hass, lambda: registry.async_get_entity_id("switch", DOMAIN, "dev_new"))
    assert state_of(hass, "switch", "dev_new").state == STATE_OFF


async def test_removed_device_is_cleaned_up(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """A device deleted in FYES disappears from HA at the next re-sync."""
    assert get_device(hass, "dev_pump") is not None
    del fake_fyes.entities["dev_pump"]
    await fake_fyes.push({"t": "scenes"})  # any re-sync will do
    await wait_for(hass, lambda: get_device(hass, "dev_pump") is None)
    assert er.async_get(hass).async_get_entity_id("switch", DOMAIN, "dev_pump") is None


async def test_renames_in_the_app_follow(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Renamed in the FYES app: device, home and scenario names follow; a name the
    user gave in HA is kept."""
    fake_fyes.entities["dev_pump"]["name"] = "Pool filter pump"
    await fake_fyes.push_entity("dev_pump")
    await wait_for(hass, lambda: get_device(hass, "dev_pump").name == "Pool filter pump")
    pump = entity_id(hass, "switch", "dev_pump")
    await wait_for(
        hass, lambda: hass.states.get(pump).attributes["friendly_name"] == "Pool filter pump"
    )

    socket = get_device(hass, "dev_socket")
    dr.async_get(hass).async_update_device(socket.id, name_by_user="My fridge")
    fake_fyes.entities["dev_socket"]["name"] = "Kitchen fridge socket"
    fake_fyes.homes[HOME_ID]["name"] = "Villa Elia Mykonos"
    fake_fyes.scenes[0]["name"] = "Cinema"
    await fake_fyes.push({"t": "scenes"})  # a re-sync carries every rename
    await wait_for(hass, lambda: get_device(hass, HOME_ID).name == "Villa Elia Mykonos")
    socket = get_device(hass, "dev_socket")
    assert socket.name == "Kitchen fridge socket"
    assert socket.name_by_user == "My fridge"
    scene = entity_id(hass, "scene", "home_villa_scene_sc_movie")
    await wait_for(
        hass, lambda: hass.states.get(scene).attributes["friendly_name"].endswith("Cinema")
    )


async def test_snapshot_does_not_overwrite_newer_realtime(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A slow snapshot never rolls back a change the stream delivered after it."""
    coordinator = init_integration.runtime_data
    old: FyesData = coordinator.data
    started = time.monotonic()
    # The stream turns the pump on after the snapshot was requested...
    coordinator.async_handle_message(
        {"t": "entity", "entity": {**_ent("dev_pump", "switch", "Pool pump", "", None, "on")}}
    )
    coordinator.async_handle_message({"t": "security", "mode": "AWAY"})
    # ...and the snapshot (taken earlier) still says off / OFF.
    stale = replace(
        old,
        entities={**old.entities, "dev_pump": replace(old.entities["dev_pump"], state="off")},
    )
    merged = coordinator._keep_newer_realtime(stale, started)
    assert merged.entities["dev_pump"].state == "on"
    assert merged.security is not None and merged.security.mode == "AWAY"
    # A snapshot requested later than the change wins again.
    later = coordinator._keep_newer_realtime(stale, time.monotonic())
    assert later.entities["dev_pump"].state == "off"


async def test_remove_config_entry_device(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """A device FYES still has cannot be deleted from HA; a gone one can."""
    from custom_components.fyes import async_remove_config_entry_device  # noqa: PLC0415

    pump = get_device(hass, "dev_pump")
    assert pump is not None
    assert not await async_remove_config_entry_device(hass, init_integration, pump)
    orphan = dr.async_get(hass).async_get_or_create(
        config_entry_id=init_integration.entry_id, identifiers={(DOMAIN, "dev_gone")}
    )
    assert await async_remove_config_entry_device(hass, init_integration, orphan)


async def test_two_homes_side_by_side(
    hass: HomeAssistant, fake_fyes: FakeFyes, mock_config_entry: MockConfigEntry
) -> None:
    """Each home is its own entry with its own socket."""
    from homeassistant.const import CONF_TOKEN, CONF_URL  # noqa: PLC0415

    from custom_components.fyes.const import CONF_HOME_ID  # noqa: PLC0415

    from .fake_fyes import OTHER_HOME_ID, OTHER_TOKEN  # noqa: PLC0415

    fake_fyes.homes[OTHER_HOME_ID]["name"] = "Beach house"
    await setup_entry(hass, mock_config_entry, fake_fyes)
    other = MockConfigEntry(
        domain=DOMAIN,
        title="Beach house",
        unique_id=OTHER_HOME_ID,
        data={CONF_URL: fake_fyes.url, CONF_TOKEN: OTHER_TOKEN, CONF_HOME_ID: OTHER_HOME_ID},
    )
    # The fake serves the same entity ids for both homes; real ids are globally unique.
    with patch.object(fake_fyes, "entities", {}):
        await setup_entry(hass, other, fake_fyes)
    assert other.state is ConfigEntryState.LOADED
    assert len(fake_fyes.subscribers) == 2
