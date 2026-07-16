"""Build redacted, read-only data for the Home Suite management console.

The console deliberately exposes a curated view instead of serializing Python
configuration modules wholesale. This keeps credentials out of browser
responses while still showing effective values, their source layer, room
capabilities, integration readiness, and Doctor results.
"""

from __future__ import annotations

import importlib
import json
import platform
import socket
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Optional

from integration_registry import integration_rows


ROOT = Path(__file__).resolve().parent


NODE_FIELDS = (
    ("Identity", "DEFAULT_ROOM", "Default room", None),
    ("Push-to-talk", "PTT_ENABLED", "PTT enabled", False),
    ("Push-to-talk", "PTT_GPIO_PIN", "BCM GPIO pin", 11),
    ("Push-to-talk", "PTT_LISTEN_LEVEL", "Listen level", "low"),
    ("Push-to-talk", "PTT_END_BEHAVIOR", "When PTT ends", "cancel"),
    ("Hardware", "PHYSICAL_BUTTONS_ENABLED", "Command buttons enabled", False),
    ("Hardware", "PHYSICAL_BUTTON_PINS", "Command button pins", {}),
    ("Hardware", "PHYSICAL_BUTTON_ACTIONS", "Command button actions", {}),
    ("Hardware", "WAKEWORD_ENABLED", "Wake word enabled", False),
    ("Wake word", "WAKEWORD_ENGINE", "Engine", "openwakeword"),
    ("Wake word", "WAKEWORD_MODEL", "Model", None),
    ("Wake word", "WAKEWORD_THRESHOLD", "Detection threshold", None),
    ("Wake word", "WAKEWORD_VAD_THRESHOLD", "Voice activity threshold", None),
    ("Audio", "ASSISTANT_AUDIO_OUTPUT_MODE", "Assistant output", "local"),
    ("Audio", "ASSISTANT_AUDIO_OUTPUT_ROOM", "Assistant output room", None),
    ("Audio", "AUDIO_INPUT_PROFILE", "Input profile", {}),
    ("Companion API", "UNIFIED_SERVER_ENABLED", "API enabled", True),
    ("Companion API", "UNIFIED_SERVER_PORT", "API port", 8765),
    ("Management console", "CONSOLE_HOST", "Listen address", "0.0.0.0"),
    ("Management console", "CONSOLE_PORT", "Console port", 8766),
)


def _load_module(name: str):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(child) for child in value]
    return str(value)


def _config_source(app_config, key: str) -> str:
    local_keys = set(getattr(app_config, "LOCAL_PREFS_KEYS", ()) or ())
    deployment_keys = set(getattr(app_config, "DEPLOYMENT_CONFIG_KEYS", ()) or ())
    if key in local_keys:
        return "device"
    if key in deployment_keys:
        return "deployment"
    return "default"


def _git_revision() -> Optional[str]:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
        ).strip() or None
    except Exception:
        return None


def _node_fields(app_config) -> list[dict]:
    rows = []
    for group, key, label, default in NODE_FIELDS:
        value = getattr(app_config, key, default)
        rows.append(
            {
                "group": group,
                "key": key,
                "label": label,
                "value": _json_safe(value),
                "configured": _has_value(value),
                "source": _config_source(app_config, key),
            }
        )
    return rows


def _display_room_value(value: Any) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return "Not configured"
    if isinstance(value, dict):
        target_type = str(value.get("type") or "").strip()
        if target_type == "area":
            return "Home Assistant area"
        if target_type == "entity" and value.get("entity_id"):
            return str(value["entity_id"])
        return json.dumps(_json_safe(value), sort_keys=True)
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value) or "Not configured"
    return str(value)


def _room_rows(app_config) -> list[dict]:
    rooms = getattr(app_config, "ROOMS", {}) or {}
    rows = []
    for room_id, room in rooms.items():
        if not isinstance(room, dict):
            continue
        defaults = room.get("defaults") if isinstance(room.get("defaults"), dict) else {}
        capabilities = []
        for key, value in defaults.items():
            capabilities.append(
                {
                    "key": str(key),
                    "configured": _has_value(value),
                    "value": _display_room_value(value),
                }
            )
        media_players = room.get("media_players") or room.get("audio_outputs") or []
        devices = room.get("devices") or []
        scenes = room.get("scenes") or []
        rows.append(
            {
                "id": str(room_id),
                "label": str(room.get("label") or str(room_id).replace("_", " ").title()),
                "aliases": [str(value) for value in (room.get("aliases") or [])],
                "ha_area_id": room.get("ha_area_id"),
                "is_default": str(room_id) == str(getattr(app_config, "DEFAULT_ROOM", "")),
                "capabilities": capabilities,
                "counts": {
                    "media_players": len(media_players),
                    "devices": len(devices),
                    "scenes": len(scenes),
                },
            }
        )
    return rows


def _local_integration_rows(app_config) -> list[dict]:
    location = getattr(app_config, "HOME_LOCATION", {}) or {}
    coords_ready = _has_value(location.get("latitude")) and _has_value(location.get("longitude"))
    calendars = getattr(app_config, "CALENDARS", {}) or {}
    weather_entity = getattr(app_config, "WEATHER_ENTITY_ID", None)
    return [
        {
            "id": "weather_astronomy",
            "label": "Weather and astronomy",
            "description": "Home Assistant weather plus local coordinate-based forecasts and sky data",
            "status": "configured" if weather_entity or coords_ready else "partial",
            "scope": "deployment",
            "configured_fields": [name for name, ready in (("WEATHER_ENTITY_ID", bool(weather_entity)), ("HOME_LOCATION coordinates", coords_ready)) if ready],
            "missing_fields": [name for name, ready in (("WEATHER_ENTITY_ID", bool(weather_entity)), ("HOME_LOCATION coordinates", coords_ready)) if not ready],
        },
        {
            "id": "calendar",
            "label": "Calendar",
            "description": "Calendar reads and optional writes through Home Assistant",
            "status": "configured" if calendars else "not_configured",
            "scope": "deployment",
            "configured_fields": [f"{len(calendars)} calendar(s)"] if calendars else [],
            "missing_fields": [] if calendars else ["CALENDARS"],
        },
    ]


def _active_roles(app_config) -> list[str]:
    roles = ["text"]
    if bool(getattr(app_config, "UNIFIED_SERVER_ENABLED", True)):
        roles.append("api")
    if bool(getattr(app_config, "PTT_ENABLED", False)):
        roles.append("ptt")
    if bool(getattr(app_config, "WAKEWORD_ENABLED", False)):
        roles.append("wakeword")
    return roles


def build_snapshot(*, app_config=None, private_config=None) -> dict:
    """Return the authenticated console's curated configuration snapshot."""
    app_config = app_config or _load_module("app_config")
    private_config = private_config or _load_module("private_config")
    if app_config is None:
        raise RuntimeError("app_config.py could not be loaded")
    if private_config is None:
        raise RuntimeError("private_config.py could not be loaded")

    node = _node_fields(app_config)
    rooms = _room_rows(app_config)
    integrations = integration_rows(private_config) + _local_integration_rows(app_config)
    configured_integrations = sum(1 for row in integrations if row["status"] == "configured")
    return {
        "overview": {
            "hostname": socket.gethostname(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "revision": _git_revision(),
            "roles": _active_roles(app_config),
            "default_room": getattr(app_config, "DEFAULT_ROOM", None),
            "room_count": len(rooms),
            "configured_integrations": configured_integrations,
            "integration_count": len(integrations),
        },
        "node": node,
        "rooms": rooms,
        "integrations": integrations,
        "sources": {
            "default": "Built-in default",
            "deployment": "Shared deployment_config.py",
            "device": "This node's local_prefs.py",
        },
    }


def _secret_values(private_config) -> list[str]:
    values = []
    if private_config is None:
        return values
    sensitive_parts = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSCODE")
    for name in dir(private_config):
        if name.startswith("_") or not any(part in name.upper() for part in sensitive_parts):
            continue
        value = getattr(private_config, name, None)
        if isinstance(value, str) and len(value.strip()) >= 4:
            values.append(value.strip())
    return sorted(set(values), key=len, reverse=True)


def _redact_detail(detail: str, secrets: Iterable[str]) -> str:
    redacted = str(detail or "")
    for secret in secrets:
        redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _diagnostic_action(row: dict[str, Any]) -> Optional[dict[str, str]]:
    """Map an unhealthy Doctor check to the most relevant console surface."""

    if row.get("status") not in {"WARN", "FAIL"}:
        return None
    group = str(row.get("group") or "").lower()
    label = str(row.get("label") or "").lower()
    combined = f"{group} {label}"

    integration_ids = {
        "home assistant": "home_assistant",
        "openai": "openai",
        "plex": "plex",
        "spotify": "spotify",
        "telegram": "telegram",
        "youtube": "youtube",
        "alpaca": "alpaca",
        "uptime kuma": "uptime_kuma",
        "qbittorrent": "qbittorrent",
        "seerr": "seerr",
        "radarr": "radarr",
        "sonarr": "sonarr",
        "lidarr": "lidarr",
        "porcupine": "porcupine",
    }
    for name, integration_id in integration_ids.items():
        if name in combined:
            return {
                "view": "integrations",
                "label": "Open Integrations",
                "target": integration_id,
                "guidance": "Review this provider's saved settings, run its connection test, then rerun diagnostics.",
            }
    if any(word in combined for word in ("audio", "microphone", "capture", "playback")):
        return {
            "view": "audio",
            "label": "Open Audio",
            "guidance": "Review the detected hardware and audio profile, then rerun calibration or this check.",
        }
    if any(word in combined for word in ("room", "entity", "area", "brightness target")):
        return {
            "view": "rooms",
            "label": "Open Rooms",
            "guidance": "Review the affected room mapping and Home Assistant targets, then rerun diagnostics.",
        }
    if any(word in combined for word in ("wakeword", "wake word", "ptt", "gpio", "api port", "config")):
        return {
            "view": "configuration",
            "label": "Open Configuration",
            "guidance": "Review the related device setting and its setup guidance, then rerun diagnostics.",
        }
    return None


def build_doctor_report(*, live: bool = False, timeout: float = 4.0) -> dict:
    """Run Doctor and retain useful details while scrubbing credential values."""
    from tools.doctor import Doctor

    doctor = Doctor(live=live, timeout=timeout)
    exit_code = doctor.run(report=False)
    secrets = _secret_values(doctor.private_config)
    checks = []
    for check in doctor.relevant_checks():
        row = asdict(check)
        row["roles"] = list(row.get("roles") or [])
        row["detail"] = _redact_detail(row.get("detail", ""), secrets)
        action = _diagnostic_action(row)
        if action:
            row["action"] = action
        checks.append(row)
    return {
        "ok": exit_code == 0,
        "live": bool(live),
        "roles": doctor.role_summary(),
        "checks": checks,
    }
