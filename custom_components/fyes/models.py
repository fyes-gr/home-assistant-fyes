"""Typed views of the FYES snapshot and realtime messages (contract §2-§3).

Every object is immutable: an update replaces the object, so entities can tell
"my data changed" with an identity check instead of comparing dictionaries.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .const import (
    CONF_SHOW_BUTTON_EVENTS,
    CONF_SHOW_HIDDEN,
    MODE_AWAY,
    MODE_HOME,
    MODE_NIGHT,
    MODE_OFF,
)

ALL_MODES: tuple[str, ...] = (MODE_OFF, MODE_HOME, MODE_NIGHT, MODE_AWAY)


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(part.title() for part in rest)


def attr(attributes: dict[str, Any], key: str, default: Any = None) -> Any:
    """Read an attribute by its snake_case name, accepting camelCase too."""
    if key in attributes:
        return attributes[key]
    camel = _camel(key)
    if camel in attributes:
        return attributes[camel]
    return default


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True, slots=True)
class FyesEntity:
    """One FYES entity, as the backend's single mapper produces it."""

    id: str
    platform: str
    device_class: str | None
    name: str
    name_el: str | None
    room_id: str | None
    available: bool
    enabled_by_default: bool
    protected: bool
    state: Any
    attributes: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> FyesEntity:
        """Build from the contract JSON."""
        attributes = raw.get("attributes")
        return cls(
            id=str(raw["id"]),
            platform=str(raw.get("platform") or ""),
            device_class=_str_or_none(raw.get("deviceClass")),
            name=str(raw.get("name") or raw["id"]),
            name_el=_str_or_none(raw.get("nameEl")),
            room_id=_str_or_none(raw.get("roomId")),
            available=bool(raw.get("available", True)),
            enabled_by_default=bool(raw.get("enabledByDefault", True)),
            protected=bool(raw.get("protected", False)),
            state=raw.get("state"),
            attributes=dict(attributes) if isinstance(attributes, dict) else {},
        )

    def display_name(self, greek: bool) -> str:
        """Greek name when HA runs in Greek and one exists, else the English one."""
        return self.name_el if greek and self.name_el else self.name

    def attr(self, key: str, default: Any = None) -> Any:
        """Read an attribute (snake_case or camelCase)."""
        return attr(self.attributes, key, default)

    @property
    def hidden(self) -> bool:
        """The device is hidden in the app."""
        return bool(self.attr("hidden", False))

    @property
    def kind(self) -> str | None:
        """FYES device kind (RELAY, DIMMER, THERMOSTAT, ...), when the backend sends it."""
        return _str_or_none(self.attr("kind"))

    @property
    def is_on(self) -> bool | None:
        """Interpret an on/off state; None when unknown."""
        return parse_on_off(self.state)


def enabled_by_default(options: Mapping[str, Any], ent: FyesEntity) -> bool:
    """The backend's default, overridden by the two integration options.

    A device hidden in the app carries attributes.hidden (its enabledByDefault is then
    false); "show hidden devices" brings it back, except a siren output (never on by
    default, contract §5) and a wall button without "show wall-button presses".
    """
    show_events = bool(options.get(CONF_SHOW_BUTTON_EVENTS, False))
    show_hidden = bool(options.get(CONF_SHOW_HIDDEN, False))
    if ent.hidden:
        if ent.platform == "siren":
            return False
        return show_hidden and (ent.platform != "event" or show_events)
    if ent.platform == "event":
        return show_events
    return ent.enabled_by_default


def parse_on_off(value: Any) -> bool | None:
    """Map "on"/"off" (or booleans) to a bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in ("on", "open", "true"):
            return True
        if lowered in ("off", "closed", "false"):
            return False
    return None


@dataclass(frozen=True, slots=True)
class FyesNamed:
    """A room or a scene: id + English and Greek names."""

    id: str
    name: str
    name_el: str | None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> FyesNamed:
        """Build from the contract JSON."""
        return cls(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            name_el=_str_or_none(raw.get("nameEl")),
        )

    def display_name(self, greek: bool) -> str:
        """Greek name when HA runs in Greek and one exists, else the English one."""
        return self.name_el if greek and self.name_el else self.name


@dataclass(frozen=True, slots=True)
class FyesHome:
    """The home itself and its gateway."""

    id: str
    name: str
    timezone: str | None
    gateway_online: bool
    sw_version: str | None
    name_el: str | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> FyesHome:
        """Build from the contract JSON."""
        return cls(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            timezone=_str_or_none(raw.get("timezone")),
            gateway_online=bool(raw.get("gatewayOnline", False)),
            sw_version=_str_or_none(raw.get("swVersion")),
            name_el=_str_or_none(raw.get("nameEl")),
        )

    def display_name(self, greek: bool) -> str:
        """Greek name when HA runs in Greek and one exists, else the English one."""
        return self.name_el if greek and self.name_el else self.name


@dataclass(frozen=True, slots=True)
class FyesSecurity:
    """The home's security mode."""

    mode: str
    modes: tuple[str, ...]
    can_set: bool

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> FyesSecurity:
        """Build from the contract JSON."""
        modes = raw.get("modes")
        return cls(
            mode=str(raw.get("mode") or MODE_OFF).upper(),
            modes=tuple(str(m).upper() for m in modes) if isinstance(modes, list) else ALL_MODES,
            can_set=bool(raw.get("canSet", True)),
        )


@dataclass(frozen=True, slots=True)
class FyesData:
    """Everything the integration knows about one home."""

    home: FyesHome
    rooms: dict[str, FyesNamed]
    entities: dict[str, FyesEntity]
    scenes: dict[str, FyesNamed]
    security: FyesSecurity | None

    @classmethod
    def from_snapshot(cls, raw: dict[str, Any]) -> FyesData:
        """Build from GET /integrations/v1/homes/:id."""
        security = raw.get("security")
        return cls(
            home=FyesHome.from_api(raw["home"]),
            rooms={
                room.id: room
                for room in (FyesNamed.from_api(r) for r in _dicts(raw.get("rooms")) if "id" in r)
            },
            entities={
                ent.id: ent
                for ent in (
                    FyesEntity.from_api(e) for e in _dicts(raw.get("entities")) if "id" in e
                )
            },
            scenes={
                scene.id: scene
                for scene in (FyesNamed.from_api(s) for s in _dicts(raw.get("scenes")) if "id" in s)
            },
            security=FyesSecurity.from_api(security) if isinstance(security, dict) else None,
        )


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []
