"""The FYES hub: one per config entry (one FYES home).

REST snapshot + a realtime websocket (push, no polling). The websocket reconnects
with backoff (1 s -> 60 s) and re-fetches the snapshot after every (re)subscribe,
so nothing that changed while disconnected is missed. States only ever come from
the server (closed loop: the hardware echo confirms a change), never from a
command we sent.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
import logging
import random
import time
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.json import json_loads

from . import const
from .api import (
    FyesApi,
    FyesAuthError,
    FyesBadRequestError,
    FyesConnectionError,
    FyesError,
    FyesForbiddenError,
    FyesNotDeliveredError,
    FyesNotFoundError,
    FyesOfflineError,
    FyesRateLimitError,
)
from .const import CONF_HOME_ID, DOMAIN, REALTIME_FORMAT
from .models import FyesData, FyesEntity, FyesSecurity, enabled_by_default

type FyesConfigEntry = ConfigEntry[FyesCoordinator]

_LOGGER = logging.getLogger(__name__)

_GATEWAY_KEY = "\x00gateway"
_SECURITY_KEY = "\x00security"


class FyesProtocolError(FyesError):
    """The realtime server said something we did not expect."""


@callback
def async_follow_default(
    ent_reg: er.EntityRegistry,
    reg: er.RegistryEntry,
    before: bool | None,
    after: bool,
    *,
    may_enable: bool = True,
) -> bool:
    """Carry a CHANGE of FYES's enabled-by-default into HA's registry, never over the user.

    Only a change counts (`before` known and different): with no `before` nothing happens,
    so whatever HA, the user or the "disable new entities" preference decided earlier stays.
    Off -> on enables an entity only if the integration disabled it (disabled_by=integration);
    on -> off disables an entity only while it is enabled (a user-disabled one is left alone).
    Returns True when the registry changed.
    """
    if (
        after
        and before is False
        and may_enable
        and reg.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    ):
        ent_reg.async_update_entity(reg.entity_id, disabled_by=None)
        return True
    if before and not after and reg.disabled_by is None:
        ent_reg.async_update_entity(reg.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION)
        return True
    return False


class FyesCoordinator(DataUpdateCoordinator[FyesData]):
    """Holds the home's data and keeps it live."""

    config_entry: FyesConfigEntry

    def __init__(self, hass: HomeAssistant, entry: FyesConfigEntry, api: FyesApi) -> None:
        """Initialize the hub."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=None,  # push: the websocket drives updates
        )
        self.api = api
        self.home_id: str = entry.data[CONF_HOME_ID]
        self.connected = False
        self.hub_device_id: str | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._resync_task: asyncio.Task[None] | None = None
        self._resync_again = False
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._event_listeners: dict[str, set[Callable[[str], None]]] = {}
        # When each entity/gateway/security last changed over the realtime stream;
        # a snapshot requested earlier must not overwrite those newer values.
        self._rt_marks: dict[str, float] = {}
        self.last_snapshot: dict[str, Any] | None = None
        self._last_rx = 0.0
        self.stats: dict[str, Any] = {
            "connects": 0,
            "disconnects": 0,
            "snapshots": 0,
            "messages": 0,
            "last_message_at": None,
            "last_error": None,
        }

    # --- language --------------------------------------------------------------------

    @property
    def greek(self) -> bool:
        """HA runs in Greek: show the Greek FYES names."""
        return (self.hass.config.language or "").lower().startswith("el")

    # --- snapshot --------------------------------------------------------------------

    async def _async_update_data(self) -> FyesData:
        """Fetch the snapshot (first refresh, re-syncs, homeassistant.update_entity)."""
        started = time.monotonic()
        try:
            raw = await self.api.async_get_snapshot(self.home_id)
        except FyesAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except FyesError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="snapshot_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        try:
            fresh = FyesData.from_snapshot(raw)
        except (KeyError, TypeError, ValueError) as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="snapshot_failed",
                translation_placeholders={"error": repr(err)},
            ) from err
        self.stats["snapshots"] += 1
        self.last_snapshot = raw
        fresh = self._keep_newer_realtime(fresh, started)
        self._async_sync_registry(fresh)
        self._async_follow_defaults(self.data, fresh)
        return fresh

    def _keep_newer_realtime(self, fresh: FyesData, started: float) -> FyesData:
        """Keep values the stream delivered after this snapshot was requested."""
        newer = {key for key, at in self._rt_marks.items() if at > started}
        self._rt_marks = {key: self._rt_marks[key] for key in newer}
        old = self.data
        if old is None or not newer:
            return fresh
        entities = dict(fresh.entities)
        home = fresh.home
        security = fresh.security
        for key in newer:
            if key == _GATEWAY_KEY:
                home = replace(home, gateway_online=old.home.gateway_online)
            elif key == _SECURITY_KEY:
                if old.security is not None:
                    security = (
                        replace(security, mode=old.security.mode)
                        if security is not None
                        else old.security
                    )
            elif key in old.entities:
                entities[key] = old.entities[key]
        return replace(fresh, home=home, entities=entities, security=security)

    @callback
    def _async_sync_registry(self, data: FyesData) -> None:
        """Follow the home: drop what it no longer has (stale-devices rule) and take
        over names changed in the app. Only the registry's `name` changes; a name the
        user set in HA (`name_by_user`) always wins. An entity whose FYES device moved
        to another platform (a rename in the app can turn a switch into a light) loses
        its old registry entry; the platform it moved to adds the new one."""
        entry = self.config_entry
        dev_reg = dr.async_get(self.hass)
        greek = self.greek
        for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
            ours = {ident for domain, ident in device.identifiers if domain == DOMAIN}
            if not ours:
                continue
            if self.home_id in ours:
                wanted = data.home.display_name(greek)
            else:
                ent = next((data.entities[i] for i in ours if i in data.entities), None)
                if ent is None:
                    _LOGGER.debug("Removing FYES device that is gone: %s", device.name)
                    dev_reg.async_remove_device(device.id)
                    continue
                wanted = ent.display_name(greek)
            if device.name != wanted:
                dev_reg.async_update_device(device.id, name=wanted)
        ent_reg = er.async_get(self.hass)
        scene_prefix = f"{self.home_id}_scene_"
        for reg in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            uid = reg.unique_id
            gone_scene = (
                reg.domain == "scene"
                and uid.startswith(scene_prefix)
                and uid[len(scene_prefix) :] not in data.scenes
            )
            gone_panel = reg.domain == "alarm_control_panel" and data.security is None
            ent = data.entities.get(uid)
            moved = ent is not None and reg.domain != ent.platform
            if gone_scene or gone_panel or moved:
                ent_reg.async_remove(reg.entity_id)

    @callback
    def _async_follow_defaults(
        self, old: FyesData | None, data: FyesData, only: str | None = None
    ) -> None:
        """A device hidden or shown again in the app follows into HA (async_follow_default),
        whether pushed or found by a re-sync snapshot: shown -> enabled, hidden -> disabled.
        The entry's "disable new entities" preference is honoured. HA itself reloads the
        entry ~30 s after an entity is enabled (that is how it appears); a disabled one goes
        at once.
        """
        if old is None:
            return  # nothing to compare with: no change to follow
        entry = self.config_entry
        options = entry.options
        may_enable = not entry.pref_disable_new_entities
        ent_reg = er.async_get(self.hass)
        for reg in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            if only is not None and reg.unique_id != only:
                continue
            ent = data.entities.get(reg.unique_id)
            if ent is None or reg.domain != ent.platform:
                continue
            prev = old.entities.get(reg.unique_id)
            before = enabled_by_default(options, prev) if prev is not None else None
            if async_follow_default(
                ent_reg, reg, before, enabled_by_default(options, ent), may_enable=may_enable
            ):
                _LOGGER.debug(
                    "FYES default for %s is now %s",
                    reg.entity_id,
                    "enabled" if enabled_by_default(options, ent) else "disabled",
                )

    # --- hub device ------------------------------------------------------------------

    @callback
    def async_register_hub_device(self) -> None:
        """Create the "FYES home" device every FYES device hangs off."""
        home = self.data.home
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.config_entry.entry_id,
            identifiers={(DOMAIN, self.home_id)},
            manufacturer=const.MANUFACTURER,
            model="FYES home",
            name=home.display_name(self.greek),
            sw_version=home.sw_version,
            configuration_url=f"{self.api.base_url}/app/",
        )
        self.hub_device_id = device.id

    # --- realtime --------------------------------------------------------------------

    @callback
    def async_start_listener(self) -> None:
        """Start the websocket listener (cancelled automatically on unload)."""
        self._listener_task = self.config_entry.async_create_background_task(
            self.hass, self._async_listen_forever(), f"{DOMAIN} realtime {self.home_id}"
        )

    async def async_shutdown(self) -> None:
        """Close the websocket and stop the listener."""
        await super().async_shutdown()
        if self._listener_task is not None:
            self._listener_task.cancel()
            self._listener_task = None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()

    async def _async_listen_forever(self) -> None:
        delay = const.BACKOFF_INITIAL
        while True:
            subscribed = False
            started = time.monotonic()
            try:
                subscribed = await self._async_session()
            except (FyesAuthError, ConfigEntryAuthFailed):
                self._async_set_connected(False)
                _LOGGER.warning(
                    "FYES refused the token for %s; sign in again", self.config_entry.title
                )
                self.config_entry.async_start_reauth(self.hass)
                return
            except (
                aiohttp.ClientError,
                TimeoutError,
                FyesError,
                UpdateFailed,
                ValueError,
            ) as err:
                self.stats["last_error"] = repr(err)
                _LOGGER.debug("FYES realtime connection failed: %r", err)
            except Exception as err:
                self.stats["last_error"] = repr(err)
                _LOGGER.exception("Unexpected error in the FYES realtime listener")
            if self.connected:
                self.stats["disconnects"] += 1
                _LOGGER.info("FYES realtime connection lost; reconnecting")
            self._async_set_connected(False)
            # Start over from 1 s only after a session that stayed up: a server that
            # drops us right after subscribing keeps backing off (1 s -> 60 s).
            if subscribed and time.monotonic() - started >= const.BACKOFF_RESET_AFTER:
                delay = const.BACKOFF_INITIAL
            await self._async_backoff_sleep(delay * random.uniform(0.8, 1.2))
            delay = min(delay * 2, const.BACKOFF_MAX)

    async def _async_backoff_sleep(self, delay: float) -> None:
        await asyncio.sleep(delay)

    async def _async_session(self) -> bool:
        """One websocket session. Returns True when it got as far as subscribing."""
        subscribed = False
        async with self.api.session.ws_connect(self.api.ws_url) as ws:
            self._ws = ws
            self._last_rx = time.monotonic()
            keepalive = self.config_entry.async_create_background_task(
                self.hass, self._async_keepalive(ws), f"{DOMAIN} keepalive {self.home_id}"
            )
            try:
                await ws.send_json({"t": "auth", "token": self.api.token})
                await self._async_expect(ws, "auth_ok")
                await ws.send_json(
                    {"t": "subscribe", "propertyId": self.home_id, "format": REALTIME_FORMAT}
                )
                await self._async_expect(ws, "subscribed")
                # Re-sync AFTER subscribing: anything that changes from here on is in
                # the stream, so the snapshot cannot leave a gap.
                data = await self._async_update_data()
                subscribed = True
                self.stats["connects"] += 1
                self.connected = True
                self.async_set_updated_data(data)
                async for msg in ws:
                    self._last_rx = time.monotonic()
                    if msg.type is aiohttp.WSMsgType.TEXT:
                        self._async_handle_text(msg.data)
                    elif msg.type is aiohttp.WSMsgType.ERROR:
                        raise FyesConnectionError(f"websocket error: {ws.exception()!r}")
            finally:
                keepalive.cancel()
                self._ws = None
        return subscribed

    async def _async_keepalive(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Ping every interval; close a socket that has gone silent (half-open)."""
        try:
            while not ws.closed:
                await asyncio.sleep(const.WS_PING_INTERVAL)
                silent_for = time.monotonic() - self._last_rx
                if silent_for > const.WS_PING_INTERVAL + const.WS_PONG_TIMEOUT:
                    _LOGGER.debug("Nothing from FYES for %.0f s; reconnecting", silent_for)
                    await ws.close()
                    return
                await ws.send_json({"t": "ping"})
        except (aiohttp.ClientError, ConnectionError, RuntimeError) as err:
            # The socket is going away; the session loop notices and reconnects.
            _LOGGER.debug("Keepalive stopped: %r", err)

    async def _async_expect(self, ws: aiohttp.ClientWebSocketResponse, wanted: str) -> None:
        async with asyncio.timeout(const.WS_HANDSHAKE_TIMEOUT):
            while True:
                msg = await ws.receive()
                self._last_rx = time.monotonic()
                if msg.type is not aiohttp.WSMsgType.TEXT:
                    raise FyesConnectionError(f"websocket closed while waiting for {wanted}")
                payload = _loads(msg.data)
                kind = payload.get("t") if payload else None
                if kind == wanted:
                    return
                if kind == "auth_err":
                    raise FyesAuthError("realtime auth_err")

    @callback
    def _async_handle_text(self, text: str) -> None:
        payload = _loads(text)
        if payload is None:
            return
        self.stats["messages"] += 1
        self.stats["last_message_at"] = time.time()
        self.async_handle_message(payload)

    @callback
    def async_handle_message(self, msg: dict[str, Any]) -> None:
        """Apply one realtime message (contract §3)."""
        kind = msg.get("t")
        data = self.data
        if data is None:
            return
        if kind == "entity":
            raw = msg.get("entity")
            if not isinstance(raw, dict) or "id" not in raw:
                return
            ent = FyesEntity.from_api(raw)
            self._rt_marks[ent.id] = time.monotonic()
            previous = data.entities.get(ent.id)
            fresh = replace(data, entities={**data.entities, ent.id: ent})
            self.async_set_updated_data(fresh)
            if previous is not None and previous.platform != ent.platform:
                self._async_sync_registry(fresh)  # the old platform's entity goes
            elif previous is not None and (previous.enabled_by_default, previous.hidden) != (
                ent.enabled_by_default,
                ent.hidden,
            ):
                self._async_follow_defaults(data, fresh, only=ent.id)  # hidden/shown in the app
            if previous is not None and (previous.name, previous.name_el) != (
                ent.name,
                ent.name_el,
            ):
                self._async_rename_device(ent)
        elif kind == "entity_removed":
            entity_id = msg.get("entityId")
            if not isinstance(entity_id, str) or entity_id not in data.entities:
                return
            self._rt_marks.pop(entity_id, None)
            fresh = replace(
                data, entities={k: v for k, v in data.entities.items() if k != entity_id}
            )
            self.async_set_updated_data(fresh)
            self._async_sync_registry(fresh)  # its HA device (and entities) go too
        elif kind == "gateway":
            online = bool(msg.get("online"))
            was_online = data.home.gateway_online
            self._rt_marks[_GATEWAY_KEY] = time.monotonic()
            self.async_set_updated_data(
                replace(data, home=replace(data.home, gateway_online=online))
            )
            if online and not was_online:
                # Back online: device availability and states may have moved on.
                self._async_schedule_resync()
        elif kind == "security":
            mode = str(msg.get("mode") or "").upper()
            if not mode:
                return
            self._rt_marks[_SECURITY_KEY] = time.monotonic()
            security = (
                replace(data.security, mode=mode)
                if data.security is not None
                else FyesSecurity.from_api({"mode": mode})
            )
            self.async_set_updated_data(replace(data, security=security))
        elif kind == "event":
            entity_id = msg.get("entityId")
            event_type = msg.get("type")
            if isinstance(entity_id, str) and isinstance(event_type, str):
                for listener in list(self._event_listeners.get(entity_id, ())):
                    listener(event_type)
        elif kind in ("scenes", "resync"):
            self._async_schedule_resync()
        elif kind == "auth_err":
            # Membership removed or token revoked while connected.
            raise FyesAuthError("realtime auth_err")

    @callback
    def _async_rename_device(self, ent: FyesEntity) -> None:
        """A device renamed in the app (pushed over the stream)."""
        dev_reg = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(dev_reg, self.config_entry.entry_id):
            if (DOMAIN, ent.id) in device.identifiers:
                wanted = ent.display_name(self.greek)
                if device.name != wanted:
                    dev_reg.async_update_device(device.id, name=wanted)
                return

    @callback
    def _async_schedule_resync(self) -> None:
        """Re-fetch the snapshot in the background (one at a time; a request that
        arrives meanwhile runs once more afterwards)."""
        if self._resync_task is not None and not self._resync_task.done():
            self._resync_again = True
            return
        self._resync_task = self.config_entry.async_create_background_task(
            self.hass, self._async_resync(), f"{DOMAIN} resync {self.home_id}"
        )

    async def _async_resync(self) -> None:
        """A push re-sync. A failure here never marks the home unavailable (the
        websocket is still live and keeps the states current): it is retried, and if it
        keeps failing the socket is reopened, which re-subscribes and re-fetches."""
        while True:
            self._resync_again = False
            for delay in (0.0, *const.RESYNC_RETRY_DELAYS):
                if delay:
                    await asyncio.sleep(delay)
                try:
                    data = await self._async_update_data()
                except ConfigEntryAuthFailed:
                    self.config_entry.async_start_reauth(self.hass)
                    return
                except UpdateFailed as err:
                    self.stats["last_error"] = repr(err)
                    _LOGGER.debug("FYES re-sync failed: %s", err)
                    continue
                self.async_set_updated_data(data)
                break
            else:
                _LOGGER.info("FYES re-sync keeps failing; reconnecting")
                if self._ws is not None and not self._ws.closed:
                    await self._ws.close()
                return
            if not self._resync_again:
                return

    @callback
    def _async_set_connected(self, connected: bool) -> None:
        if self.connected != connected:
            self.connected = connected
            self.async_update_listeners()

    @callback
    def async_add_event_listener(
        self, entity_id: str, listener: Callable[[str], None]
    ) -> CALLBACK_TYPE:
        """Listen for wall-button presses of one entity."""
        self._event_listeners.setdefault(entity_id, set()).add(listener)

        @callback
        def _remove() -> None:
            listeners = self._event_listeners.get(entity_id)
            if listeners is not None:
                listeners.discard(listener)
                if not listeners:
                    del self._event_listeners[entity_id]

        return _remove

    # --- commands --------------------------------------------------------------------

    async def async_command(self, entity_id: str, action: str, value: float | None = None) -> None:
        """Send a command; the new state arrives later on the stream."""
        await self._async_call(self.api.async_command(self.home_id, entity_id, action, value))

    async def async_activate_scene(self, scene_id: str) -> None:
        """Run a FYES scenario."""
        await self._async_call(self.api.async_activate_scene(self.home_id, scene_id))

    async def async_set_security(self, mode: str) -> None:
        """Set the security mode."""
        await self._async_call(self.api.async_set_security(self.home_id, mode))

    async def _async_call(self, request: Any) -> None:
        try:
            await request
        except FyesOfflineError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="gateway_offline"
            ) from err
        except FyesForbiddenError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="forbidden"
            ) from err
        except FyesAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except (FyesBadRequestError, FyesNotFoundError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_command",
                translation_placeholders={"error": str(err)},
            ) from err
        except FyesRateLimitError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="rate_limited"
            ) from err
        except FyesNotDeliveredError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_delivered",
                translation_placeholders={"error": str(err)},
            ) from err
        except FyesConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        except FyesError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err


def _loads(text: str) -> dict[str, Any] | None:
    try:
        payload = json_loads(text)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None
