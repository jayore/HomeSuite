"""Inventory active configuration without treating every setting as a form field.

The example configuration files are Home Suite's documented public contract.
This module compares active assignments against that contract and the guided
console schema so advanced, deprecated, and unrecognized settings never become
invisible merely because they do not yet have a purpose-built editor.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from config_schema import EDITABLE_FIELDS, field_for_key
from integration_registry import has_value


@dataclass(frozen=True)
class ConfigSource:
    scope: str
    scope_label: str
    filename: str
    example_filename: str


CONFIG_SOURCES = (
    ConfigSource("device", "This device", "local_prefs.py", "local_prefs.example.py"),
    ConfigSource(
        "deployment",
        "Shared deployment",
        "deployment_config.py",
        "deployment_config.example.py",
    ),
    ConfigSource("credentials", "Credentials", "private_config.py", "private_config.example.py"),
)


DEPRECATED_SETTINGS = {
    "HANDSET_PRESENT": (
        "PTT_ENABLED",
        "The handset role is now represented by the general push-to-talk capability.",
    ),
    "HANDSET_GPIO_PIN": (
        "PTT_GPIO_PIN",
        "Use the general push-to-talk GPIO setting.",
    ),
    "WAKEWORD_ONLY_ONHOOK": (
        "WAKEWORD_SUPPRESS_WHILE_PTT",
        "Wake-word coexistence is now expressed in terms of PTT activity.",
    ),
    "PHYSICAL_BUTTON_IGNORE_WHILE_HANDSET_UP": (
        "PHYSICAL_BUTTON_IGNORE_WHILE_PTT_ACTIVE",
        "Command-button suppression is now expressed in terms of PTT activity.",
    ),
    "PIPHONE_HTTP_API_KEY": (
        "HOMESUITE_HTTP_API_KEY",
        "The Home Suite name replaces the original project-specific API key name.",
    ),
    "WAKEWORD_ALLOW_ONHOOK_TTS": (
        None,
        "This prototype flag was removed; wake-word replies now follow the configured assistant audio output policy.",
    ),
    "TELEGRAM_BOT_ID": (
        None,
        "A standalone Telegram bot ID is not used; the bot token identifies the bot.",
    ),
    "PHONETIC_TOKEN_REPAIRS": (
        "PHONETIC_DEVICE_REPAIRS",
        "Use the scoped device-repair map for deployment-specific vocabulary.",
    ),
}


SPECIAL_LABELS = {
    "AUDIO_INPUT_PROFILE": "Microphone input profile",
    "ASSISTANT_PROFILE": "Assistant profile",
    "HOME_LOCATION": "Home location",
    "PHONETIC_DEVICE_REPAIRS": "Phonetic device repairs",
    "PHONETIC_ROUTING_REPAIRS": "Phonetic routing repairs",
    "TTS_PRONUNCIATION_OVERRIDES": "TTS pronunciation overrides",
    "HA_DEVICE_ALIASES": "Home Assistant device aliases",
    "HA_TRIGGER_ALIASES": "Home Assistant scene and script aliases",
}

SPECIAL_GUIDED_SETTINGS = {
    "AUDIO_INPUT_PROFILE": "Managed on the Audio page.",
    "HOMESUITE_ALSA_DEVICE": "Managed on the Audio page.",
    "ROOMS": "Managed on the Rooms page.",
}


_EXAMPLE_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:#\s*)?([A-Z][A-Z0-9_]+)(?:\s*:[^=]+)?\s*=",
    re.MULTILINE,
)
_MISSING = object()


def _title_from_key(key: str) -> str:
    return str(key).replace("_", " ").title()


def _assignment_values(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, Any] = {}
    for node in tree.body:
        name: Optional[str] = None
        value_node: Optional[ast.expr] = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            value_node = node.value
        if not name or not name.isupper() or value_node is None:
            continue
        try:
            values[name] = ast.literal_eval(value_node)
        except Exception:
            values[name] = _MISSING
    return values


def _documented_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(_EXAMPLE_ASSIGNMENT_RE.findall(path.read_text(encoding="utf-8")))


def _category(key: str) -> str:
    if key.startswith(("PTT_", "HANDSET_")) or key == "START_CHIME_DELAY_SECONDS":
        return "Push-to-talk"
    if key.startswith("WAKEWORD_"):
        return "Wake word"
    if key.startswith(("AUDIO_", "HOMESUITE_ALSA_")) or key in {
        "SAMPLE_RATE",
        "VAD_MODE",
        "MAX_UTTERANCE_SECONDS",
        "SILENCE_END_MS",
        "PRE_ROLL_MS",
        "MIN_SPEECH_MS",
    }:
        return "Microphone and audio"
    if key.startswith("PHYSICAL_BUTTON_"):
        return "Additional GPIO buttons"
    if key.startswith(("RUNTIME_LOG_", "COMMAND_EVENT_LOG_")):
        return "Logging and privacy"
    if key.startswith(("UNIFIED_SERVER_", "CONSOLE_")):
        return "Network services"
    if key.startswith(("PHONETIC_", "TTS_PRONUNCIATION_", "HA_DEVICE_ALIAS", "HA_TRIGGER_ALIAS")):
        return "Language and aliases"
    if key in {"LOCATION_ALIASES", "ENTITY_LABEL_OVERRIDES"}:
        return "Language and aliases"
    if key.startswith(("ROOM", "SOURCE")) or key in {"DEFAULT_ROOM", "DEFAULT_SONOS_ROOM"}:
        return "Rooms"
    if key.startswith("CALENDAR_") or key in {"CALENDARS", "DEFAULT_CALENDAR"}:
        return "Calendar"
    if key.startswith(("TEMPORARY_ACTION_", "COMMAND_CONFIRMATION_", "SCHEDULER_")):
        return "Command policies"
    if key.startswith(("ALARM_", "REMINDER_")):
        return "Alarms and reminders"
    if key.startswith(("STOCK_", "PLEX_", "SPOTIFY_", "PINNED_", "YOUTUBE_", "HOMELAB_")):
        return "Integration behavior"
    if key.startswith(("CHATGPT_", "ASSISTANT_", "DIALOGUE_", "MEDIA_REFERENT_")):
        return "Assistant behavior"
    return "Advanced configuration"


def _docs_path(category: str, scope: str) -> str:
    if category == "Wake word":
        return "docs/WAKEWORD.md"
    if category == "Push-to-talk":
        return "docs/PTT.md"
    if category == "Additional GPIO buttons":
        return "docs/GPIO_BUTTONS.md"
    if category == "Logging and privacy":
        return "docs/OPERATIONS.md"
    if scope == "credentials":
        return "docs/CREDENTIALS.md"
    return "docs/CONFIGURATION.md"


def _looks_secret(key: str) -> bool:
    upper = str(key).upper()
    return any(part in upper for part in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSCODE"))


def _summarize_value(
    key: str,
    value: Any,
    *,
    scope: str,
    effective: bool,
    module_value: Any,
) -> str:
    if not effective:
        return "Overridden on this device"
    candidate = module_value if value is _MISSING and module_value is not _MISSING else value
    if scope == "credentials" or _looks_secret(key):
        return "Configured" if has_value(candidate) else "Not configured"
    if candidate is _MISSING:
        return "Python expression"
    if isinstance(candidate, bool):
        return "Enabled" if candidate else "Disabled"
    if candidate is None or candidate == "":
        return "Not set"
    if isinstance(candidate, dict):
        return f"{len(candidate)} entr{'y' if len(candidate) == 1 else 'ies'}"
    if isinstance(candidate, (list, tuple, set)):
        return f"{len(candidate)} item{'s' if len(candidate) != 1 else ''}"
    text = str(candidate)
    return text if len(text) <= 80 else text[:77] + "..."


def build_config_inventory(*, root: Path, app_config=None, private_config=None) -> dict[str, Any]:
    """Return redacted coverage and active override classifications."""

    root = Path(root)
    source_values = {
        source.scope: _assignment_values(root / source.filename)
        for source in CONFIG_SOURCES
    }
    documented = {
        source.scope: _documented_keys(root / source.example_filename)
        for source in CONFIG_SOURCES
    }
    app_keys = set(_assignment_values(root / "app_config.py"))
    schema_keys = {field.key for field in EDITABLE_FIELDS}
    local_keys = set(source_values["device"])

    rows: list[dict[str, Any]] = []
    for source in CONFIG_SOURCES:
        for key, assigned_value in sorted(source_values[source.scope].items()):
            field = field_for_key(key)
            if key in DEPRECATED_SETTINGS:
                classification = "deprecated"
                replacement, guidance = DEPRECATED_SETTINGS[key]
            elif field is not None or key in SPECIAL_GUIDED_SETTINGS:
                classification = "guided"
                replacement = None
                guidance = SPECIAL_GUIDED_SETTINGS.get(key, "Managed by the guided console editor.")
            elif key in documented[source.scope] or key in app_keys:
                classification = "advanced"
                replacement = None
                guidance = "Supported setting; direct file editing is still required."
            else:
                classification = "unknown"
                replacement = None
                guidance = "No matching supported setting was found in the current configuration contract."

            effective = source.scope != "deployment" or key not in local_keys
            module = private_config if source.scope == "credentials" else app_config
            module_value = getattr(module, key, _MISSING) if module is not None else _MISSING
            category = field.section_label if field is not None else _category(key)
            configured = (
                has_value(module_value if module_value is not _MISSING else assigned_value)
                if source.scope == "credentials"
                else True
            )
            rows.append(
                {
                    "key": key,
                    "label": field.label if field is not None else SPECIAL_LABELS.get(key, _title_from_key(key)),
                    "scope": source.scope,
                    "scope_label": source.scope_label,
                    "source_file": source.filename,
                    "category": category,
                    "classification": classification,
                    "configured": configured,
                    "effective": effective,
                    "value_summary": _summarize_value(
                        key,
                        assigned_value,
                        scope=source.scope,
                        effective=effective,
                        module_value=module_value,
                    ),
                    "guidance": guidance,
                    "replacement": replacement,
                    "docs_path": field.docs_path if field is not None else _docs_path(category, source.scope),
                }
            )

    active_rows = [
        row
        for row in rows
        if row["effective"] and (row["scope"] != "credentials" or row["configured"])
    ]
    counts = {
        classification: sum(1 for row in active_rows if row["classification"] == classification)
        for classification in ("guided", "advanced", "deprecated", "unknown")
    }
    documented_total = sum(len(keys) for keys in documented.values())
    file_managed_total = sum(
        1
        for keys in documented.values()
        for key in keys
        if key not in schema_keys and key not in DEPRECATED_SETTINGS
    )
    return {
        "summary": {
            "active_assignments": len(active_rows),
            "guided_active": counts["guided"],
            "advanced_active": counts["advanced"],
            "deprecated_active": counts["deprecated"],
            "unknown_active": counts["unknown"],
            "attention_count": counts["deprecated"] + counts["unknown"],
            "guided_available": len(EDITABLE_FIELDS),
            "documented_available": documented_total,
            "file_managed_available": file_managed_total,
        },
        "rows": rows,
    }
