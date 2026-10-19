"""The independent review of 2026-10-19, one test per finding (HA side)."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import ATTR_ENTITY_ID, CONF_EMAIL, CONF_TOKEN, CONF_URL, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fyes.config_flow import SECTION_ADVANCED, token_name
from custom_components.fyes.const import (
    CONF_CODE,
    CONF_SHOW_BUTTON_EVENTS,
    CONF_SHOW_HIDDEN,
    CONF_TOKEN_ID,
    DOMAIN,
)

from .conftest import entity_id, get_device, setup_entry, state_of, wait_for
from .fake_fyes import CODE, EMAIL, HOME_ID, OTHER_HOME_ID, TOKEN, FakeFyes, _ent


def _registry_has(hass: HomeAssistant, platform: str, unique_id: str) -> bool:
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id) is not None


# --- a device that moves platform (renamed in the app: socket -> lamp) -------------------


async def test_platform_move_over_the_stream_replaces_the_entity(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """The old switch entity goes (not left behind, available and controllable); the
    new light sits on the same HA device."""
    device_before = get_device(hass, "dev_pump")
    assert _registry_has(hass, "switch", "dev_pump")
    fake_fyes.entities["dev_pump"]["platform"] = "light"
    fake_fyes.entities["dev_pump"]["name"] = "Garden light"
    await fake_fyes.push_entity("dev_pump")
    await wait_for(hass, lambda: _registry_has(hass, "light", "dev_pump"))
    await wait_for(hass, lambda: not _registry_has(hass, "switch", "dev_pump"))
    assert state_of(hass, "light", "dev_pump").state == "off"
    device_after = get_device(hass, "dev_pump")
    assert device_after is not None and device_after.id == device_before.id
    assert device_after.name == "Garden light"


async def test_platform_move_found_by_a_snapshot(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """The same when the move is only seen in a re-fetched snapshot (after a reconnect)."""
    coordinator = init_integration.runtime_data
    fake_fyes.entities["dev_pump"]["platform"] = "light"
    connects = coordinator.stats["connects"]
    await fake_fyes.drop_connections()
    await wait_for(hass, lambda: coordinator.stats["connects"] > connects)
    await wait_for(hass, lambda: not _registry_has(hass, "switch", "dev_pump"))
    assert _registry_has(hass, "light", "dev_pump")


# --- a device removed in the app ---------------------------------------------------------


async def test_entity_removed_message(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """{t:'entity_removed'} takes the entity and its HA device away at once."""
    assert get_device(hass, "dev_pump") is not None
    del fake_fyes.entities["dev_pump"]
    await fake_fyes.push({"t": "entity_removed", "entityId": "dev_pump"})
    await wait_for(hass, lambda: get_device(hass, "dev_pump") is None)
    assert not _registry_has(hass, "switch", "dev_pump")
    assert "dev_pump" not in init_integration.runtime_data.data.entities


# --- a failed push re-sync ---------------------------------------------------------------


async def test_failed_resync_is_retried_and_never_makes_the_home_unavailable(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """A 502 on the re-sync after {t:'scenes'}: retried; the home stays available."""
    fake_fyes.scenes.append({"id": "sc_new", "name": "Dinner", "nameEl": "Δείπνο"})
    fake_fyes.snapshot_fail_times = 2
    before = fake_fyes.snapshot_requests
    await fake_fyes.push({"t": "scenes"})
    await wait_for(hass, lambda: _registry_has(hass, "scene", f"{HOME_ID}_scene_sc_new"))
    assert fake_fyes.snapshot_requests - before == 3  # two failures, then the good one
    assert state_of(hass, "light", "dev_ceiling").state != STATE_UNAVAILABLE
    assert state_of(hass, "alarm_control_panel", f"{HOME_ID}_security").state != STATE_UNAVAILABLE


async def test_resync_that_keeps_failing_reconnects(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Still failing after the retries: the socket is reopened (a fresh session
    re-subscribes and re-fetches), and the home recovers on its own."""
    coordinator = init_integration.runtime_data
    connections = fake_fyes.ws_connections
    fake_fyes.snapshot_fail_times = 4  # the re-sync and its three retries
    await fake_fyes.push({"t": "resync"})
    await wait_for(hass, lambda: fake_fyes.ws_connections > connections)
    await wait_for(hass, lambda: coordinator.connected)
    assert state_of(hass, "light", "dev_ceiling").state == "on"


# --- tokens: no leaks, clear errors ------------------------------------------------------


async def test_removing_the_integration_revokes_its_token(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Delete the entry -> DELETE /integrations/v1/token with its own token."""
    assert await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()
    assert fake_fyes.self_revoked == [TOKEN]
    assert TOKEN not in fake_fyes.tokens


async def test_email_reauth_revokes_the_token_it_replaces(
    hass: HomeAssistant, fake_fyes: FakeFyes, mock_config_entry: MockConfigEntry
) -> None:
    """Each email re-auth mints a token; the replaced one is revoked with the session."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "email"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_EMAIL: EMAIL})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["reason"] == "reauth_successful"
    assert fake_fyes.revoked == ["tok_0"]
    assert mock_config_entry.data[CONF_TOKEN_ID] == "tok_1"


async def _email_flow(hass: HomeAssistant, fake: FakeFyes) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "email"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, SECTION_ADVANCED: {CONF_URL: fake.url}}
    )
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})


async def test_too_many_tokens_has_its_own_message(
    hass: HomeAssistant, fake_fyes: FakeFyes
) -> None:
    """409 TOO_MANY_TOKENS -> 'remove an old connection in the app', not 'unknown'."""
    fake_fyes.user_homes = [HOME_ID]
    fake_fyes.max_tokens = 0
    result = await _email_flow(hass, fake_fyes)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "too_many_tokens"


async def test_a_long_location_name_still_signs_in(
    hass: HomeAssistant, fake_fyes: FakeFyes
) -> None:
    """The token name is cut to the 60 characters the server accepts."""
    hass.config.location_name = "Our big family summer house by the sea in Chalkidiki, Greece"
    fake_fyes.user_homes = [HOME_ID]
    result = await _email_flow(hass, fake_fyes)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    name = fake_fyes.issued_tokens[0]["name"]
    assert (
        len(name) <= 60
        and name.startswith("Home Assistant (Our big family")
        and name.endswith("…)")
    )
    assert token_name(None) == "Home Assistant"
    assert token_name("Σπίτι") == "Home Assistant (Σπίτι)"


async def test_an_expired_session_asks_for_a_new_code(
    hass: HomeAssistant, fake_fyes: FakeFyes
) -> None:
    """A 401 on the homes list (the session expired while the form was open) drops the
    session: the next submit verifies a code again instead of failing forever."""
    fake_fyes.user_homes = [HOME_ID]
    fake_fyes.jwt_valid = False
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "email"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, SECTION_ADVANCED: {CONF_URL: fake_fyes.url}}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["errors"] == {"base": "invalid_code"}
    fake_fyes.jwt_valid = True
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert fake_fyes.verify_calls == 2


async def test_forbidden_homes_list_is_not_unknown(
    hass: HomeAssistant, fake_fyes: FakeFyes
) -> None:
    """A 403 on the homes list (e.g. the demo account) says 'not allowed'."""
    fake_fyes.homes_status = 403
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "email"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, SECTION_ADVANCED: {CONF_URL: fake_fyes.url}}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["errors"] == {"base": "not_allowed"}


# --- reconfigure (the Server) ------------------------------------------------------------


async def test_reconfigure_changes_the_server(
    hass: HomeAssistant, fake_fyes: FakeFyes, mock_config_entry: MockConfigEntry
) -> None:
    """A local hub moved: the stored token must open the same home at the new address."""
    mock_config_entry.add_to_hass(hass)
    moved = FakeFyes()  # the same FYES server at a new address
    await moved.start()
    elsewhere = FakeFyes()  # a server where this token belongs to another home
    elsewhere.tokens[TOKEN] = OTHER_HOME_ID
    await elsewhere.start()
    try:
        result = await mock_config_entry.start_reconfigure_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reconfigure"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: "http://127.0.0.1:9"}
        )
        assert result["errors"] == {"base": "cannot_connect"}
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: "ftp://nope"}
        )
        assert result["errors"] == {"base": "invalid_url"}
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: moved.url + "/"}
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert mock_config_entry.data[CONF_URL] == moved.url
        assert mock_config_entry.data[CONF_TOKEN] == TOKEN

        result = await mock_config_entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: elsewhere.url}
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "wrong_home"
        assert mock_config_entry.data[CONF_URL] == moved.url
    finally:
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        await moved.close()
        await elsewhere.close()


# --- hidden devices, climate, sensors, door relay, gate, hub name, errors ---------------


@pytest.mark.parametrize(
    "entry_options", [{CONF_SHOW_HIDDEN: True, CONF_SHOW_BUTTON_EVENTS: False}]
)
async def test_show_hidden_uses_the_backend_flag(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """The backend marks app-hidden devices with attributes.hidden (enabledByDefault
    false): the option brings them back; a hidden siren output stays off."""
    fake_fyes.entities["dev_pump"].update(enabledByDefault=False)
    fake_fyes.entities["dev_pump"]["attributes"]["hidden"] = True
    fake_fyes.entities["dev_siren"]["attributes"]["hidden"] = True
    await setup_entry(hass, mock_config_entry, fake_fyes)
    registry = er.async_get(hass)
    assert registry.async_get(entity_id(hass, "switch", "dev_pump")).disabled_by is None
    assert registry.async_get(entity_id(hass, "siren", "dev_siren")).disabled_by is not None


async def test_climate_shows_whether_it_is_heating(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """hvac_action from the backend (the heating relay's confirmed state)."""
    fake_fyes.entities["dev_thermo"]["attributes"]["hvac_action"] = "heating"
    await setup_entry(hass, mock_config_entry, fake_fyes)
    assert state_of(hass, "climate", "dev_thermo").attributes["hvac_action"] == "heating"
    fake_fyes.entities["dev_thermo"]["attributes"]["hvac_action"] = "idle"
    await fake_fyes.push_entity("dev_thermo")
    await wait_for(
        hass, lambda: state_of(hass, "climate", "dev_thermo").attributes["hvac_action"] == "idle"
    )


async def test_distance_sensor_keeps_its_class(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """The backend's distance / cm sensors (tank level) keep their device class."""
    fake_fyes.entities["dev_tank"] = _ent(
        "dev_tank",
        "sensor",
        "Water tank level",
        "Στάθμη δεξαμενής",
        None,
        42,
        device_class="distance",
        attributes={"unit": "cm", "state_class": "measurement"},
    )
    await setup_entry(hass, mock_config_entry, fake_fyes)
    state = state_of(hass, "sensor", "dev_tank")
    assert state.attributes["device_class"] == "distance"
    assert state.attributes["unit_of_measurement"] == "cm"


async def test_door_button_shows_the_relay_state_and_gate_is_a_switch(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """A door left energized after a press shows on the button; a gate relay is an
    on/off switch (as in the app) with a gate icon."""
    fake_fyes.entities["dev_door"]["attributes"]["relayState"] = "on"
    fake_fyes.entities["dev_gate"] = _ent(
        "dev_gate",
        "switch",
        "Driveway gate",
        "Καγκελόπορτα",
        None,
        "off",
        attributes={"kind": "RELAY", "role": "gate"},
    )
    await setup_entry(hass, mock_config_entry, fake_fyes)
    assert state_of(hass, "button", "dev_door").attributes["relay_state"] == "on"
    reg = er.async_get(hass).async_get(entity_id(hass, "switch", "dev_gate"))
    assert reg.translation_key == "gate"


async def test_greek_hub_name(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """HA in Greek: the FYES home device is called by the home's Greek name."""
    hass.config.language = "el"
    await setup_entry(hass, mock_config_entry, fake_fyes)
    assert get_device(hass, HOME_ID).name == "Βίλα Ελιά"


async def test_not_delivered_says_what_the_server_said(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """409 NOT_DELIVERED / ALARM_NOT_REACHED: the backend's own sentence reaches the user."""
    fake_fyes.command_error = (409, "NOT_DELIVERED", "The controller did not answer.")
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "switch",
            "turn_on",
            {ATTR_ENTITY_ID: entity_id(hass, "switch", "dev_pump")},
            blocking=True,
        )
    assert err.value.translation_key == "not_delivered"
    assert err.value.translation_placeholders == {"error": "The controller did not answer."}
