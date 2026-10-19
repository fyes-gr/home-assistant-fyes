"""FYES's enabled-by-default follows into HA's entity registry, never over the user.

- a device unhidden in the app is enabled in HA, one hidden in the app is disabled;
- a change found by a snapshot (not pushed) is followed too; with no change, nothing moves;
- the entry's "disable new entities" preference is honoured;
- removing the integration puts what an OPTION enabled back to integration-disabled, so
  adding it again starts from FYES's defaults (newer HA versions restore a removed
  entity's enabled state, 2025.1 does not; HA still restores what the user chose by hand).
"""

from __future__ import annotations

import attr
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fyes.const import (
    CONF_HOME_ID,
    CONF_SHOW_BUTTON_EVENTS,
    CONF_SHOW_HIDDEN,
    CONF_TOKEN_ID,
    CONF_TOKEN_PREFIX,
    DOMAIN,
)

from .conftest import entity_id, setup_entry, wait_for
from .fake_fyes import EMAIL, HOME_ID, TOKEN, FakeFyes

INTEGRATION = er.RegistryEntryDisabler.INTEGRATION
# Newer HA keeps a removed entity's disabled_by and restores it when the entry is added again (2025.1 does not).
HA_RESTORES_DISABLED_BY = "disabled_by" in attr.fields_dict(er.DeletedRegistryEntry)


def _hide(fake: FakeFyes, eid: str, hidden: bool) -> None:
    """As the backend sends a device hidden (or shown again) in the app."""
    fake.entities[eid]["attributes"]["hidden"] = hidden
    fake.entities[eid]["enabledByDefault"] = not hidden


def _disabled_by(hass: HomeAssistant, platform: str, unique_id: str):
    return er.async_get(hass).async_get(entity_id(hass, platform, unique_id)).disabled_by


def _entry(fake: FakeFyes, **kwargs) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Villa Elia",
        unique_id=HOME_ID,
        data={
            CONF_URL: fake.url,
            CONF_TOKEN: TOKEN,
            CONF_HOME_ID: HOME_ID,
            CONF_TOKEN_ID: "tok_0",
            CONF_TOKEN_PREFIX: TOKEN[:9],
            CONF_EMAIL: EMAIL,
        },
        **kwargs,
    )


async def test_unhidden_in_the_app_is_enabled_in_ha(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Hidden at setup -> integration-disabled; shown again in the app -> enabled."""
    _hide(fake_fyes, "dev_pump", True)
    await setup_entry(hass, mock_config_entry, fake_fyes)
    assert _disabled_by(hass, "switch", "dev_pump") is INTEGRATION

    _hide(fake_fyes, "dev_pump", False)
    await fake_fyes.push_entity("dev_pump")
    await wait_for(hass, lambda: _disabled_by(hass, "switch", "dev_pump") is None)


async def test_hidden_in_the_app_is_disabled_in_ha_but_never_over_the_user(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Hidden in the app -> integration-disabled; a user-disabled entity stays the user's."""
    registry = er.async_get(hass)
    assert _disabled_by(hass, "switch", "dev_pump") is None
    _hide(fake_fyes, "dev_pump", True)
    await fake_fyes.push_entity("dev_pump")
    await wait_for(hass, lambda: _disabled_by(hass, "switch", "dev_pump") is INTEGRATION)

    # The user disabled it themselves: showing it again in the app does not enable it.
    registry.async_update_entity(
        entity_id(hass, "switch", "dev_pump"), disabled_by=er.RegistryEntryDisabler.USER
    )
    _hide(fake_fyes, "dev_pump", False)
    await fake_fyes.push_entity("dev_pump")
    await hass.async_block_till_done()
    assert _disabled_by(hass, "switch", "dev_pump") is er.RegistryEntryDisabler.USER


@pytest.mark.parametrize("entry_options", [{CONF_SHOW_HIDDEN: True}])
async def test_hidden_in_the_app_stays_with_show_hidden(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """With "show hidden devices" on, a device hidden in the app stays enabled."""
    _hide(fake_fyes, "dev_pump", True)
    await fake_fyes.push_entity("dev_pump")
    await hass.async_block_till_done()
    coordinator = init_integration.runtime_data
    await wait_for(hass, lambda: coordinator.data.entities["dev_pump"].hidden)
    assert _disabled_by(hass, "switch", "dev_pump") is None


async def test_a_snapshot_finds_an_unhidden_device(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """Changed while HA was not listening (found by the next snapshot, not pushed)."""
    _hide(fake_fyes, "dev_pump", True)
    await setup_entry(hass, mock_config_entry, fake_fyes)
    assert _disabled_by(hass, "switch", "dev_pump") is INTEGRATION
    _hide(fake_fyes, "dev_pump", False)
    await fake_fyes.push({"t": "resync"})
    await wait_for(hass, lambda: _disabled_by(hass, "switch", "dev_pump") is None)


async def test_disable_new_entities_preference_is_honoured(
    hass: HomeAssistant, fake_fyes: FakeFyes
) -> None:
    """With HA's "disable new entities" on, FYES enables nothing by itself."""
    entry = _entry(fake_fyes, pref_disable_new_entities=True)
    _hide(fake_fyes, "dev_pump", True)
    await setup_entry(hass, entry, fake_fyes)
    assert _disabled_by(hass, "switch", "dev_pump") is INTEGRATION
    light = _disabled_by(hass, "light", "dev_ceiling")
    assert light is INTEGRATION  # HA's preference, not ours to undo
    _hide(fake_fyes, "dev_pump", False)
    await fake_fyes.push_entity("dev_pump")
    await fake_fyes.push({"t": "resync"})
    await hass.async_block_till_done()
    await wait_for(hass, lambda: not entry.runtime_data.data.entities["dev_pump"].hidden)
    assert _disabled_by(hass, "switch", "dev_pump") is INTEGRATION
    assert _disabled_by(hass, "light", "dev_ceiling") is INTEGRATION


async def test_remove_and_add_again_starts_from_fyes_defaults(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_fyes: FakeFyes
) -> None:
    """What the options enabled goes back to disabled on removal; the user's own choices
    are restored by HA as usual."""
    registry = er.async_get(hass)
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SHOW_BUTTON_EVENTS: True, CONF_SHOW_HIDDEN: False}
    )
    await hass.async_block_till_done()
    assert _disabled_by(hass, "event", "dev_wallbtn") is None
    # The user enables the siren output by hand (FYES keeps it off by default).
    siren = entity_id(hass, "siren", "dev_siren")
    assert registry.async_get(siren).disabled_by is INTEGRATION
    registry.async_update_entity(siren, disabled_by=None)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()

    fake_fyes.tokens[TOKEN] = HOME_ID  # the removal revoked it; a new sign-in
    again = _entry(fake_fyes)
    await setup_entry(hass, again, fake_fyes)
    assert again.options == {}
    # The wall button was on only because of the option: it starts from FYES's default.
    assert _disabled_by(hass, "event", "dev_wallbtn") is INTEGRATION
    if HA_RESTORES_DISABLED_BY:
        # The user's own enable is HA's to restore (newer HA keeps it).
        assert registry.async_get(entity_id(hass, "siren", "dev_siren")).disabled_by is None
    else:
        assert _disabled_by(hass, "siren", "dev_siren") is INTEGRATION


async def test_nothing_changes_without_a_change_in_fyes(
    hass: HomeAssistant, fake_fyes: FakeFyes
) -> None:
    """Only a change in FYES is followed: entities "disable new entities" left disabled stay
    disabled after the preference is turned off, the entry reloads and a snapshot arrives."""
    entry = _entry(fake_fyes, pref_disable_new_entities=True)
    await setup_entry(hass, entry, fake_fyes)
    assert _disabled_by(hass, "light", "dev_ceiling") is INTEGRATION
    hass.config_entries.async_update_entry(entry, pref_disable_new_entities=False)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await wait_for(hass, lambda: entry.runtime_data.stats["connects"] >= 1)
    snapshots = entry.runtime_data.stats["snapshots"]
    await fake_fyes.push({"t": "resync"})
    await wait_for(hass, lambda: entry.runtime_data.stats["snapshots"] > snapshots)
    assert _disabled_by(hass, "light", "dev_ceiling") is INTEGRATION
    assert _disabled_by(hass, "switch", "dev_pump") is INTEGRATION
