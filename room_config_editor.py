"""Validated room-topology editing for the Home Suite management console."""

from __future__ import annotations

import ast
import copy
import hmac
import importlib
import logging
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

from config_editor import (
    ConfigEditError,
    atomic_write_config,
    parse_config_source,
    rewrite_config_assignments,
    source_revision,
)


DEPLOYMENT_CONFIG_FILE = "deployment_config.py"
ROOMS_KEY = "ROOMS"
ROOM_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
AREA_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
ENTITY_ID_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
MAX_ROOMS = 64
MANAGED_TOPOLOGY_MARKER = "# Home Suite Console managed shared topology"
log = logging.getLogger("room_config_editor")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(child) for child in value]
    raise ConfigEditError("Room configuration contains a value the console cannot safely edit.", status=409)


def _literal_assignment(
    assignments: dict[str, list[tuple[ast.stmt, ast.expr]]],
    key: str,
) -> Any:
    nodes = assignments.get(key) or []
    if not nodes:
        return None
    try:
        return ast.literal_eval(nodes[-1][1])
    except Exception as exc:
        raise ConfigEditError(
            f"{key} in {DEPLOYMENT_CONFIG_FILE} must be a literal value before the console can edit it.",
            status=409,
        ) from exc


def _required_text(value: Any, label: str, *, maximum: int = 120) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigEditError(f"{label} is required.")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ConfigEditError(f"{label} must be {maximum} characters or fewer.")
    return normalized


def _optional_text(value: Any, label: str, *, maximum: int = 180) -> Optional[str]:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ConfigEditError(f"{label} must be text or left blank.")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise ConfigEditError(f"{label} must be {maximum} characters or fewer.")
    return normalized


def _string_list(
    value: Any,
    label: str,
    *,
    maximum_items: int = 32,
    item_maximum: int = 180,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigEditError(f"{label} must be a list.")
    if len(value) > maximum_items:
        raise ConfigEditError(f"{label} accepts at most {maximum_items} entries.")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in value:
        item = _required_text(raw, f"Each {label} entry", maximum=item_maximum)
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            normalized.append(item)
    return normalized


def _entity_id(
    value: Any,
    label: str,
    *,
    domains: Optional[Iterable[str]] = None,
    optional: bool = True,
) -> Optional[str]:
    normalized = _optional_text(value, label)
    if normalized is None:
        if optional:
            return None
        raise ConfigEditError(f"{label} is required.")
    normalized = normalized.lower()
    if not ENTITY_ID_PATTERN.fullmatch(normalized):
        raise ConfigEditError(f"{label} must be a Home Assistant entity ID such as light.floor_lamp.")
    allowed = {str(domain).lower() for domain in (domains or [])}
    domain = normalized.split(".", 1)[0]
    if allowed and domain not in allowed:
        joined = ", ".join(sorted(allowed))
        raise ConfigEditError(f"{label} must use one of these Home Assistant domains: {joined}.")
    return normalized


def _entity_list(
    value: Any,
    label: str,
    *,
    domains: Optional[Iterable[str]] = None,
    maximum_items: int = 48,
) -> list[str]:
    values = _string_list(value, label, maximum_items=maximum_items)
    return [
        str(_entity_id(item, label, domains=domains, optional=False))
        for item in values
    ]


def _area_id(value: Any, label: str = "Home Assistant area") -> Optional[str]:
    normalized = _optional_text(value, label, maximum=128)
    if normalized is None:
        return None
    normalized = normalized.lower()
    if not AREA_ID_PATTERN.fullmatch(normalized):
        raise ConfigEditError(f"{label} must be a Home Assistant area ID.")
    return normalized


def _brightness_target(value: Any, *, room_label: str, room_area: Optional[str]) -> Any:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigEditError(f"{room_label} brightness target must be a structured target or disabled.")
    target_type = str(value.get("type") or "").strip().lower()
    if target_type == "area":
        target_area = _area_id(value.get("area_id"), f"{room_label} brightness area")
        if not target_area and not room_area:
            raise ConfigEditError(
                f"{room_label} needs a Home Assistant area before area brightness can be enabled."
            )
        target = {"type": "area"}
        if target_area:
            target["area_id"] = target_area
        return target
    if target_type == "entity":
        entity_id = _entity_id(
            value.get("entity_id"),
            f"{room_label} brightness entity",
            domains={"light", "number", "input_number"},
            optional=False,
        )
        return {"type": "entity", "entity_id": entity_id}
    if target_type == "entities":
        entity_ids = _entity_list(
            value.get("entity_ids"),
            f"{room_label} brightness lights",
            domains={"light"},
        )
        if not entity_ids:
            raise ConfigEditError(f"{room_label} selected-light brightness needs at least one light.")
        return {"type": "entities", "entity_ids": entity_ids}
    raise ConfigEditError(
        f"{room_label} brightness target must use area, entity, entities, or be disabled."
    )


def _volume_target(value: Any, *, room_label: str) -> Any:
    if value is None:
        return None
    if not isinstance(value, dict) or str(value.get("type") or "").strip().lower() != "entity":
        raise ConfigEditError(f"{room_label} volume target must be one entity or disabled.")
    entity_id = _entity_id(
        value.get("entity_id"),
        f"{room_label} volume entity",
        domains={"media_player", "number", "input_number"},
        optional=False,
    )
    return {"type": "entity", "entity_id": entity_id}


def _labeled_entities(
    value: Any,
    label: str,
    *,
    domains: Optional[Iterable[str]] = None,
    maximum_items: int = 48,
) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigEditError(f"{label} must be a list.")
    if len(value) > maximum_items:
        raise ConfigEditError(f"{label} accepts at most {maximum_items} entries.")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise ConfigEditError(f"{label} entry {index} must have a label and entity.")
        entity_id = str(
            _entity_id(
                raw.get("entity"),
                f"{label} entry {index} entity",
                domains=domains,
                optional=False,
            )
        )
        display_label = _required_text(raw.get("label"), f"{label} entry {index} label", maximum=80)
        if entity_id in seen:
            raise ConfigEditError(f"{entity_id} appears more than once in {label}.")
        seen.add(entity_id)
        normalized.append({"entity": entity_id, "label": display_label})
    return normalized


def _audio_aliases(value: Any, room_label: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigEditError(f"{room_label} audio aliases must be a mapping.")
    if len(value) > 32:
        raise ConfigEditError(f"{room_label} accepts at most 32 audio aliases.")
    normalized: dict[str, str] = {}
    for raw_alias, raw_entity in value.items():
        alias = _required_text(raw_alias, f"{room_label} audio alias", maximum=80).casefold()
        if alias in normalized:
            raise ConfigEditError(f"{room_label} repeats the audio alias {alias}.")
        normalized[alias] = str(
            _entity_id(
                raw_entity,
                f"{room_label} audio alias {alias}",
                domains={"media_player"},
                optional=False,
            )
        )
    return normalized


def _scene_entries(value: Any, room_label: str) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigEditError(f"{room_label} shortcuts must be a list.")
    if len(value) > 48:
        raise ConfigEditError(f"{room_label} accepts at most 48 shortcuts.")
    normalized: list[dict[str, str]] = []
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise ConfigEditError(f"{room_label} shortcut {index} is invalid.")
        label = _required_text(raw.get("label"), f"{room_label} shortcut {index} label", maximum=80)
        actions = [key for key in ("command", "scene", "script") if _optional_text(raw.get(key), key)]
        if len(actions) != 1:
            raise ConfigEditError(f"{room_label} shortcut {label} needs exactly one action type.")
        action = actions[0]
        raw_target = raw.get(action)
        if action == "command":
            target = _required_text(raw_target, f"{room_label} shortcut {label} command", maximum=400)
        else:
            target = str(
                _entity_id(
                    raw_target,
                    f"{room_label} shortcut {label}",
                    domains={action},
                    optional=False,
                )
            )
        normalized.append({"label": label, action: target})
    return normalized


def _normalize_room(room_id: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigEditError(f"Room {room_id} must be an object.")
    room = copy.deepcopy(_json_safe(raw))
    label = _required_text(room.get("label"), f"{room_id} label", maximum=80)
    area = _area_id(room.get("ha_area_id"), f"{label} Home Assistant area")
    aliases = _string_list(room.get("aliases"), f"{label} aliases", maximum_items=24, item_maximum=80)

    defaults_raw = room.get("defaults")
    if defaults_raw is None:
        defaults_raw = {}
    if not isinstance(defaults_raw, dict):
        raise ConfigEditError(f"{label} defaults must be an object.")
    defaults = copy.deepcopy(_json_safe(defaults_raw))

    if "brightness_target" in defaults:
        defaults["brightness_target"] = _brightness_target(
            defaults.get("brightness_target"),
            room_label=label,
            room_area=area,
        )
    if "color_light" in defaults:
        defaults["color_light"] = _entity_id(
            defaults.get("color_light"),
            f"{label} color light",
            domains={"light"},
        )
    if "volume_target" in defaults:
        defaults["volume_target"] = _volume_target(defaults.get("volume_target"), room_label=label)

    entity_defaults = {
        "audio_output": {"media_player"},
        "announcements": {"media_player"},
        "tv": {"media_player"},
        "tv_remote": {"remote"},
        "tv_on_scene": {"scene"},
        "plex_launch_script": {"script"},
    }
    for key, domains in entity_defaults.items():
        if key in defaults:
            defaults[key] = _entity_id(
                defaults.get(key),
                f"{label} {key.replace('_', ' ')}",
                domains=domains,
            )
    for key in ("spotcast_device_name", "plex_client_name"):
        if key in defaults:
            defaults[key] = _optional_text(defaults.get(key), f"{label} {key.replace('_', ' ')}")
    if "spotcast_device_aliases" in defaults:
        defaults["spotcast_device_aliases"] = _string_list(
            defaults.get("spotcast_device_aliases"),
            f"{label} Spotcast aliases",
            maximum_items=24,
            item_maximum=80,
        )

    room["label"] = label
    room["ha_area_id"] = area
    room["aliases"] = aliases
    room["defaults"] = defaults
    if "media_players" in room:
        room["media_players"] = _labeled_entities(
            room.get("media_players"),
            f"{label} client media players",
            domains={"media_player"},
        )
    if "audio_outputs" in room:
        room["audio_outputs"] = _entity_list(
            room.get("audio_outputs"),
            f"{label} audio outputs",
            domains={"media_player"},
        )
    if "focus_participants" in room:
        room["focus_participants"] = _entity_list(
            room.get("focus_participants"),
            f"{label} focus participants",
            domains={"media_player"},
        )
    if "audio_aliases" in room:
        room["audio_aliases"] = _audio_aliases(room.get("audio_aliases"), label)
    if "scenes" in room:
        room["scenes"] = _scene_entries(room.get("scenes"), label)
    if "devices" in room:
        room["devices"] = _labeled_entities(room.get("devices"), f"{label} client devices")
    return room


def _normalize_rooms(value: Any, *, default_room: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise ConfigEditError("Rooms must be a JSON object keyed by stable room IDs.")
    if not value:
        raise ConfigEditError("Keep at least one configured room.")
    if len(value) > MAX_ROOMS:
        raise ConfigEditError(f"Home Suite supports at most {MAX_ROOMS} rooms in the console editor.")

    normalized: dict[str, dict[str, Any]] = {}
    spoken_names: dict[str, str] = {}
    for raw_room_id, raw_room in value.items():
        room_id = str(raw_room_id or "").strip()
        if not ROOM_ID_PATTERN.fullmatch(room_id):
            raise ConfigEditError(
                f"Room ID {room_id or '(blank)'} must use lowercase letters, numbers, and underscores."
            )
        if room_id in normalized:
            raise ConfigEditError(f"Room ID {room_id} is duplicated.")
        room = _normalize_room(room_id, raw_room)
        normalized[room_id] = room

        names = [room_id, room_id.replace("_", " "), *(room.get("aliases") or [])]
        for raw_name in names:
            name = re.sub(r"\s+", " ", str(raw_name).strip().casefold().replace("_", " "))
            existing = spoken_names.get(name)
            if name and existing and existing != room_id:
                raise ConfigEditError(
                    f"The spoken room name {raw_name!r} is shared by {existing} and {room_id}."
                )
            if name:
                spoken_names[name] = room_id

    if default_room not in normalized:
        raise ConfigEditError(
            f"The current default room ({default_room}) cannot be removed. Choose another default under Configuration first."
        )
    return normalized


def _changed_paths(before: Any, after: Any, prefix: str = "") -> list[str]:
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changed: list[str] = []
        for key in dict.fromkeys([*before.keys(), *after.keys()]):
            path = f"{prefix}.{key}" if prefix else str(key)
            changed.extend(_changed_paths(before.get(key), after.get(key), path))
        return changed
    return [prefix or "room"]


class RoomConfigEditor:
    """Read, validate, review, and atomically update shared room topology."""

    def __init__(
        self,
        *,
        root: Path,
        app_config=None,
        private_config=None,
        backup_root: Optional[Path] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.app_config = app_config or importlib.import_module("app_config")
        self.private_config = private_config or importlib.import_module("private_config")
        self.backup_root = Path(backup_root or (self.root / "backups" / "console"))
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self.root / DEPLOYMENT_CONFIG_FILE

    def _read_source(self) -> tuple[str, bool]:
        if not self.path.exists():
            return "", False
        return self.path.read_text(encoding="utf-8"), True

    def _current_rooms(self, source: str, exists: bool) -> dict[str, dict[str, Any]]:
        if exists:
            assignments = parse_config_source(DEPLOYMENT_CONFIG_FILE, source)[1]
            if assignments.get(ROOMS_KEY):
                value = _literal_assignment(assignments, ROOMS_KEY)
                if not isinstance(value, dict):
                    raise ConfigEditError(f"{ROOMS_KEY} in {DEPLOYMENT_CONFIG_FILE} must be a dictionary.", status=409)
                return _json_safe(value)
        effective = getattr(self.app_config, "ROOMS", {}) or {}
        if not isinstance(effective, dict):
            raise ConfigEditError("The effective ROOMS setting is invalid.", status=409)
        return _json_safe(effective)

    def _default_source(self) -> str:
        local_keys = set(getattr(self.app_config, "LOCAL_PREFS_KEYS", ()) or ())
        deployment_keys = set(getattr(self.app_config, "DEPLOYMENT_CONFIG_KEYS", ()) or ())
        if "DEFAULT_ROOM" in local_keys:
            return "device"
        if "DEFAULT_ROOM" in deployment_keys:
            return "deployment"
        return "default"

    def public_state(self) -> dict[str, Any]:
        with self._lock:
            source, exists = self._read_source()
            rooms = self._current_rooms(source, exists)
            return {
                "schema_version": 1,
                "rooms": rooms,
                "default_room": str(getattr(self.app_config, "DEFAULT_ROOM", "") or ""),
                "default_source": self._default_source(),
                "managed_file_exists": exists,
                "revision": source_revision(source),
            }

    def _summaries(
        self,
        current: dict[str, dict[str, Any]],
        proposed: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for room_id in current:
            if room_id not in proposed:
                summaries.append(
                    {
                        "action": "remove",
                        "room_id": room_id,
                        "label": str(current[room_id].get("label") or room_id),
                        "details": "Room and its shared routing defaults",
                    }
                )
        for room_id, room in proposed.items():
            if room_id not in current:
                summaries.append(
                    {
                        "action": "add",
                        "room_id": room_id,
                        "label": str(room.get("label") or room_id),
                        "details": "New shared room",
                    }
                )
                continue
            paths = _changed_paths(current[room_id], room)
            if paths:
                visible = [path.replace("defaults.", "") for path in paths[:6]]
                details = ", ".join(visible)
                if len(paths) > len(visible):
                    details += f", and {len(paths) - len(visible)} more"
                summaries.append(
                    {
                        "action": "update",
                        "room_id": room_id,
                        "label": str(room.get("label") or room_id),
                        "details": details,
                    }
                )
        return summaries

    def _prepare(self, rooms: Any) -> tuple[str, bool, dict, dict, list[dict]]:
        source, exists = self._read_source()
        current = self._current_rooms(source, exists)
        default_room = str(getattr(self.app_config, "DEFAULT_ROOM", "") or "").strip()
        proposed = _normalize_rooms(rooms, default_room=default_room)
        summaries = self._summaries(current, proposed)
        return source, exists, current, proposed, summaries

    def preview(self, rooms: Any) -> dict[str, Any]:
        with self._lock:
            source, _exists, _current, proposed, summaries = self._prepare(rooms)
            return {
                "rooms": proposed,
                "changes": summaries,
                "change_count": len(summaries),
                "revision": source_revision(source),
                "restart_services": ["homesuite.service"],
            }

    def apply(self, rooms: Any, revision: Any) -> dict[str, Any]:
        with self._lock:
            source, exists, _current, proposed, summaries = self._prepare(rooms)
            if not hmac.compare_digest(str(revision or ""), source_revision(source)):
                raise ConfigEditError(
                    f"{DEPLOYMENT_CONFIG_FILE} changed after this review. Reload Rooms and review again.",
                    status=409,
                )
            if not summaries:
                return {
                    "applied": False,
                    "changes": [],
                    "change_count": 0,
                    "written_files": [],
                    "restart_services": [],
                    "backup_dir": None,
                }

            base_source = source
            if not exists:
                base_source = (
                    '"""Shared, non-secret Home Suite deployment configuration."""\n'
                )
            rewritten = rewrite_config_assignments(
                DEPLOYMENT_CONFIG_FILE,
                base_source,
                updates={ROOMS_KEY: proposed},
                marker=MANAGED_TOPOLOGY_MARKER,
                sort_dicts=False,
            )

            stamp = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
            backup_dir = self.backup_root / stamp
            try:
                self.backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(self.backup_root, 0o700)
                backup_dir.mkdir(mode=0o700, exist_ok=False)
                if exists:
                    shutil.copy2(self.path.resolve(strict=True), backup_dir / DEPLOYMENT_CONFIG_FILE)
                else:
                    (backup_dir / f"{DEPLOYMENT_CONFIG_FILE}.absent").write_text(
                        "The file did not exist before this console update.\n",
                        encoding="utf-8",
                    )
            except Exception as exc:
                raise ConfigEditError(
                    "Home Suite could not create a private configuration backup, so nothing was written.",
                    status=500,
                ) from exc

            target = self.path.resolve(strict=True) if exists else self.path
            try:
                atomic_write_config(target, rewritten)
            except Exception as exc:
                try:
                    if exists:
                        atomic_write_config(target, source)
                    else:
                        target.unlink(missing_ok=True)
                except Exception:
                    pass
                raise ConfigEditError(
                    "The room configuration write failed and was rolled back. Check file permissions and logs.",
                    status=500,
                ) from exc

            return {
                "applied": True,
                "changes": summaries,
                "change_count": len(summaries),
                "written_files": [DEPLOYMENT_CONFIG_FILE],
                "restart_services": ["homesuite.service"],
                "backup_dir": str(backup_dir),
            }

    def catalog(self, *, force: bool = False) -> dict[str, Any]:
        """Return optional HA-assisted area and entity choices."""
        ha_url = str(getattr(self.private_config, "HA_URL", "") or "").strip()
        ha_token = str(getattr(self.private_config, "HA_TOKEN", "") or "").strip()
        if not ha_url or not ha_token:
            return {
                "available": False,
                "reason": "Home Assistant is not configured on this node.",
                "areas": [],
                "entities": [],
            }
        try:
            import ha_client

            ha_client.configure_ha(ha_url=ha_url, ha_token=ha_token)
            registry = ha_client.ha_get_registry_snapshot(force=force)
            if registry is None:
                raise RuntimeError("Home Assistant registry lookup failed")
            states = ha_client.ha_get_states() or []
            state_labels = {
                str(row.get("entity_id") or ""): str((row.get("attributes") or {}).get("friendly_name") or "")
                for row in states
                if isinstance(row, dict)
            }
            device_areas = {
                str(row.get("id") or ""): str(row.get("area_id") or "")
                for row in registry.get("devices", [])
                if isinstance(row, dict)
            }
            areas = [
                {
                    "id": str(row.get("area_id") or "").strip(),
                    "label": str(row.get("name") or row.get("area_id") or "").strip(),
                }
                for row in registry.get("areas", [])
                if isinstance(row, dict) and str(row.get("area_id") or "").strip()
            ]
            entities = []
            for row in registry.get("entities", []):
                if not isinstance(row, dict) or row.get("disabled_by"):
                    continue
                entity_id = str(row.get("entity_id") or "").strip()
                if not ENTITY_ID_PATTERN.fullmatch(entity_id):
                    continue
                device_id = str(row.get("device_id") or "")
                area_id = str(row.get("area_id") or device_areas.get(device_id) or "")
                label = str(
                    state_labels.get(entity_id)
                    or row.get("name")
                    or row.get("original_name")
                    or entity_id
                ).strip()
                entities.append(
                    {
                        "id": entity_id,
                        "label": label,
                        "domain": entity_id.split(".", 1)[0],
                        "area_id": area_id,
                    }
                )
            return {
                "available": True,
                "reason": None,
                "areas": sorted(areas, key=lambda row: (row["label"].casefold(), row["id"])),
                "entities": sorted(entities, key=lambda row: (row["domain"], row["label"].casefold(), row["id"])),
            }
        except Exception:
            log.exception("ROOM_CATALOG_REFRESH_FAIL")
            return {
                "available": False,
                "reason": "Home Assistant choices are unavailable; manual IDs still work.",
                "areas": [],
                "entities": [],
            }
