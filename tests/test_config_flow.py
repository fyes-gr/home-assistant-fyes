"""Config flow: email + code, token, errors, reauth."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fyes.config_flow import SECTION_ADVANCED
from custom_components.fyes.const import (
    CONF_CODE,
    CONF_HOME_ID,
    CONF_TOKEN_ID,
    CONF_TOKEN_PREFIX,
    DEFAULT_URL,
    DOMAIN,
)

from .fake_fyes import CODE, EMAIL, HOME_ID, JWT, OTHER_HOME_ID, OTHER_TOKEN, TOKEN, FakeFyes

DEAD_URL = "http://127.0.0.1:9"  # nothing listens there


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[AsyncMock]:
    """Do not set the entry up after the flow (that is tested elsewhere)."""
    with patch("custom_components.fyes.async_setup_entry", return_value=True) as mock:
        yield mock


def _server(url: str) -> dict[str, dict[str, str]]:
    return {SECTION_ADVANCED: {CONF_URL: url}}


async def _start(hass: HomeAssistant, step: str) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["email", "token"]
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": step})


async def _email_to_code(hass: HomeAssistant, fake: FakeFyes) -> dict[str, Any]:
    result = await _start(hass, "email")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "email"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: " Owner@Example.com ", SECTION_ADVANCED: {CONF_URL: fake.url + "/"}},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "code"
    assert result["description_placeholders"] == {"email": EMAIL}
    assert fake.otp_requests == [EMAIL]
    return result


async def test_email_flow_pick_home(hass: HomeAssistant, fake_fyes: FakeFyes) -> None:
    """Email -> code -> pick a home -> FYES mints a token; HA keeps only that."""
    result = await _email_to_code(hass, fake_fyes)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "000000"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_code"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "123 456"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "home"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOME_ID: HOME_ID}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Villa Elia"
    assert result["result"].unique_id == HOME_ID
    issued = fake_fyes.issued_tokens[0]
    assert issued["propertyId"] == HOME_ID
    assert issued["name"].startswith("Home Assistant")
    assert result["data"] == {
        CONF_URL: fake_fyes.url,
        CONF_TOKEN: issued["token"],
        CONF_HOME_ID: HOME_ID,
        CONF_TOKEN_ID: issued["id"],
        CONF_TOKEN_PREFIX: issued["prefix"],
        CONF_EMAIL: EMAIL,
    }
    assert JWT not in str(result["data"])  # the app session is never stored


async def test_email_flow_single_home_skips_picker(
    hass: HomeAssistant, fake_fyes: FakeFyes
) -> None:
    """One home: no picker."""
    fake_fyes.user_homes = [HOME_ID]
    result = await _email_to_code(hass, fake_fyes)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOME_ID] == HOME_ID


async def test_email_flow_hides_configured_homes(hass: HomeAssistant, fake_fyes: FakeFyes) -> None:
    """The picker only offers homes that are not set up yet."""
    MockConfigEntry(domain=DOMAIN, unique_id=HOME_ID, data={}).add_to_hass(hass)
    result = await _email_to_code(hass, fake_fyes)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["step_id"] == "home"
    options = result["data_schema"].schema[CONF_HOME_ID].config["options"]
    assert [o["value"] for o in options] == [OTHER_HOME_ID]


async def test_email_flow_already_configured(hass: HomeAssistant, fake_fyes: FakeFyes) -> None:
    """The only home is already set up: abort before minting a token."""
    MockConfigEntry(domain=DOMAIN, unique_id=HOME_ID, data={}).add_to_hass(hass)
    fake_fyes.user_homes = [HOME_ID]
    result = await _email_to_code(hass, fake_fyes)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert fake_fyes.issued_tokens == []


async def test_email_flow_no_homes(hass: HomeAssistant, fake_fyes: FakeFyes) -> None:
    """An account without a home cannot connect anything."""
    fake_fyes.user_homes = []
    result = await _email_to_code(hass, fake_fyes)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_homes"


async def test_email_flow_token_not_allowed(hass: HomeAssistant, fake_fyes: FakeFyes) -> None:
    """The backend refuses a token (e.g. the demo account)."""
    fake_fyes.user_homes = [HOME_ID]
    fake_fyes.forbid_token_creation = True
    result = await _email_to_code(hass, fake_fyes)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_allowed"


async def test_email_step_errors(hass: HomeAssistant, fake_fyes: FakeFyes) -> None:
    """Bad server address, unreachable server, invalid email."""
    result = await _start(hass, "email")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, **_server("ftp://example.com")}
    )
    assert result["errors"] == {"base": "invalid_url"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, **_server(DEAD_URL)}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "not-an-email", **_server(fake_fyes.url)}
    )
    assert result["errors"] == {CONF_EMAIL: "invalid_email"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, **_server(fake_fyes.url)}
    )
    assert result["step_id"] == "code"


async def test_code_step_cannot_connect(hass: HomeAssistant, fake_fyes: FakeFyes) -> None:
    """The server goes away between the two steps."""
    result = await _email_to_code(hass, fake_fyes)
    await fake_fyes.close()
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_code_works_once_retry_keeps_session(
    hass: HomeAssistant, fake_fyes: FakeFyes
) -> None:
    """The code is spent on success; a failed homes call can be retried without it."""
    fake_fyes.user_homes = [HOME_ID]
    result = await _email_to_code(hass, fake_fyes)
    fake_fyes.homes_status = 503
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["errors"] == {"base": "cannot_connect"}
    fake_fyes.homes_status = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_server_defaults_to_fyes_cloud(hass: HomeAssistant) -> None:
    """The Server sits in a collapsed Advanced section and defaults to api.fyes.eu."""
    result = await _start(hass, "token")
    advanced = result["data_schema"].schema[SECTION_ADVANCED]
    assert advanced.options == {"collapsed": True}
    (url_key,) = advanced.schema.schema
    assert url_key == CONF_URL
    assert url_key.default() == DEFAULT_URL == "https://api.fyes.eu"


async def test_token_flow(hass: HomeAssistant, fake_fyes: FakeFyes) -> None:
    """Paste a token: it names its home."""
    result = await _start(hass, "token")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "fyes_wrong", **_server(fake_fyes.url)}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: f"  {TOKEN} ", **_server(fake_fyes.url)}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Villa Elia"
    assert result["result"].unique_id == HOME_ID
    assert result["data"] == {CONF_URL: fake_fyes.url, CONF_TOKEN: TOKEN, CONF_HOME_ID: HOME_ID}


async def test_token_flow_errors(hass: HomeAssistant, fake_fyes: FakeFyes) -> None:
    """Unreachable server; already configured."""
    result = await _start(hass, "token")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: TOKEN, **_server(DEAD_URL)}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    MockConfigEntry(domain=DOMAIN, unique_id=HOME_ID, data={}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: TOKEN, **_server(fake_fyes.url)}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def _start_reauth(hass: HomeAssistant, entry: MockConfigEntry, step: str) -> dict[str, Any]:
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"] == {"home": "Villa Elia"}
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": step})


async def test_reauth_with_token(
    hass: HomeAssistant, fake_fyes: FakeFyes, mock_config_entry: MockConfigEntry
) -> None:
    """A new token for the same home replaces the old one."""
    fake_fyes.tokens["fyes_new"] = HOME_ID
    result = await _start_reauth(hass, mock_config_entry, "token")
    assert SECTION_ADVANCED not in result["data_schema"].schema  # the server is kept
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "fyes_new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_TOKEN] == "fyes_new"
    assert mock_config_entry.data[CONF_URL] == fake_fyes.url
    assert CONF_TOKEN_ID not in mock_config_entry.data  # the old token's id is gone


async def test_reauth_with_token_for_another_home(
    hass: HomeAssistant, fake_fyes: FakeFyes, mock_config_entry: MockConfigEntry
) -> None:
    """A token of another home must not silently re-point the entry."""
    result = await _start_reauth(hass, mock_config_entry, "token")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: OTHER_TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_home"
    assert mock_config_entry.data[CONF_TOKEN] == TOKEN


async def test_reauth_with_email(
    hass: HomeAssistant, fake_fyes: FakeFyes, mock_config_entry: MockConfigEntry
) -> None:
    """Sign in again: no home picker, a fresh token for this home."""
    result = await _start_reauth(hass, mock_config_entry, "email")
    assert result["step_id"] == "email"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_EMAIL: EMAIL})
    assert result["step_id"] == "code"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    issued = fake_fyes.issued_tokens[0]
    assert issued["propertyId"] == HOME_ID
    assert mock_config_entry.data[CONF_TOKEN] == issued["token"]
    assert mock_config_entry.data[CONF_TOKEN_ID] == issued["id"]


async def test_reauth_with_email_lost_access(
    hass: HomeAssistant, fake_fyes: FakeFyes, mock_config_entry: MockConfigEntry
) -> None:
    """The account is no longer a member of this home."""
    fake_fyes.user_homes = [OTHER_HOME_ID]
    result = await _start_reauth(hass, mock_config_entry, "email")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_EMAIL: EMAIL})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CODE: CODE})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_home"
    assert fake_fyes.issued_tokens == []
