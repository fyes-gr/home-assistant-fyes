"""Config flow for FYES.

Default: email -> 6-digit code (the app's own OTP endpoints) -> pick the home ->
FYES creates an integration token for that home. HA keeps ONLY that token, never
the app session. Fallback: paste a token made in the app (Settings -> Integrations
-> Home Assistant). Re-auth on 401 (an email re-auth revokes the token it replaces);
reconfigure changes the Server; options for wall-button events and hidden devices.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_EMAIL, CONF_TOKEN, CONF_URL
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    FyesApi,
    FyesAuthError,
    FyesBadRequestError,
    FyesConnectionError,
    FyesError,
    FyesForbiddenError,
    FyesRateLimitError,
    FyesTooManyTokensError,
    normalize_url,
)
from .const import (
    CONF_CODE,
    CONF_HOME_ID,
    CONF_SHOW_BUTTON_EVENTS,
    CONF_SHOW_HIDDEN,
    CONF_TOKEN_ID,
    CONF_TOKEN_PREFIX,
    DEFAULT_URL,
    DOMAIN,
    TOKEN_NAME,
    TOKEN_NAME_MAX,
)
from .coordinator import async_follow_default
from .models import enabled_by_default

_LOGGER = logging.getLogger(__name__)

# Collapsed form section holding the Server field (self-hosted FYES: Pi hub, tests).
SECTION_ADVANCED = "advanced"

_OptionsFlowBase: type[config_entries.OptionsFlow] = getattr(
    config_entries, "OptionsFlowWithReload", config_entries.OptionsFlow
)


def _valid_url(url: str) -> bool:
    return url.startswith(("https://", "http://")) and len(url) > len("https://")


def token_name(location_name: str | None) -> str:
    """'Home Assistant (<location>)', cut to what the server accepts (60 characters)."""
    if not location_name or not location_name.strip():
        return TOKEN_NAME
    name = f"{TOKEN_NAME} ({location_name.strip()})"
    if len(name) <= TOKEN_NAME_MAX:
        return name
    room = TOKEN_NAME_MAX - len(TOKEN_NAME) - len(" (…)")
    return f"{TOKEN_NAME} ({location_name.strip()[:room].rstrip()}…)"


class FyesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Sign in to FYES and pick a home."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._url: str = DEFAULT_URL
        self._email: str | None = None
        self._jwt: str | None = None
        self._homes: dict[str, dict[str, Any]] = {}

    # --- helpers ---------------------------------------------------------------------

    def _api(self) -> FyesApi:
        return FyesApi(async_get_clientsession(self.hass), self._url)

    def _url_field(self, schema: dict[Any, Any]) -> dict[Any, Any]:
        """A collapsed "Advanced" section with the Server (not on re-auth: kept)."""
        if self.source not in (SOURCE_REAUTH, SOURCE_RECONFIGURE):
            schema[vol.Required(SECTION_ADVANCED)] = section(
                vol.Schema(
                    {
                        vol.Optional(CONF_URL, default=self._url): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.URL)
                        )
                    }
                ),
                {"collapsed": True},
            )
        return schema

    def _take_url(self, user_input: dict[str, Any], errors: dict[str, str]) -> bool:
        advanced = user_input.get(SECTION_ADVANCED) or {}
        if CONF_URL not in advanced:
            return True
        url = normalize_url(advanced[CONF_URL])
        if not _valid_url(url):
            errors["base"] = "invalid_url"
            return False
        self._url = url
        return True

    # --- entry points ----------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose how to sign in."""
        return self.async_show_menu(step_id="user", menu_options=["email", "token"])

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The token stopped working (revoked, or the membership is gone)."""
        self._url = entry_data.get(CONF_URL, DEFAULT_URL)
        self._email = entry_data.get(CONF_EMAIL)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how to sign in again."""
        return self.async_show_menu(
            step_id="reauth_confirm",
            menu_options=["email", "token"],
            description_placeholders={"home": self._get_reauth_entry().title},
        )

    # --- email + code ----------------------------------------------------------------

    async def async_step_email(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the email; FYES mails a 6-digit code."""
        errors: dict[str, str] = {}
        if user_input is not None and self._take_url(user_input, errors):
            email = user_input[CONF_EMAIL].strip().lower()
            try:
                await self._api().async_request_code(email)
            except FyesBadRequestError:
                errors[CONF_EMAIL] = "invalid_email"
            except FyesRateLimitError:
                errors["base"] = "rate_limited"
            except FyesConnectionError:
                errors["base"] = "cannot_connect"
            except FyesError:
                _LOGGER.exception("Unexpected answer from FYES while requesting a code")
                errors["base"] = "unknown"
            else:
                self._email = email
                return await self.async_step_code()
        schema = self._url_field(
            {
                vol.Required(CONF_EMAIL, default=self._email or ""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="email")
                )
            }
        )
        return self.async_show_form(step_id="email", data_schema=vol.Schema(schema), errors=errors)

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the emailed code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            assert self._email is not None
            code = "".join(ch for ch in str(user_input[CONF_CODE]) if ch.isdigit())
            api = self._api()
            try:
                if self._jwt is None:  # a code works once: keep the session for a retry
                    if len(code) != 6:
                        raise FyesAuthError("not 6 digits")
                    self._jwt = await api.async_verify_code(self._email, code)
                homes = await api.async_get_homes(token=self._jwt)
            except FyesAuthError:
                # A wrong code, or the session expired while the form was open: either
                # way the next submit must verify a (new) code again.
                self._jwt = None
                errors["base"] = "invalid_code"
            except FyesForbiddenError:
                self._jwt = None
                errors["base"] = "not_allowed"
            except FyesRateLimitError:
                errors["base"] = "rate_limited"
            except FyesConnectionError:
                errors["base"] = "cannot_connect"
            except FyesError:
                _LOGGER.exception("Unexpected answer from FYES while checking the code")
                errors["base"] = "unknown"
            else:
                self._homes = {home["id"]: home for home in homes}
                if self.source == SOURCE_REAUTH:
                    home_id = self._get_reauth_entry().unique_id
                    if home_id not in self._homes:
                        return self.async_abort(reason="wrong_home")
                    return await self._async_finish_with_jwt(home_id)
                if not self._homes:
                    return self.async_abort(reason="no_homes")
                if len(self._homes) == 1:
                    return await self._async_finish_with_jwt(next(iter(self._homes)))
                return await self.async_step_home()
        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CODE): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="one-time-code")
                    )
                }
            ),
            errors=errors,
            description_placeholders={"email": self._email or ""},
        )

    async def async_step_home(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick one of the user's homes."""
        if user_input is not None:
            return await self._async_finish_with_jwt(user_input[CONF_HOME_ID])
        configured = {
            entry.unique_id for entry in self._async_current_entries(include_ignore=False)
        }
        options = [
            SelectOptionDict(value=home_id, label=str(home.get("name") or home_id))
            for home_id, home in self._homes.items()
            if home_id not in configured
        ]
        if not options:
            return self.async_abort(reason="already_configured")
        return self.async_show_form(
            step_id="home",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOME_ID): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                    )
                }
            ),
        )

    async def _async_finish_with_jwt(self, home_id: str) -> ConfigFlowResult:
        """Mint the integration token for the chosen home and store only that."""
        jwt, self._jwt = self._jwt, None  # the app session is never kept
        assert jwt is not None
        await self.async_set_unique_id(home_id)
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_home")
        else:
            self._abort_if_unique_id_configured()
        api = self._api()
        try:
            created = await api.async_create_token(
                jwt, home_id, token_name(self.hass.config.location_name)
            )
        except FyesTooManyTokensError:
            return self.async_abort(reason="too_many_tokens")
        except FyesForbiddenError:
            return self.async_abort(reason="not_allowed")
        except FyesConnectionError:
            return self.async_abort(reason="cannot_connect")
        except FyesError:
            _LOGGER.exception("Unexpected answer from FYES while creating the token")
            return self.async_abort(reason="unknown")
        if self.source == SOURCE_REAUTH:
            # The token this one replaces is revoked (usually it already is: that is
            # why we are here), so re-auths never pile up live tokens on the account.
            old_id = self._get_reauth_entry().data.get(CONF_TOKEN_ID)
            if isinstance(old_id, str) and old_id and old_id != created.get("id"):
                try:
                    await api.async_revoke_token(jwt, old_id)
                except FyesError as err:
                    _LOGGER.debug("Could not revoke the replaced FYES token: %s", err)
        data = {
            CONF_URL: self._url,
            CONF_TOKEN: created["token"],
            CONF_HOME_ID: home_id,
            CONF_TOKEN_ID: created.get("id"),
            CONF_TOKEN_PREFIX: created.get("prefix"),
            CONF_EMAIL: self._email,
        }
        return await self._async_create_or_update(home_id, data)

    # --- token -----------------------------------------------------------------------

    async def async_step_token(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Paste a token created in the FYES app."""
        errors: dict[str, str] = {}
        if user_input is not None and self._take_url(user_input, errors):
            token = user_input[CONF_TOKEN].strip()
            try:
                homes = await self._api().async_get_homes(token=token)
            except FyesAuthError:
                errors["base"] = "invalid_auth"
            except FyesRateLimitError:
                errors["base"] = "rate_limited"
            except FyesConnectionError:
                errors["base"] = "cannot_connect"
            except FyesError:
                _LOGGER.exception("Unexpected answer from FYES while checking the token")
                errors["base"] = "unknown"
            else:
                if not homes:
                    return self.async_abort(reason="no_homes")
                home_id = homes[0]["id"]  # a token belongs to exactly one home
                self._homes = {home_id: homes[0]}
                await self.async_set_unique_id(home_id)
                if self.source == SOURCE_REAUTH:
                    self._abort_if_unique_id_mismatch(reason="wrong_home")
                else:
                    self._abort_if_unique_id_configured()
                data = {CONF_URL: self._url, CONF_TOKEN: token, CONF_HOME_ID: home_id}
                return await self._async_create_or_update(home_id, data)
        schema = self._url_field(
            {
                vol.Required(CONF_TOKEN): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                )
            }
        )
        return self.async_show_form(step_id="token", data_schema=vol.Schema(schema), errors=errors)

    # --- reconfigure (the Server, e.g. a local hub moved) -----------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the Server; the stored token must still open this same home there."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            if not _valid_url(url):
                errors["base"] = "invalid_url"
            else:
                api = FyesApi(async_get_clientsession(self.hass), url)
                try:
                    homes = await api.async_get_homes(token=entry.data[CONF_TOKEN])
                except FyesAuthError:
                    errors["base"] = "invalid_auth"
                except FyesRateLimitError:
                    errors["base"] = "rate_limited"
                except FyesConnectionError:
                    errors["base"] = "cannot_connect"
                except FyesError:
                    _LOGGER.exception("Unexpected answer from FYES while checking the server")
                    errors["base"] = "unknown"
                else:
                    if entry.unique_id not in {home["id"] for home in homes}:
                        return self.async_abort(reason="wrong_home")
                    return self.async_update_reload_and_abort(entry, data_updates={CONF_URL: url})
        current = entry.data.get(CONF_URL, DEFAULT_URL)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL, default=current): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.URL)
                    )
                }
            ),
            errors=errors,
            description_placeholders={"home": entry.title},
        )

    async def _async_create_or_update(self, home_id: str, data: dict[str, Any]) -> ConfigFlowResult:
        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            merged = {**entry.data, **data}
            if CONF_TOKEN_ID not in data:
                # A pasted token: the previous token's id/prefix no longer apply.
                merged.pop(CONF_TOKEN_ID, None)
                merged.pop(CONF_TOKEN_PREFIX, None)
            return self.async_update_reload_and_abort(entry, data=merged)
        title = str(self._homes.get(home_id, {}).get("name") or home_id)
        return self.async_create_entry(title=title, data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> config_entries.OptionsFlow:
        """Return the options flow."""
        return FyesOptionsFlow()


class FyesOptionsFlow(_OptionsFlowBase):  # type: ignore[misc,valid-type]
    """Wall-button events and hidden devices."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the two switches."""
        if user_input is not None:
            self._async_apply_to_registry(user_input)
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SHOW_BUTTON_EVENTS,
                        default=options.get(CONF_SHOW_BUTTON_EVENTS, False),
                    ): bool,
                    vol.Required(
                        CONF_SHOW_HIDDEN, default=options.get(CONF_SHOW_HIDDEN, False)
                    ): bool,
                }
            ),
        )

    @callback
    def _async_apply_to_registry(self, new_options: dict[str, Any]) -> None:
        """Enable/disable the affected entities that already exist.

        Only entities the integration itself disabled are enabled, and only entities
        nobody touched are disabled, so a user's own choice always wins.
        """
        entry = self.config_entry
        coordinator = getattr(entry, "runtime_data", None)
        if coordinator is None or coordinator.data is None:
            return
        ent_reg = er.async_get(self.hass)
        for reg in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            ent = coordinator.data.entities.get(reg.unique_id)
            if ent is None:
                continue
            before = enabled_by_default(entry.options, ent)
            after = enabled_by_default(new_options, ent)
            if before != after:  # the user's own switch: enables even with "disable new entities"
                async_follow_default(ent_reg, reg, before, after)
