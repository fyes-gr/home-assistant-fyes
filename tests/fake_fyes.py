"""A fake FYES Cloud implementing docs/HOME-ASSISTANT-INTEGRATION.md (REST + WSS).

It behaves like the real backend where the integration depends on it: tokens act
for one home, commands answer 202 and the new state only arrives later over the
realtime socket (the "hardware echo"), an offline gateway answers 409 OFFLINE.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import copy
import secrets
from typing import Any

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

HOME_ID = "home_villa"
OTHER_HOME_ID = "home_beach"
TOKEN = "fyes_" + "A1b2C3d4E5" * 4
OTHER_TOKEN = "fyes_" + "Z9y8X7w6V5" * 4
EMAIL = "owner@example.com"
CODE = "123456"
JWT = "jwt-owner-session"


def _ent(
    eid: str,
    platform: str,
    name: str,
    name_el: str,
    room: str | None,
    state: Any,
    *,
    device_class: str | None = None,
    attributes: dict[str, Any] | None = None,
    enabled: bool = True,
    protected: bool = False,
    available: bool = True,
) -> dict[str, Any]:
    return {
        "id": eid,
        "platform": platform,
        "deviceClass": device_class,
        "name": name,
        "nameEl": name_el,
        "roomId": room,
        "available": available,
        "enabledByDefault": enabled,
        "protected": protected,
        "state": state,
        "attributes": attributes or {},
    }


def default_entities() -> dict[str, dict[str, Any]]:
    """Villa Elia: one entity of every platform (and the interesting variants)."""
    ents = [
        _ent(
            "dev_ceiling",
            "light",
            "Kitchen ceiling light",
            "Φως οροφής κουζίνας",
            "r_kitchen",
            "on",
            attributes={"kind": "RELAY"},
        ),
        _ent(
            "dev_dimmer",
            "light",
            "Living room dimmer",
            "Ντίμερ σαλονιού",
            "r_living",
            "on",
            attributes={"kind": "DIMMER", "brightness": 50},
        ),
        _ent(
            "dev_socket",
            "switch",
            "Fridge socket",
            "Πρίζα ψυγείου",
            "r_kitchen",
            "on",
            device_class="outlet",
            protected=True,
            attributes={"kind": "RELAY"},
        ),
        _ent(
            "dev_pump",
            "switch",
            "Pool pump",
            "Αντλία πισίνας",
            None,
            "off",
            attributes={"kind": "RELAY"},
        ),
        _ent(
            "cover_dev_shutter_up",
            "cover",
            "Bedroom shutter",
            "Ρολό υπνοδωματίου",
            "r_bed",
            "stopped",  # the real backend: opening / closing / stopped (no position)
            device_class="shutter",
        ),
        _ent(
            "dev_thermo",
            "climate",
            "Living room thermostat",
            "Θερμοστάτης σαλονιού",
            "r_living",
            "heat",
            attributes={
                "kind": "THERMOSTAT",
                "hvac_mode": "heat",
                "target_temperature": 21.5,
                "current_temperature": 20.1,
                "min_temp": 5,
                "max_temp": 30,
                "target_temp_step": 0.5,
            },
        ),
        _ent(
            "dev_temp",
            "sensor",
            "Living room temperature",
            "Θερμοκρασία σαλονιού",
            "r_living",
            20.1,
            device_class="temperature",
            attributes={"kind": "ANALOG_INPUT", "unit": "°C", "state_class": "measurement"},
        ),
        _ent(
            "dev_hum",
            "sensor",
            "Bathroom humidity",
            "Υγρασία μπάνιου",
            None,
            55,
            device_class="humidity",
            attributes={"unit": "%"},
        ),
        _ent(
            "dev_power",
            "sensor",
            "Main power",
            "Κατανάλωση",
            None,
            1250.5,
            device_class="power",
            attributes={"unit": "W"},
        ),
        _ent(
            "dev_lux",
            "sensor",
            "Garden light level",
            "Φωτεινότητα κήπου",
            None,
            300,
            device_class="illuminance",
            attributes={"unit": "lx"},
        ),
        _ent(
            "dev_raw", "sensor", "Analog input 3", "Αναλογική είσοδος 3", None, 512, enabled=False
        ),
        _ent(
            "dev_contact",
            "binary_sensor",
            "Front door contact",
            "Επαφή εξώπορτας",
            "r_entrance",
            "on",
            device_class="door",
        ),
        _ent(
            "dev_window",
            "binary_sensor",
            "Bedroom window",
            "Παράθυρο υπνοδωματίου",
            "r_bed",
            "off",
            device_class="window",
        ),
        _ent(
            "dev_motion",
            "binary_sensor",
            "Hall motion",
            "Κίνηση διαδρόμου",
            "r_entrance",
            "off",
            device_class="motion",
        ),
        _ent(
            "dev_smoke",
            "binary_sensor",
            "Kitchen smoke",
            "Καπνός κουζίνας",
            "r_kitchen",
            "off",
            device_class="smoke",
        ),
        _ent("dev_door", "button", "Front door", "Εξώπορτα", "r_entrance", None),
        _ent("dev_siren", "siren", "Siren", "Σειρήνα", None, "off", enabled=False),
        _ent(
            "dev_wallbtn",
            "event",
            "Kitchen wall button",
            "Διακόπτης τοίχου κουζίνας",
            "r_kitchen",
            None,
            device_class="button",
            enabled=False,
        ),
    ]
    return {e["id"]: e for e in ents}


class FakeFyes:
    """The fake server. Tests poke its attributes to steer behaviour."""

    def __init__(self) -> None:
        """Set up routes and the Villa Elia data."""
        self.app = web.Application(middlewares=[self._json_only])
        r = self.app.router
        r.add_post("/auth/otp/request", self._otp_request)
        r.add_post("/auth/otp/verify", self._otp_verify)
        r.add_post("/integrations/tokens", self._create_token)
        r.add_delete("/integrations/tokens/{token_id}", self._revoke_token)
        r.add_delete("/integrations/v1/token", self._revoke_self)
        r.add_get("/integrations/v1/homes", self._homes)
        r.add_get("/integrations/v1/homes/{home}", self._snapshot)
        r.add_post("/integrations/v1/homes/{home}/entities/{entity}/command", self._command)
        r.add_post("/integrations/v1/homes/{home}/scenes/{scene}/activate", self._activate)
        r.add_put("/integrations/v1/homes/{home}/security", self._security)
        r.add_get("/realtime", self._realtime)
        self.server: TestServer | None = None
        self.url = ""

        self.homes: dict[str, dict[str, Any]] = {
            HOME_ID: {
                "id": HOME_ID,
                "name": "Villa Elia",
                "nameEl": "Βίλα Ελιά",
                "timezone": "Europe/Athens",
            },
            OTHER_HOME_ID: {
                "id": OTHER_HOME_ID,
                "name": "Beach house",
                "timezone": "Europe/Athens",
            },
        }
        self.rooms = [
            {"id": "r_kitchen", "name": "Kitchen", "nameEl": "Κουζίνα"},
            {"id": "r_living", "name": "Living room", "nameEl": "Σαλόνι"},
            {"id": "r_bed", "name": "Bedroom", "nameEl": "Υπνοδωμάτιο"},
            {"id": "r_entrance", "name": "Entrance", "nameEl": "Είσοδος"},
        ]
        self.entities = default_entities()
        self.scenes = [
            {"id": "sc_movie", "name": "Movie night", "nameEl": "Βραδιά ταινίας"},
            {"id": "sc_alloff", "name": "All off", "nameEl": "Όλα κλειστά"},
        ]
        self.security: dict[str, Any] | None = {
            "mode": "OFF",
            "modes": ["OFF", "HOME", "NIGHT", "AWAY"],
            "canSet": True,
        }
        self.gateway_online = True

        # Auth: integration tokens act for one home; the JWT user owns user_homes.
        self.tokens: dict[str, str] = {TOKEN: HOME_ID, OTHER_TOKEN: OTHER_HOME_ID}
        self.token_ids: dict[str, str] = {TOKEN: "tok_0", OTHER_TOKEN: "tok_other"}
        self.user_homes: list[str] = [HOME_ID, OTHER_HOME_ID]
        self.forbid_token_creation = False
        # TOO_MANY_TOKENS once this many tokens were issued (None = no cap).
        self.max_tokens: int | None = None
        # False = the app session (JWT) expired: every call with it answers 401.
        self.jwt_valid = True

        # Behaviour knobs.
        self.auto_echo = True
        self.echo_delay = 0.01
        self.snapshot_status: int | None = None
        # Fail the next N snapshot requests with 502 (a transient server error).
        self.snapshot_fail_times = 0
        # Answer every command with (status, code, message) instead of 202.
        self.command_error: tuple[int, str, str | None] | None = None
        self.homes_status: int | None = None
        self.reject_ws_auth = False
        self.refuse_ws = False
        self.ignore_pings = False
        self.drop_after_subscribe = False
        self.pings = 0

        # What happened.
        self.otp_requests: list[str] = []
        self.verify_calls = 0
        self.revoked: list[str] = []  # token ids revoked with the app session
        self.self_revoked: list[str] = []  # tokens that revoked themselves
        self.issued_tokens: list[dict[str, Any]] = []
        self.commands: list[tuple[str, str, Any]] = []
        self.scene_activations: list[str] = []
        self.security_requests: list[str] = []
        self.snapshot_requests = 0
        self.ws_connections = 0
        self.unsupported_media = 0
        self.ws_formats: list[Any] = []
        self.subscribers: list[web.WebSocketResponse] = []
        self._tasks: set[asyncio.Task[None]] = set()

    @web.middleware
    async def _json_only(self, request: web.Request, handler: Any) -> web.StreamResponse:
        """Like the real server (Fastify): a POST/PUT body that is not JSON is refused
        with 415 before the route runs (aiohttp labels an EMPTY body octet-stream)."""
        if (
            request.method in ("POST", "PUT", "PATCH")
            and request.content_type != "application/json"
        ):
            self.unsupported_media += 1
            return web.json_response({"error": "Unsupported Media Type"}, status=415)
        return await handler(request)

    # --- lifecycle -------------------------------------------------------------------

    async def start(self) -> None:
        """Listen on 127.0.0.1 (the only host the HA test harness allows)."""
        self.server = TestServer(self.app, host="127.0.0.1")
        await self.server.start_server()
        self.url = str(self.server.make_url("")).rstrip("/")

    async def close(self) -> None:
        """Stop everything."""
        for task in list(self._tasks):
            task.cancel()
        await self.drop_connections()
        if self.server is not None:
            await self.server.close()

    # --- helpers for tests -----------------------------------------------------------

    def entity_json(self, eid: str) -> dict[str, Any]:
        """The entity as the backend's mapper would send it right now."""
        ent = copy.deepcopy(self.entities[eid])
        ent["available"] = bool(ent["available"] and self.gateway_online)
        return ent

    def snapshot(self, home_id: str = HOME_ID) -> dict[str, Any]:
        """GET /integrations/v1/homes/:id."""
        home = self.homes[home_id]
        return {
            "home": {**home, "gatewayOnline": self.gateway_online, "swVersion": "2026.10.19"},
            "rooms": copy.deepcopy(self.rooms),
            "entities": [self.entity_json(eid) for eid in self.entities],
            "scenes": copy.deepcopy(self.scenes),
            "security": copy.deepcopy(self.security),
        }

    async def push(self, msg: dict[str, Any]) -> None:
        """Send one realtime message to every subscriber."""
        for ws in list(self.subscribers):
            if not ws.closed:
                await ws.send_json(msg)

    async def push_entity(self, eid: str) -> None:
        """Push the current state of one entity."""
        await self.push({"t": "entity", "entity": self.entity_json(eid)})

    async def set_gateway(self, online: bool) -> None:
        """Gateway goes offline/online (pushed like the backend does)."""
        self.gateway_online = online
        await self.push({"t": "gateway", "online": online})

    async def echo(self, eid: str, action: str, value: Any = None) -> None:
        """Apply a command to the fake hardware and push the confirmed state."""
        if self._apply(eid, action, value):
            await self.push_entity(eid)

    async def drop_connections(self) -> None:
        """Close every realtime socket (network blip / server restart)."""
        subscribers, self.subscribers = list(self.subscribers), []
        for ws in subscribers:
            await ws.close()

    # --- auth ------------------------------------------------------------------------

    def _bearer(self, request: web.Request) -> str | None:
        header = request.headers.get("Authorization", "")
        return header[7:] if header.startswith("Bearer ") else None

    def _homes_for(self, request: web.Request) -> list[str] | None:
        bearer = self._bearer(request)
        if bearer is None:
            return None
        if bearer in self.tokens:
            return [self.tokens[bearer]]
        if bearer == JWT and self.jwt_valid:
            return list(self.user_homes)
        return None

    @staticmethod
    def _error(status: int, code: str) -> web.Response:
        return web.json_response({"error": code}, status=status)

    def _check_home(self, request: web.Request) -> web.Response | str:
        homes = self._homes_for(request)
        if homes is None:
            return self._error(401, "UNAUTHORIZED")
        home_id = request.match_info["home"]
        if home_id not in homes:
            return self._error(403, "FORBIDDEN")
        return home_id

    # --- routes ----------------------------------------------------------------------

    async def _otp_request(self, request: web.Request) -> web.Response:
        body = await request.json()
        email = str(body.get("email", ""))
        if "@" not in email:
            return self._error(400, "INVALID_BODY")
        self.otp_requests.append(email)
        return web.json_response({"ok": True})

    async def _otp_verify(self, request: web.Request) -> web.Response:
        self.verify_calls += 1
        body = await request.json()
        if body.get("email") != EMAIL or body.get("code") != CODE:
            return self._error(401, "INVALID_CODE")
        return web.json_response({"accessToken": JWT, "refreshToken": "r", "isNewUser": False})

    async def _create_token(self, request: web.Request) -> web.Response:
        if self._bearer(request) != JWT or not self.jwt_valid:  # app JWT only
            return self._error(401, "UNAUTHORIZED")
        if self.forbid_token_creation:
            return self._error(403, "FORBIDDEN")
        body = await request.json()
        name = body.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:  # as the backend
            return self._error(400, "INVALID_BODY")
        property_id = body.get("propertyId")
        if property_id not in self.user_homes:
            return self._error(403, "FORBIDDEN")
        if self.max_tokens is not None and len(self.issued_tokens) >= self.max_tokens:
            return self._error(409, "TOO_MANY_TOKENS")
        token = "fyes_" + secrets.token_urlsafe(30)[:40]
        self.tokens[token] = property_id
        self.token_ids[token] = f"tok_{len(self.issued_tokens) + 1}"
        issued = {
            "id": f"tok_{len(self.issued_tokens) + 1}",
            "token": token,
            "prefix": token[:9],
            "name": body.get("name"),
            "createdAt": "2026-10-19T10:00:00.000Z",
            "propertyId": property_id,
        }
        self.issued_tokens.append(issued)
        return web.json_response({k: v for k, v in issued.items() if k != "propertyId"})

    async def _revoke_token(self, request: web.Request) -> web.Response:
        if self._bearer(request) != JWT or not self.jwt_valid:
            return self._error(401, "UNAUTHORIZED")
        token_id = request.match_info["token_id"]
        self.revoked.append(token_id)
        for token, tid in list(self.token_ids.items()):
            if tid == token_id:
                self.tokens.pop(token, None)
        return web.json_response({"id": token_id, "revokedAt": "2026-10-19T10:00:00.000Z"})

    async def _revoke_self(self, request: web.Request) -> web.Response:
        bearer = self._bearer(request)
        if bearer not in self.tokens:
            return self._error(401, "UNAUTHORIZED")
        self.self_revoked.append(bearer)
        self.tokens.pop(bearer)
        return web.json_response({"id": self.token_ids.get(bearer), "revokedAt": "now"})

    async def _homes(self, request: web.Request) -> web.Response:
        if self.homes_status is not None:
            return self._error(self.homes_status, "FORCED")
        homes = self._homes_for(request)
        if homes is None:
            return self._error(401, "UNAUTHORIZED")
        return web.json_response([self.homes[h] for h in homes])

    async def _snapshot(self, request: web.Request) -> web.Response:
        checked = self._check_home(request)
        if isinstance(checked, web.Response):
            return checked
        self.snapshot_requests += 1
        if self.snapshot_status is not None:
            return self._error(self.snapshot_status, "FORCED")
        if self.snapshot_fail_times > 0:
            self.snapshot_fail_times -= 1
            return self._error(502, "FORCED")
        return web.json_response(self.snapshot(checked))

    async def _command(self, request: web.Request) -> web.Response:
        checked = self._check_home(request)
        if isinstance(checked, web.Response):
            return checked
        eid = request.match_info["entity"]
        if eid not in self.entities:
            return self._error(404, "NOT_FOUND")
        body = await request.json()
        action, value = body.get("action"), body.get("value")
        if not self.gateway_online:
            return self._error(409, "OFFLINE")
        if self.command_error is not None:
            status, code, message = self.command_error
            return web.json_response({"error": code, "message": message}, status=status)
        if not self._valid(eid, action, value):
            return self._error(400, "INVALID_ACTION")
        self.commands.append((eid, action, value))
        if self.auto_echo:
            self._later(self.echo, eid, action, value)
        return web.json_response({"accepted": True}, status=202)

    async def _activate(self, request: web.Request) -> web.Response:
        checked = self._check_home(request)
        if isinstance(checked, web.Response):
            return checked
        scene = request.match_info["scene"]
        if scene not in {s["id"] for s in self.scenes}:
            return self._error(404, "NOT_FOUND")
        if not self.gateway_online:
            return self._error(409, "OFFLINE")
        self.scene_activations.append(scene)
        return web.json_response({"accepted": True}, status=202)

    async def _security(self, request: web.Request) -> web.Response:
        checked = self._check_home(request)
        if isinstance(checked, web.Response):
            return checked
        if self.security is None or not self.security["canSet"]:
            return self._error(403, "FORBIDDEN")
        mode = (await request.json()).get("mode")
        if mode not in self.security["modes"]:
            return self._error(400, "INVALID_MODE")
        self.security_requests.append(mode)
        if self.auto_echo:
            self._later(self.confirm_security, mode)
        return web.json_response({"accepted": True}, status=202)

    async def confirm_security(self, mode: str) -> None:
        """The security mode change is confirmed (pushed)."""
        assert self.security is not None
        self.security["mode"] = mode
        await self.push({"t": "security", "mode": mode})

    async def _realtime(self, request: web.Request) -> web.StreamResponse:
        if self.refuse_ws:
            return self._error(503, "UNAVAILABLE")
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws_connections += 1
        authed_home: str | None = None
        async for msg in ws:
            if msg.type is not WSMsgType.TEXT:
                continue
            data = msg.json()
            kind = data.get("t")
            if kind == "ping":
                self.pings += 1
                if not self.ignore_pings:
                    await ws.send_json({"t": "pong"})
            elif kind == "auth":
                token = data.get("token")
                if self.reject_ws_auth or token not in self.tokens:
                    await ws.send_json({"t": "auth_err"})
                    await ws.close()
                    break
                authed_home = self.tokens[token]
                await ws.send_json({"t": "auth_ok"})
            elif kind == "subscribe" and authed_home is not None:
                if data.get("propertyId") != authed_home:
                    await ws.send_json({"t": "auth_err"})
                    continue
                self.ws_formats.append(data.get("format"))
                self.subscribers.append(ws)
                await ws.send_json({"t": "subscribed", "propertyId": authed_home})
                if self.drop_after_subscribe:
                    await asyncio.sleep(0.05)  # long enough for the client's snapshot
                    await ws.close()
                    break
        if ws in self.subscribers:
            self.subscribers.remove(ws)
        return ws

    # --- fake hardware ---------------------------------------------------------------

    def _later(self, func: Callable[..., Awaitable[None]], *args: Any) -> None:
        async def run() -> None:
            await asyncio.sleep(self.echo_delay)
            await func(*args)

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _valid(self, eid: str, action: Any, value: Any) -> bool:
        platform = self.entities[eid]["platform"]
        allowed = {
            "light": {"turn_on", "turn_off", "set_brightness"},
            "switch": {"turn_on", "turn_off"},
            "siren": {"turn_on", "turn_off"},
            "cover": {"open", "close", "stop"},
            "climate": {"turn_on", "turn_off", "set_temperature"},
            "button": {"press"},
        }.get(platform, set())
        if action not in allowed:
            return False
        if action == "set_brightness":
            return isinstance(value, (int, float)) and 0 <= value <= 100
        if action == "set_temperature":
            return isinstance(value, (int, float)) and 5 <= value <= 30
        return True

    def _apply(self, eid: str, action: str, value: Any) -> bool:
        ent = self.entities[eid]
        attrs = ent["attributes"]
        if action in ("turn_on", "turn_off") and ent["platform"] != "climate":
            ent["state"] = "on" if action == "turn_on" else "off"
        elif action == "set_brightness":
            attrs["brightness"] = value
            ent["state"] = "on" if value > 0 else "off"
        elif action in ("open", "close", "stop"):
            # As the backend reports it: the motor relay that runs, or both idle.
            ent["state"] = {"open": "opening", "close": "closing", "stop": "stopped"}[action]
        elif action == "set_temperature":
            attrs["target_temperature"] = value
            attrs["hvac_mode"] = ent["state"] = "heat"
        elif action == "turn_off":  # climate: frost park
            attrs["target_temperature"] = 5
            attrs["hvac_mode"] = ent["state"] = "off"
        elif action == "turn_on":  # climate
            attrs["hvac_mode"] = ent["state"] = "heat"
        else:
            return False  # press: a pulse, no state
        return True
