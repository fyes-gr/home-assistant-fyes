"""The alarm panel, FYES scenarios and wall-button events (+ the options flow)."""

from __future__ import annotations

from homeassistant.components.alarm_control_panel import AlarmControlPanelEntityFeature
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fyes.const import CONF_SHOW_BUTTON_EVENTS, CONF_SHOW_HIDDEN, DOMAIN

from .conftest import entity_id, setup_entry, state_of, wait_for
from .fake_fyes import HOME_ID, FakeFyes

PANEL = f"{HOME_ID}_security"


async def _wait_reloaded(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Options changes reload the entry; wait until the new hub is live."""
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    await wait_for(hass, lambda: entry.runtime_data.stats["connects"] >= 1)


async def _call(hass: HomeAssistant, domain: str, service: str, target: str, **data) -> None:
    await hass.services.async_call(domain, service, {ATTR_ENTITY_ID: target, **data}, blocking=True)


async def test_alarm_panel_modes(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """OFF/HOME/NIGHT/AWAY <-> disarmed/armed_*; the state waits for confirmation."""
    target = entity_id(hass, "alarm_control_panel", PANEL)
    state = hass.states.get(target)
    assert state.state == "disarmed"
    assert state.attributes["code_arm_required"] is False
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_NIGHT
        | AlarmControlPanelEntityFeature.ARM_AWAY
    )
    assert state.attributes["friendly_name"] == "Villa Elia Security"

    fake_fyes.auto_echo = False
    await _call(hass, "alarm_control_panel", "alarm_arm_away", target)
    assert fake_fyes.security_requests == ["AWAY"]
    await hass.async_block_till_done()
    assert hass.states.get(target).state == "disarmed"  # not confirmed yet
    await fake_fyes.confirm_security("AWAY")
    await wait_for(hass, lambda: hass.states.get(target).state == "armed_away")

    fake_fyes.auto_echo = True
    await _call(hass, "alarm_control_panel", "alarm_arm_night", target)
    await wait_for(hass, lambda: hass.states.get(target).state == "armed_night")
    await _call(hass, "alarm_control_panel", "alarm_arm_home", target)
    await wait_for(hass, lambda: hass.states.get(target).state == "armed_home")
    await _call(hass, "alarm_control_panel", "alarm_disarm", target)
    await wait_for(hass, lambda: hass.states.get(target).state == "disarmed")
    assert fake_fyes.security_requests == ["AWAY", "NIGHT", "HOME", "OFF"]


async def test_alarm_panel_changed_elsewhere(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Armed from the app or a wall button: HA follows."""
    target = entity_id(hass, "alarm_control_panel", PANEL)
    await fake_fyes.push({"t": "security", "mode": "NIGHT"})
    await wait_for(hass, lambda: hass.states.get(target).state == "armed_night")


async def test_alarm_panel_read_only_member(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """A user who may not change the mode sees it but cannot set it."""
    assert fake_fyes.security is not None
    fake_fyes.security["canSet"] = False
    await setup_entry(hass, mock_config_entry, fake_fyes)
    target = entity_id(hass, "alarm_control_panel", PANEL)
    assert hass.states.get(target).attributes[ATTR_SUPPORTED_FEATURES] == 0
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "alarm_control_panel", "alarm_disarm", target)
    assert err.value.translation_key == "security_not_allowed"
    assert fake_fyes.security_requests == []


async def test_no_security_no_panel(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """A home without security has no alarm panel."""
    fake_fyes.security = None
    await setup_entry(hass, mock_config_entry, fake_fyes)
    assert er.async_get(hass).async_get_entity_id("alarm_control_panel", DOMAIN, PANEL) is None


async def test_scene_activate(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Activating the HA scene runs the FYES scenario."""
    target = entity_id(hass, "scene", "home_villa_scene_sc_movie")
    assert hass.states.get(target).attributes["friendly_name"] == "Villa Elia Movie night"
    await _call(hass, "scene", "turn_on", target)
    assert fake_fyes.scene_activations == ["sc_movie"]


async def test_scene_list_changes(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """{t:'scenes'} re-fetches: new scenarios appear, deleted ones go away."""
    registry = er.async_get(hass)
    fake_fyes.scenes.append({"id": "sc_dinner", "name": "Dinner", "nameEl": "Δείπνο"})
    fake_fyes.scenes = [s for s in fake_fyes.scenes if s["id"] != "sc_alloff"]
    await fake_fyes.push({"t": "scenes"})
    await wait_for(
        hass, lambda: registry.async_get_entity_id("scene", DOMAIN, "home_villa_scene_sc_dinner")
    )
    await wait_for(
        hass,
        lambda: registry.async_get_entity_id("scene", DOMAIN, "home_villa_scene_sc_alloff") is None,
    )
    assert state_of(hass, "scene", "home_villa_scene_sc_dinner").attributes["friendly_name"] == (
        "Villa Elia Dinner"
    )


@pytest.mark.parametrize("entry_options", [{CONF_SHOW_BUTTON_EVENTS: True}])
async def test_wall_button_events(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """With the option on, wall-button presses arrive as HA events."""
    target = entity_id(hass, "event", "dev_wallbtn")
    state = hass.states.get(target)
    assert state.state == STATE_UNKNOWN
    assert state.attributes["event_types"] == ["press"]
    assert state.attributes["device_class"] == "button"

    await fake_fyes.push({"t": "event", "entityId": "dev_wallbtn", "type": "press"})
    await wait_for(hass, lambda: hass.states.get(target).state != STATE_UNKNOWN)
    first = hass.states.get(target)
    assert first.attributes["event_type"] == "press"

    # Unknown event types and other buttons are ignored.
    await fake_fyes.push({"t": "event", "entityId": "dev_wallbtn", "type": "hold"})
    await fake_fyes.push({"t": "event", "entityId": "dev_other", "type": "press"})
    await fake_fyes.push({"t": "event", "entityId": "dev_wallbtn", "type": "press"})
    await wait_for(hass, lambda: hass.states.get(target).state != first.state)
    assert hass.states.get(target).attributes["event_type"] == "press"


async def test_options_flow_enables_button_events(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Turning the option on enables the existing wall-button entities (and off again)."""
    registry = er.async_get(hass)
    target = entity_id(hass, "event", "dev_wallbtn")
    assert registry.async_get(target).disabled_by is er.RegistryEntryDisabler.INTEGRATION

    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SHOW_BUTTON_EVENTS: True, CONF_SHOW_HIDDEN: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options == {CONF_SHOW_BUTTON_EVENTS: True, CONF_SHOW_HIDDEN: False}
    assert registry.async_get(target).disabled_by is None
    await _wait_reloaded(hass, init_integration)
    assert hass.states.get(target) is not None

    # Off again: the integration disables what it enabled.
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SHOW_BUTTON_EVENTS: False, CONF_SHOW_HIDDEN: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert registry.async_get(target).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    await _wait_reloaded(hass, init_integration)
    assert hass.states.get(target) is None


async def test_options_never_override_the_user(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """A user-disabled entity stays disabled when the option is switched on."""
    registry = er.async_get(hass)
    target = entity_id(hass, "event", "dev_wallbtn")
    registry.async_update_entity(target, disabled_by=er.RegistryEntryDisabler.USER)
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SHOW_BUTTON_EVENTS: True, CONF_SHOW_HIDDEN: False}
    )
    assert registry.async_get(target).disabled_by is er.RegistryEntryDisabler.USER
    await _wait_reloaded(hass, init_integration)


async def test_hidden_devices_option(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Hidden devices are disabled unless the option asks for them."""
    fake_fyes.entities["dev_pump"]["attributes"]["hidden"] = True
    await setup_entry(hass, mock_config_entry, fake_fyes)
    registry = er.async_get(hass)
    pump = entity_id(hass, "switch", "dev_pump")
    assert registry.async_get(pump).disabled_by is er.RegistryEntryDisabler.INTEGRATION

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SHOW_BUTTON_EVENTS: False, CONF_SHOW_HIDDEN: True}
    )
    assert registry.async_get(pump).disabled_by is None
    await _wait_reloaded(hass, mock_config_entry)
    assert state_of(hass, "switch", "dev_pump").state == "off"
