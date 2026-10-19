"""Constants for the FYES integration."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "fyes"
MANUFACTURER: Final = "F.YES"

DEFAULT_URL: Final = "https://api.fyes.eu"

CONF_HOME_ID: Final = "home_id"
CONF_TOKEN_ID: Final = "token_id"
CONF_TOKEN_PREFIX: Final = "token_prefix"
CONF_CODE: Final = "code"

CONF_SHOW_BUTTON_EVENTS: Final = "show_button_events"
CONF_SHOW_HIDDEN: Final = "show_hidden"

# Name the integration gives the token it creates (the app lists it under Integrations).
TOKEN_NAME: Final = "Home Assistant"
# The server accepts token names up to this many characters.
TOKEN_NAME_MAX: Final = 60

# Realtime stream format agreed in docs/HOME-ASSISTANT-INTEGRATION.md §3.
REALTIME_FORMAT: Final = "ha1"

# Websocket reconnect backoff: 1 s doubling to 60 s (contract §4). Module level so tests
# can shrink them.
BACKOFF_INITIAL: float = 1.0
BACKOFF_MAX: float = 60.0
# App-level keepalive (contract §3): {t:'ping'} every 30 s, the server answers
# {t:'pong'}; nothing heard for interval + timeout = the socket is dead, reconnect.
# (The server also sends WS ping frames every 30 s; aiohttp answers those itself.)
WS_PING_INTERVAL: float = 30.0
WS_PONG_TIMEOUT: float = 15.0
# The backoff starts over only after a session stayed up this long: a server that
# drops the socket right after subscribing must not cause a reconnect (and a full
# snapshot) every second.
BACKOFF_RESET_AFTER: float = 60.0
# A push re-sync (gateway back, scenario list changed) that fails is retried after
# these delays; still failing, the websocket is reopened (a fresh session re-fetches).
RESYNC_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0, 15.0)
# How long we wait for auth_ok / subscribed before giving up on a connection.
WS_HANDSHAKE_TIMEOUT: float = 10.0
REQUEST_TIMEOUT: float = 15.0

PLATFORMS: Final = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.EVENT,
    Platform.LIGHT,
    Platform.SCENE,
    Platform.SENSOR,
    Platform.SIREN,
    Platform.SWITCH,
]

# Commands (contract §2).
ACTION_TURN_ON: Final = "turn_on"
ACTION_TURN_OFF: Final = "turn_off"
ACTION_SET_BRIGHTNESS: Final = "set_brightness"
ACTION_OPEN: Final = "open"
ACTION_CLOSE: Final = "close"
ACTION_STOP: Final = "stop"
ACTION_SET_TEMPERATURE: Final = "set_temperature"
ACTION_PRESS: Final = "press"

# Security modes (contract §2).
MODE_OFF: Final = "OFF"
MODE_HOME: Final = "HOME"
MODE_NIGHT: Final = "NIGHT"
MODE_AWAY: Final = "AWAY"
