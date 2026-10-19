"""Entities per platform, the device registry, and command -> echo -> state."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_STEP,
    HVACMode,
)
from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_COLOR_MODE, ColorMode
from homeassistant.components.sensor import ATTR_STATE_CLASS, SensorStateClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fyes.const import DOMAIN

from .conftest import entity_id, get_device, setup_entry, state_of, wait_for
from .fake_fyes import FakeFyes

EXPECTED = {
    ("light", "dev_ceiling"),
    ("light", "dev_dimmer"),
    ("switch", "dev_socket"),
    ("switch", "dev_pump"),
    ("cover", "cover_dev_shutter_up"),
    ("climate", "dev_thermo"),
    ("sensor", "dev_temp"),
    ("sensor", "dev_hum"),
    ("sensor", "dev_power"),
    ("sensor", "dev_lux"),
    ("sensor", "dev_raw"),
    ("binary_sensor", "dev_contact"),
    ("binary_sensor", "dev_window"),
    ("binary_sensor", "dev_motion"),
    ("binary_sensor", "dev_smoke"),
    ("button", "dev_door"),
    ("siren", "dev_siren"),
    ("event", "dev_wallbtn"),
    ("scene", "home_villa_scene_sc_movie"),
    ("scene", "home_villa_scene_sc_alloff"),
    ("alarm_control_panel", "home_villa_security"),
}
DISABLED_BY_DEFAULT = {"dev_raw", "dev_siren", "dev_wallbtn"}


async def _call(hass: HomeAssistant, domain: str, service: str, target: str, **data) -> None:
    await hass.services.async_call(domain, service, {ATTR_ENTITY_ID: target, **data}, blocking=True)


async def test_every_platform_is_created(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """One entity per FYES entity, with unique_id = the FYES entity id."""
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, init_integration.entry_id)
    assert {(e.domain, e.unique_id) for e in entries} == EXPECTED
    for entry in entries:
        if entry.unique_id in DISABLED_BY_DEFAULT:
            assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            assert hass.states.get(entry.entity_id) is None
        else:
            assert entry.disabled_by is None
            assert entry.has_entity_name
            assert hass.states.get(entry.entity_id) is not None


async def test_device_registry(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """One device per FYES device under the FYES home hub, in its FYES room."""
    hub = get_device(hass, "home_villa")
    assert hub is not None
    assert hub.name == "Villa Elia"
    assert hub.manufacturer == "F.YES"
    assert hub.model == "FYES home"
    assert hub.sw_version == "2026.10.19"

    dimmer = get_device(hass, "dev_dimmer")
    assert dimmer is not None
    assert dimmer.name == "Living room dimmer"
    assert dimmer.manufacturer == "F.YES"
    assert dimmer.model == "Dimmer"
    assert dimmer.via_device_id == hub.id
    area = ar.async_get(hass).async_get_area(dimmer.area_id)
    assert area is not None and area.name == "Living room"

    pump = get_device(hass, "dev_pump")
    assert pump is not None and pump.area_id is None  # no FYES room

    # The scenarios and the alarm belong to the hub device.
    registry = er.async_get(hass)
    scene = registry.async_get(entity_id(hass, "scene", "home_villa_scene_sc_movie"))
    assert scene is not None and scene.device_id == hub.id


async def test_greek_names(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """HA in Greek: the Greek FYES names for devices, rooms and scenarios."""
    hass.config.language = "el"
    await setup_entry(hass, mock_config_entry, fake_fyes)
    dimmer = get_device(hass, "dev_dimmer")
    assert dimmer is not None and dimmer.name == "Ντίμερ σαλονιού"
    area = ar.async_get(hass).async_get_area(dimmer.area_id)
    assert area is not None and area.name == "Σαλόνι"
    assert (
        state_of(hass, "scene", "home_villa_scene_sc_movie")
        .attributes["friendly_name"]
        .endswith("Βραδιά ταινίας")
    )


async def test_light_onoff_and_dimmer(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Relay light = on/off; dimmer = brightness scaled 0-100 <-> 0-255."""
    relay = state_of(hass, "light", "dev_ceiling")
    assert relay.state == STATE_ON
    assert relay.attributes[ATTR_COLOR_MODE] == ColorMode.ONOFF

    dimmer = state_of(hass, "light", "dev_dimmer")
    assert dimmer.attributes[ATTR_COLOR_MODE] == ColorMode.BRIGHTNESS
    assert dimmer.attributes[ATTR_BRIGHTNESS] == 128  # FYES 50 %

    target = entity_id(hass, "light", "dev_dimmer")
    await _call(hass, "light", "turn_on", target, brightness=255)
    assert fake_fyes.commands[-1] == ("dev_dimmer", "set_brightness", 100)
    await wait_for(hass, lambda: hass.states.get(target).attributes[ATTR_BRIGHTNESS] == 255)

    await _call(hass, "light", "turn_on", target, brightness=1)
    assert fake_fyes.commands[-1] == ("dev_dimmer", "set_brightness", 1)
    await _call(hass, "light", "turn_on", target, brightness_pct=30)
    assert fake_fyes.commands[-1] == ("dev_dimmer", "set_brightness", 30)

    await _call(hass, "light", "turn_off", target)
    assert fake_fyes.commands[-1] == ("dev_dimmer", "turn_off", None)
    await wait_for(hass, lambda: hass.states.get(target).state == STATE_OFF)

    await _call(hass, "light", "turn_on", target)
    assert fake_fyes.commands[-1] == ("dev_dimmer", "turn_on", None)


async def test_no_optimistic_state(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """The state changes only when the hardware echo arrives, never on the command."""
    fake_fyes.auto_echo = False
    target = entity_id(hass, "switch", "dev_socket")
    await _call(hass, "switch", "turn_off", target)
    assert fake_fyes.commands[-1] == ("dev_socket", "turn_off", None)
    await hass.async_block_till_done()
    assert hass.states.get(target).state == STATE_ON  # not confirmed yet

    await fake_fyes.echo("dev_socket", "turn_off")
    await wait_for(hass, lambda: hass.states.get(target).state == STATE_OFF)


async def test_command_errors(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """409 OFFLINE and other refusals surface as translated errors; nothing changes."""
    target = entity_id(hass, "switch", "dev_pump")
    fake_fyes.gateway_online = False  # the server knows, HA has not heard yet
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "switch", "turn_on", target)
    assert err.value.translation_key == "gateway_offline"
    assert hass.states.get(target).state == STATE_OFF

    fake_fyes.gateway_online = True
    fake_fyes.tokens.clear()  # token revoked -> 401 -> reauth
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "switch", "turn_on", target)
    assert err.value.translation_key == "auth_failed"
    await hass.async_block_till_done()
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN
    )


async def test_switches(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Sockets are outlets; protected devices say so; on/off round trip."""
    socket = state_of(hass, "switch", "dev_socket")
    assert socket.attributes[ATTR_DEVICE_CLASS] == "outlet"
    assert socket.attributes["protected"] is True
    pump = state_of(hass, "switch", "dev_pump")
    assert pump.attributes[ATTR_DEVICE_CLASS] == "switch"
    assert "protected" not in pump.attributes

    target = entity_id(hass, "switch", "dev_pump")
    await _call(hass, "switch", "turn_on", target)
    await wait_for(hass, lambda: hass.states.get(target).state == STATE_ON)
    assert fake_fyes.commands[-1] == ("dev_pump", "turn_on", None)


async def test_cover(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Shutter as the backend reports it: opening / closing / stopped, no position.

    An idle shutter's position is unknown, so HA keeps Open AND Close usable
    (assumed_state); it is never shown "open" (that greys out Open in the frontend).
    """
    target = entity_id(hass, "cover", "cover_dev_shutter_up")
    state = hass.states.get(target)
    assert state.state == "unknown"
    assert state.attributes[ATTR_DEVICE_CLASS] == "shutter"
    assert state.attributes["assumed_state"] is True

    await _call(hass, "cover", "open_cover", target)
    assert fake_fyes.commands[-1] == ("cover_dev_shutter_up", "open", None)
    await wait_for(hass, lambda: hass.states.get(target).state == "opening")

    await _call(hass, "cover", "stop_cover", target)
    assert fake_fyes.commands[-1] == ("cover_dev_shutter_up", "stop", None)
    await wait_for(hass, lambda: hass.states.get(target).state == "unknown")

    await _call(hass, "cover", "close_cover", target)
    assert fake_fyes.commands[-1] == ("cover_dev_shutter_up", "close", None)
    await wait_for(hass, lambda: hass.states.get(target).state == "closing")

    # A backend that knows the end state (a future position source) is shown as such.
    fake_fyes.entities["cover_dev_shutter_up"]["state"] = "closed"
    await fake_fyes.push_entity("cover_dev_shutter_up")
    await wait_for(hass, lambda: hass.states.get(target).state == "closed")
    # Toggle on a closed shutter opens it.
    await _call(hass, "cover", "toggle", target)
    assert fake_fyes.commands[-1] == ("cover_dev_shutter_up", "open", None)


async def test_climate(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Thermostat: heat/off, setpoint 5-30 step 0.5, current = room sensor."""
    target = entity_id(hass, "climate", "dev_thermo")
    state = hass.states.get(target)
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_TEMPERATURE] == 21.5
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 20.1
    assert state.attributes[ATTR_MIN_TEMP] == 5
    assert state.attributes[ATTR_MAX_TEMP] == 30
    assert state.attributes[ATTR_TARGET_TEMP_STEP] == 0.5

    await _call(hass, "climate", "set_temperature", target, temperature=22.5)
    assert fake_fyes.commands[-1] == ("dev_thermo", "set_temperature", 22.5)
    await wait_for(hass, lambda: hass.states.get(target).attributes[ATTR_TEMPERATURE] == 22.5)

    await _call(hass, "climate", "set_hvac_mode", target, hvac_mode=HVACMode.OFF)
    assert fake_fyes.commands[-1] == ("dev_thermo", "turn_off", None)
    await wait_for(hass, lambda: hass.states.get(target).state == HVACMode.OFF)
    assert hass.states.get(target).attributes[ATTR_TEMPERATURE] == 5  # frost park

    await _call(hass, "climate", "turn_on", target)
    assert fake_fyes.commands[-1] == ("dev_thermo", "turn_on", None)
    await wait_for(hass, lambda: hass.states.get(target).state == HVACMode.HEAT)

    await _call(hass, "climate", "set_temperature", target, temperature=19, hvac_mode=HVACMode.HEAT)
    assert fake_fyes.commands[-1] == ("dev_thermo", "set_temperature", 19.0)

    # Out of range: HA itself refuses (min/max come from FYES), nothing is sent.
    with pytest.raises(ServiceValidationError):
        await _call(hass, "climate", "set_temperature", target, temperature=40)
    assert fake_fyes.commands[-1] == ("dev_thermo", "set_temperature", 19.0)


async def test_sensors(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Units, device classes and state classes as HA expects them."""
    temp = state_of(hass, "sensor", "dev_temp")
    assert float(temp.state) == 20.1
    assert temp.attributes[ATTR_UNIT_OF_MEASUREMENT] == "°C"
    assert temp.attributes[ATTR_DEVICE_CLASS] == "temperature"
    assert temp.attributes[ATTR_STATE_CLASS] == SensorStateClass.MEASUREMENT

    hum = state_of(hass, "sensor", "dev_hum")
    assert (hum.state, hum.attributes[ATTR_UNIT_OF_MEASUREMENT]) == ("55", "%")
    assert hum.attributes[ATTR_DEVICE_CLASS] == "humidity"

    power = state_of(hass, "sensor", "dev_power")
    assert (power.state, power.attributes[ATTR_UNIT_OF_MEASUREMENT]) == ("1250.5", "W")

    lux = state_of(hass, "sensor", "dev_lux")
    assert (lux.state, lux.attributes[ATTR_UNIT_OF_MEASUREMENT]) == ("300", "lx")
    assert lux.attributes[ATTR_DEVICE_CLASS] == "illuminance"


async def test_sensor_live_update_and_unknown(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """A reading pushed over the stream lands; a missing reading is unknown."""
    target = entity_id(hass, "sensor", "dev_temp")
    fake_fyes.entities["dev_temp"]["state"] = 23.4
    await fake_fyes.push_entity("dev_temp")
    await wait_for(hass, lambda: hass.states.get(target).state == "23.4")
    fake_fyes.entities["dev_temp"]["state"] = None
    await fake_fyes.push_entity("dev_temp")
    await wait_for(hass, lambda: hass.states.get(target).state == STATE_UNKNOWN)


async def test_binary_sensors(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Door contact "on" = open; motion follows the stream."""
    door = state_of(hass, "binary_sensor", "dev_contact")
    assert door.state == STATE_ON
    assert door.attributes[ATTR_DEVICE_CLASS] == BinarySensorDeviceClass.DOOR
    window = state_of(hass, "binary_sensor", "dev_window")
    assert window.attributes[ATTR_DEVICE_CLASS] == BinarySensorDeviceClass.WINDOW
    assert state_of(hass, "binary_sensor", "dev_smoke").attributes[ATTR_DEVICE_CLASS] == "smoke"

    motion = entity_id(hass, "binary_sensor", "dev_motion")
    assert hass.states.get(motion).state == STATE_OFF
    fake_fyes.entities["dev_motion"]["state"] = "on"
    await fake_fyes.push_entity("dev_motion")
    await wait_for(hass, lambda: hass.states.get(motion).state == STATE_ON)


async def test_door_button(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """A door is a button named Open: pressing it sends the door pulse."""
    target = entity_id(hass, "button", "dev_door")
    assert hass.states.get(target).attributes["friendly_name"] == "Front door Open"
    await _call(hass, "button", "press", target)
    assert fake_fyes.commands[-1] == ("dev_door", "press", None)


async def test_siren_enabled_by_user(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Sirens are disabled by default; once enabled they work like a switch."""
    registry = er.async_get(hass)
    target = entity_id(hass, "siren", "dev_siren")
    registry.async_update_entity(target, disabled_by=None)
    assert await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    await wait_for(hass, lambda: init_integration.runtime_data.stats["connects"] >= 1)
    assert hass.states.get(target).state == STATE_OFF
    await _call(hass, "siren", "turn_on", target)
    assert fake_fyes.commands[-1] == ("dev_siren", "turn_on", None)
    await wait_for(hass, lambda: hass.states.get(target).state == STATE_ON)
