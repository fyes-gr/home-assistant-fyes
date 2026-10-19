"""Async client for the FYES Cloud integration API (REST v1).

Contract: docs/HOME-ASSISTANT-INTEGRATION.md in the FYES Cloud repository.
Everything here is non-blocking (aiohttp); no I/O ever runs in the event loop.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import REQUEST_TIMEOUT


class FyesError(Exception):
    """Base error for the FYES API.

    `code` is the server's error code (OFFLINE, NOT_DELIVERED, ...) and `detail` its
    human-readable `message`, when it sent one.
    """

    def __init__(
        self, message: str = "", *, code: str | None = None, detail: str | None = None
    ) -> None:
        """Keep the server's code and message next to the text."""
        super().__init__(message)
        self.code = code
        self.detail = detail


class FyesConnectionError(FyesError):
    """The server could not be reached, timed out or failed (5xx)."""


class FyesAuthError(FyesError):
    """401: the token or code is wrong, revoked or no longer a member."""


class FyesForbiddenError(FyesError):
    """403: the user's role does not allow this."""


class FyesOfflineError(FyesError):
    """409 OFFLINE: the home's gateway is offline, nothing was sent."""


class FyesBadRequestError(FyesError):
    """400: the request was malformed (bad email, unknown action, ...)."""


class FyesNotFoundError(FyesError):
    """404: the home, entity or scene does not exist."""


class FyesRateLimitError(FyesError):
    """429: too many requests for this token."""


class FyesTooManyTokensError(FyesError):
    """409 TOO_MANY_TOKENS: this account already has the most connections allowed for the home."""


class FyesNotDeliveredError(FyesError):
    """409 NOT_DELIVERED / ALARM_NOT_REACHED: sent, but the building did not confirm it."""


def normalize_url(url: str) -> str:
    """Return the base URL without a trailing slash."""
    return url.strip().rstrip("/")


class FyesApi:
    """Thin REST client. The token is sent as a Bearer header."""

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str, token: str | None = None
    ) -> None:
        """Initialize the client."""
        self._session = session
        self.base_url = normalize_url(base_url)
        self.token = token

    @property
    def session(self) -> aiohttp.ClientSession:
        """Return the shared HA client session."""
        return self._session

    @property
    def ws_url(self) -> str:
        """Return the realtime websocket URL (http->ws, https->wss)."""
        if self.base_url.startswith("https://"):
            return "wss://" + self.base_url[len("https://") :] + "/realtime"
        if self.base_url.startswith("http://"):
            return "ws://" + self.base_url[len("http://") :] + "/realtime"
        return self.base_url + "/realtime"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Send one request and map HTTP errors to exceptions."""
        headers = {"Accept": "application/json"}
        bearer = token if token is not None else self.token
        if json is None and method in ("POST", "PUT", "PATCH"):
            # Always a JSON body: aiohttp labels an empty body application/octet-stream,
            # which the FYES server (Fastify) refuses with 415 before the route runs.
            json = {}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.request(
                    method, self.base_url + path, json=json, headers=headers
                ) as resp:
                    body: Any = None
                    if resp.status != 204:
                        try:
                            body = await resp.json(content_type=None)
                        except ValueError:
                            body = None
        except (aiohttp.ClientError, TimeoutError) as err:
            raise FyesConnectionError(str(err) or type(err).__name__) from err

        status = resp.status
        if status < 400:
            return body
        code = body.get("error") if isinstance(body, dict) else None
        code = code if isinstance(code, str) else None
        detail = body.get("message") if isinstance(body, dict) else None
        detail = detail if isinstance(detail, str) and detail else None
        # The server's own sentence when it sent one ("The alarm button ... did not reach
        # the building"), else the status and code.
        message = detail or (f"HTTP {status}" + (f" {code}" if code else ""))
        error_cls: type[FyesError] = FyesError
        if status == 401:
            error_cls = FyesAuthError
        elif status == 403:
            error_cls = FyesForbiddenError
        elif status == 404:
            error_cls = FyesNotFoundError
        elif status == 409 and code == "OFFLINE":
            error_cls = FyesOfflineError
        elif status == 409 and code == "TOO_MANY_TOKENS":
            error_cls = FyesTooManyTokensError
        elif status == 409 and code in ("NOT_DELIVERED", "ALARM_NOT_REACHED"):
            error_cls = FyesNotDeliveredError
        elif status == 400:
            error_cls = FyesBadRequestError
        elif status == 429:
            error_cls = FyesRateLimitError
        elif status >= 500:
            error_cls = FyesConnectionError
        raise error_cls(message, code=code, detail=detail)

    # --- sign-in (config flow) -------------------------------------------------------

    async def async_request_code(self, email: str) -> None:
        """Ask FYES to email a 6-digit sign-in code (existing app endpoint)."""
        await self._request("POST", "/auth/otp/request", token="", json={"email": email})

    async def async_verify_code(self, email: str, code: str) -> str:
        """Exchange the emailed code for an app JWT (only used during sign-in)."""
        body = await self._request(
            "POST", "/auth/otp/verify", token="", json={"email": email, "code": code}
        )
        if not isinstance(body, dict) or not isinstance(body.get("accessToken"), str):
            raise FyesError("Sign-in answer has no accessToken")
        return body["accessToken"]

    async def async_create_token(self, jwt: str, property_id: str, name: str) -> dict[str, Any]:
        """Create the integration token HA keeps (shown once by the server)."""
        body = await self._request(
            "POST",
            "/integrations/tokens",
            token=jwt,
            json={"propertyId": property_id, "name": name},
        )
        if not isinstance(body, dict) or not isinstance(body.get("token"), str):
            raise FyesError("Token answer has no token")
        return body

    async def async_revoke_token(self, jwt: str, token_id: str) -> None:
        """Revoke one of the account's tokens with the app session (re-auth replaces it)."""
        await self._request("DELETE", f"/integrations/tokens/{_q(token_id)}", token=jwt)

    # --- integration API v1 ----------------------------------------------------------

    async def async_revoke_self(self) -> None:
        """The integration token revokes itself (the integration is being removed)."""
        await self._request("DELETE", "/integrations/v1/token")

    async def async_get_homes(self, token: str | None = None) -> list[dict[str, Any]]:
        """List the homes the token (or app JWT) may use."""
        body = await self._request("GET", "/integrations/v1/homes", token=token)
        if not isinstance(body, list):
            raise FyesError("Homes answer is not a list")
        return [h for h in body if isinstance(h, dict) and isinstance(h.get("id"), str)]

    async def async_get_snapshot(self, home_id: str) -> dict[str, Any]:
        """Fetch the full snapshot of one home."""
        body = await self._request("GET", f"/integrations/v1/homes/{_q(home_id)}")
        if not isinstance(body, dict) or not isinstance(body.get("home"), dict):
            raise FyesError("Snapshot answer has no home")
        return body

    async def async_command(
        self,
        home_id: str,
        entity_id: str,
        action: str,
        value: float | None = None,
    ) -> None:
        """Send a command. 202 = accepted; the state arrives on the realtime stream."""
        payload: dict[str, Any] = {"action": action}
        if value is not None:
            payload["value"] = value
        await self._request(
            "POST",
            f"/integrations/v1/homes/{_q(home_id)}/entities/{_q(entity_id)}/command",
            json=payload,
        )

    async def async_activate_scene(self, home_id: str, scene_id: str) -> None:
        """Run a FYES scenario."""
        await self._request(
            "POST",
            f"/integrations/v1/homes/{_q(home_id)}/scenes/{_q(scene_id)}/activate",
        )

    async def async_set_security(self, home_id: str, mode: str) -> None:
        """Set the security mode (same path as the app, alarm buttons included)."""
        await self._request(
            "PUT", f"/integrations/v1/homes/{_q(home_id)}/security", json={"mode": mode}
        )


def _q(value: str) -> str:
    return quote(value, safe="")
