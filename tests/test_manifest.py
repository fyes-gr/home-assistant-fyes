"""hassfest-style checks that can run outside HA core.

`python -m script.hassfest` only exists in the HA core repository, so this file
re-states the parts of it that apply to a custom integration: the manifest schema
(CUSTOM_INTEGRATION_MANIFEST_SCHEMA) and key order, hacs.json, the translation
files, and that HA's own loader accepts the integration.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

from awesomeversion import AwesomeVersion, AwesomeVersionStrategy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations
from homeassistant.loader import async_get_integration
import voluptuous as vol

from custom_components.fyes.const import DOMAIN, PLATFORMS

COMPONENT = Path(__file__).parent.parent / "custom_components" / DOMAIN
ROOT = COMPONENT.parent.parent

SUPPORTED_IOT_CLASSES = [
    "assumed_state",
    "calculated",
    "cloud_polling",
    "cloud_push",
    "local_polling",
    "local_push",
]
INTEGRATION_TYPES = [
    "device",
    "entity",
    "hardware",
    "helper",
    "hub",
    "service",
    "system",
    "virtual",
]


def _version(value: str) -> str:
    AwesomeVersion(
        value,
        ensure_strategy=[
            AwesomeVersionStrategy.CALVER,
            AwesomeVersionStrategy.SEMVER,
            AwesomeVersionStrategy.SIMPLEVER,
            AwesomeVersionStrategy.BUILDVER,
            AwesomeVersionStrategy.PEP440,
        ],
    )
    return value


# hassfest (script/hassfest/manifest.py) for custom integrations, minus discovery keys.
CUSTOM_INTEGRATION_MANIFEST_SCHEMA = vol.Schema(
    {
        vol.Required("domain"): DOMAIN,
        vol.Required("name"): str,
        vol.Required("codeowners"): [str],
        vol.Optional("config_flow"): bool,
        vol.Optional("dependencies"): [str],
        vol.Optional("after_dependencies"): [str],
        vol.Required("documentation"): vol.Url(),
        vol.Optional("integration_type"): vol.In(INTEGRATION_TYPES),
        vol.Required("iot_class"): vol.In(SUPPORTED_IOT_CLASSES),
        vol.Optional("issue_tracker"): vol.Url(),
        vol.Optional("loggers"): [str],
        vol.Optional("requirements"): [str],
        vol.Optional("single_config_entry"): bool,
        vol.Optional("import_executor"): bool,
        vol.Required("version"): vol.All(str, _version),
    }
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_manifest_schema_and_order() -> None:
    """Valid keys and values; domain, name first, then alphabetical (hassfest)."""
    manifest = _load(COMPONENT / "manifest.json")
    CUSTOM_INTEGRATION_MANIFEST_SCHEMA(manifest)
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])
    assert manifest["iot_class"] == "cloud_push"
    assert manifest["config_flow"] is True
    assert manifest["integration_type"] == "hub"
    assert manifest["requirements"] == []  # aiohttp is bundled with HA


def test_hacs_json() -> None:
    """hacs.json has the keys HACS reads, and the integration sits where HACS looks."""
    hacs = _load(ROOT / "hacs.json")
    assert set(hacs) <= {
        "name",
        "content_in_root",
        "filename",
        "country",
        "homeassistant",
        "hacs",
        "hide_default_branch",
        "persistent_directory",
        "render_readme",
        "zip_release",
    }
    assert hacs["name"] == "FYES"
    assert AwesomeVersion(hacs["homeassistant"]) >= AwesomeVersion("2025.1.0")
    assert (ROOT / "custom_components" / DOMAIN / "manifest.json").is_file()
    assert (ROOT / "README.md").is_file()


async def test_loader_accepts_integration(hass: HomeAssistant) -> None:
    """HA's loader finds the integration, its version and every platform."""
    integration = await async_get_integration(hass, DOMAIN)
    assert not integration.is_built_in
    assert integration.version == AwesomeVersion("0.1.0")
    assert integration.config_flow
    assert integration.iot_class == "cloud_push"
    assert integration.platforms_exists([str(p) for p in PLATFORMS]) == [str(p) for p in PLATFORMS]
    assert integration.platforms_exists(["config_flow", "diagnostics"]) == [
        "config_flow",
        "diagnostics",
    ]
    await integration.async_get_component()
    await integration.async_get_platform("config_flow")


def _leaves(tree: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_leaves(value, path))
        else:
            out[path] = value
    return out


def test_translations_complete() -> None:
    """en.json mirrors strings.json; el.json has every key and the same placeholders."""
    strings = _load(COMPONENT / "strings.json")
    en = _load(COMPONENT / "translations" / "en.json")
    el = _load(COMPONENT / "translations" / "el.json")
    assert en == strings
    en_leaves, el_leaves = _leaves(en), _leaves(el)
    assert set(el_leaves) == set(en_leaves)
    placeholder = re.compile(r"\{(\w+)\}")
    for key, text in en_leaves.items():
        assert text.strip() == text and text, key
        assert "[%key:" not in text, key  # custom integrations get no key references
        assert sorted(placeholder.findall(text)) == sorted(placeholder.findall(el_leaves[key])), key
        assert el_leaves[key] != "" and el_leaves[key].strip() == el_leaves[key], key


def test_every_used_key_is_translated() -> None:
    """Errors, aborts, exceptions and entity names used in code all have strings."""
    strings = _load(COMPONENT / "strings.json")
    source = {path.name: path.read_text() for path in COMPONENT.glob("*.py")}

    flow = source["config_flow.py"]
    errors = set(re.findall(r'errors\[[^\]]+\] = "(\w+)"', flow))
    # Reasons HA raises on the integration's behalf (async_set_unique_id while a flow for
    # the same home is open, async_update_reload_and_abort after re-auth / reconfigure).
    implicit = {
        "already_configured",
        "already_in_progress",
        "reauth_successful",
        "reconfigure_successful",
    }
    aborts = set(re.findall(r'reason="(\w+)"', flow)) | implicit
    assert errors and aborts
    assert errors <= set(strings["config"]["error"]), errors - set(strings["config"]["error"])
    assert aborts <= set(strings["config"]["abort"]), aborts - set(strings["config"]["abort"])

    for menu in ("user", "reauth_confirm"):
        assert set(strings["config"]["step"][menu]["menu_options"]) == {"email", "token"}
    steps = set(re.findall(r'step_id="(\w+)"', flow))
    assert steps <= set(strings["config"]["step"]) | {"init"}

    exceptions = set()
    for text in source.values():
        exceptions |= set(re.findall(r'translation_key="(\w+)"', text))
    assert exceptions <= set(strings["exceptions"]), exceptions - set(strings["exceptions"])

    for platform, key in (
        ("button", "open"),
        ("alarm_control_panel", "security"),
        ("event", "wall_button"),
        ("switch", "gate"),
    ):
        assert key in strings["entity"][platform]
        assert f'_attr_translation_key = "{key}"' in source[f"{platform}.py"]


async def test_ha_loads_both_languages(hass: HomeAssistant) -> None:
    """HA's translation loader reads en and el for the flow, entities and errors."""
    for language, title, open_name in (
        ("en", "Connect your FYES home", "Open"),
        ("el", "Σύνδεση του σπιτιού σας FYES", "Άνοιγμα"),
    ):
        config = await async_get_translations(hass, language, "config", {DOMAIN})
        assert config[f"component.{DOMAIN}.config.step.user.title"] == title
        entity = await async_get_translations(hass, language, "entity", {DOMAIN})
        assert entity[f"component.{DOMAIN}.entity.button.open.name"] == open_name
        exceptions = await async_get_translations(hass, language, "exceptions", {DOMAIN})
        assert f"component.{DOMAIN}.exceptions.gateway_offline.message" in exceptions


def test_icons_json() -> None:
    """icons.json only references entity translation keys that exist."""
    icons = _load(COMPONENT / "icons.json")
    strings = _load(COMPONENT / "strings.json")
    for platform, keys in icons["entity"].items():
        for key, icon in keys.items():
            assert key in strings["entity"][platform]
            assert icon["default"].startswith("mdi:")
