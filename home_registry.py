"""Describe rooms, source instances, and client-visible HomeSuite capabilities.

``ROOMS`` contains deployment-oriented defaults such as preferred lights and
media outputs. ``SOURCES`` describes clients/endpoints and associates them with
room policy. Most entries are concrete instances; an entry with
``source_id_prefix`` describes a dynamic family whose instances need separate
continuity state. Lookup helpers use this data to build request context and the
manifest served to UI clients.

Keep optional values explicit with ``None``. Increment
``MANIFEST_SCHEMA_VERSION`` only when a non-additive shape change requires
clients to adapt.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any, Dict, List, Optional


from app_config import (
    ASSISTANT_BULK_EXCLUDED_ENTITY_IDS,
    ASSISTANT_BULK_EXCLUDED_ENTITY_PATTERNS,
    DEFAULT_ROOM,
    ENTITY_LABEL_OVERRIDES,
    MANIFEST_SCHEMA_VERSION,
    ROOMS,
    SOURCES,
)


def is_assistant_bulk_entity_allowed(entity_id: Optional[str]) -> bool:
    """Return whether an entity may appear in summaries or bulk actions."""
    candidate = str(entity_id or "").strip().lower()
    if not candidate or "." not in candidate:
        return False

    excluded_ids = {
        str(value or "").strip().lower()
        for value in (ASSISTANT_BULK_EXCLUDED_ENTITY_IDS or [])
        if str(value or "").strip()
    }
    if candidate in excluded_ids:
        return False

    for pattern in (ASSISTANT_BULK_EXCLUDED_ENTITY_PATTERNS or []):
        normalized = str(pattern or "").strip().lower()
        if normalized and fnmatchcase(candidate, normalized):
            return False
    return True


def get_room(room_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not room_id:
        return None
    return ROOMS.get(str(room_id).strip())


def get_room_label(room_id: Optional[str]) -> Optional[str]:
    """Friendly display name for a room (falls back to a de-underscored id)."""
    if not room_id:
        return None
    room = get_room(room_id)
    if isinstance(room, dict):
        label = room.get("label")
        if label:
            label = str(label).strip()
            if label:
                return label
    return str(room_id).strip().replace("_", " ") or None


def get_source(source_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not source_id:
        return None
    sid = str(source_id).strip()
    exact = SOURCES.get(sid)
    if isinstance(exact, dict):
        return exact
    for source in SOURCES.values():
        if not isinstance(source, dict):
            continue
        prefix = str(source.get("source_id_prefix") or "").strip()
        if prefix and sid.startswith(prefix):
            return source
    return None


def get_source_room(source_id: Optional[str]) -> Optional[str]:
    src = get_source(source_id)
    if not src:
        return None
    if src.get("inherit_default_room"):
        return get_default_room_id()
    room = src.get("room")
    if room is None:
        return None
    room = str(room).strip()
    return room or None


def is_source_mobile(source_id: Optional[str]) -> bool:
    """
    Return True only for sources explicitly marked mobile in the registry.

    Unknown or unregistered sources default to False (not mobile) so that an
    unrecognized frontend can never silently change room focus. Registered
    dynamic source families inherit the mobility policy of their prefix entry.
    """
    src = get_source(source_id)
    if not isinstance(src, dict):
        return False
    return bool(src.get("mobile"))


def get_source_room_key(source_id: Optional[str]) -> Optional[str]:
    """
    Return the key under which a source's sticky room focus is stored.

    Sources sharing a `device_group` (e.g. the menubar app + Raycast on one
    laptop) share a single room focus; otherwise the key is the source id.
    """
    sid = (str(source_id).strip() if source_id else "")
    if not sid:
        return None
    src = get_source(sid)
    if isinstance(src, dict):
        group = src.get("device_group")
        if group:
            g = str(group).strip()
            if g:
                return g
    return sid


def get_source_label(source_id: Optional[str]) -> Optional[str]:
    src = get_source(source_id)
    if not isinstance(src, dict):
        return None
    label = src.get("label")
    if label is None:
        return None
    label = str(label).strip()
    return label or None


def get_room_defaults(room_id: Optional[str]) -> Dict[str, Any]:
    room = get_room(room_id) or {}
    defaults = room.get("defaults") or {}
    return defaults if isinstance(defaults, dict) else {}


def get_room_default(room_id: Optional[str], key: str, default=None):
    defaults = get_room_defaults(room_id)
    return defaults.get(key, default)


def get_room_audio_outputs(room_id: Optional[str]) -> list[str]:
    room = get_room(room_id) or {}
    vals = room.get("audio_outputs") or []
    return [str(v).strip() for v in vals if str(v).strip()]


def get_room_focus_participants(room_id: Optional[str]) -> list[str]:
    room = get_room(room_id) or {}
    vals = room.get("focus_participants") or []
    return [str(v).strip() for v in vals if str(v).strip()]


def find_room_by_alias(name: Optional[str]) -> Optional[str]:
    needle = str(name or "").strip().lower().replace("_", " ")
    return get_room_alias_map().get(needle) if needle else None


def get_room_alias_map() -> Dict[str, str]:
    """Map normalized room IDs and spoken aliases to canonical room IDs."""
    out: Dict[str, str] = {}
    for room_id, room in (ROOMS or {}).items():
        rid = str(room_id or "").strip()
        if not rid or not isinstance(room, dict):
            continue
        aliases = {rid, rid.replace("_", " ")}
        aliases.update(room.get("aliases") or [])
        for alias in aliases:
            key = str(alias or "").strip().lower().replace("_", " ")
            if key:
                out[key] = rid
    return out


def resolve_room_id(name: Optional[str]) -> Optional[str]:
    """Resolve a configured room ID from an ID, spoken form, or alias."""
    raw = str(name or "").strip()
    if not raw:
        return None
    if get_room(raw):
        return raw
    underscored = raw.lower().replace(" ", "_")
    if get_room(underscored):
        return underscored
    return get_room_alias_map().get(raw.lower().replace("_", " "))


def get_default_room_id() -> Optional[str]:
    """Return the configured default only when it names a real room."""
    return resolve_room_id(DEFAULT_ROOM)


def get_default_room() -> Optional[Dict[str, Any]]:
    """Return the canonical configuration object for DEFAULT_ROOM."""
    return get_room(get_default_room_id())


def get_room_volume_target(room_hint: Optional[str]) -> Optional[Dict[str, str]]:
    """Return a validated entity target for room-level volume commands."""
    raw_hint = str(room_hint or "").strip()
    room_id = resolve_room_id(raw_hint) if raw_hint else get_default_room_id()
    if not room_id:
        return None
    defaults = get_room_defaults(room_id)
    if "volume_target" in defaults:
        raw = defaults.get("volume_target")
        if isinstance(raw, dict) and str(raw.get("type") or "").strip().lower() == "entity":
            entity_id = str(raw.get("entity_id") or "").strip()
            if entity_id.startswith(("media_player.", "number.", "input_number.")):
                return {"type": "entity", "entity_id": entity_id, "room_id": room_id}
        return None

    # Backward compatibility for installations that predate volume_target.
    legacy = defaults.get("volume_number") or defaults.get("audio_output")
    entity_id = str(legacy or "").strip()
    if entity_id.startswith(("media_player.", "number.", "input_number.")):
        return {"type": "entity", "entity_id": entity_id, "room_id": room_id}
    return None


def get_room_color_light_map() -> Dict[str, str]:
    """Map every configured room ID/alias to its room color entity."""
    out: Dict[str, str] = {}
    for room_id, room in (ROOMS or {}).items():
        defaults = room.get("defaults") or {}
        entity_id = str(defaults.get("color_light") or "").strip()
        if not entity_id:
            continue
        aliases = {str(room_id).strip().lower().replace("_", " ")}
        aliases.update(
            str(alias).strip().lower().replace("_", " ")
            for alias in (room.get("aliases") or [])
            if str(alias).strip()
        )
        for alias in aliases:
            out[alias] = entity_id
    return out


def get_brightness_light_phrase_overrides() -> Dict[str, str]:
    """Derive legacy '<room> brightness' light aliases from room targets."""
    out: Dict[str, str] = {}
    for room_id, room in (ROOMS or {}).items():
        defaults = room.get("defaults") or {}
        target = defaults.get("brightness_target")
        if not isinstance(target, dict):
            continue
        entity_id = str(target.get("entity_id") or "").strip()
        if not entity_id.startswith("light."):
            continue
        aliases = {str(room_id).strip().lower().replace("_", " ")}
        aliases.update(
            str(alias).strip().lower().replace("_", " ")
            for alias in (room.get("aliases") or [])
            if str(alias).strip()
        )
        for alias in aliases:
            out[f"{alias} brightness"] = entity_id
    return out


def get_room_spotcast_device_name(room_hint: Optional[str]) -> Optional[str]:
    raw_hint = str(room_hint or "").strip()
    room_id = resolve_room_id(raw_hint) if raw_hint else get_default_room_id()
    if not room_id:
        return None
    value = get_room_default(room_id, "spotcast_device_name")
    value = str(value or "").strip()
    return value or None


def get_spotcast_device_aliases() -> Dict[str, str]:
    """Map configured room aliases to provider-specific Spotcast names."""
    out: Dict[str, str] = {}
    for room_id, room in (ROOMS or {}).items():
        value = str((room.get("defaults") or {}).get("spotcast_device_name") or "").strip()
        if not value:
            continue
        aliases = {
            str(room_id).strip().lower().replace("_", " "),
            str(room_id).strip().lower().replace("_", ""),
        }
        aliases.update(
            str(alias).strip().lower()
            for alias in (room.get("aliases") or [])
            if str(alias).strip()
        )
        aliases.update(
            str(alias).strip().lower()
            for alias in ((room.get("defaults") or {}).get("spotcast_device_aliases") or [])
            if str(alias).strip()
        )
        for alias in aliases:
            out[alias] = value
    return out


# ============================================================================
# Client manifest
# ============================================================================
#
# build_manifest() produces a JSON-serializable dict describing the rooms
# and what each room makes available to client UIs (menubar app, raycast
# extension, future dashboards, etc.). Served by piphone_http's GET
# /manifest endpoint.
#
# Goals:
# * Single source of truth — every client gets the same view.
# * Forward-compatible — new clients/devices can join without server
#   changes; clients ignore fields they don't understand.
# * Decoupled from action plumbing — clients use this for *layout*, then
#   POST natural-language commands back to PiPhone for actions and
#   subscribe to HA directly for state. The manifest itself contains no
#   credentials.
#
# Schema (informal, see MANIFEST_SCHEMA_VERSION):
# {
#   "schema_version": 1,
#   "default_room":   "living_room",
#   "rooms": [
#     {
#       "id":                "living_room",
#       "display_name":      "Living Room",
#       "aliases":           ["living room"],
#       "ha_area_id":        "living_room",
#       "media_players":     [{"entity": "...", "label": "..."}, ...],
#       "brightness_entity": "light.living_room_brightness" | null,
#       "scenes":            [<scene entry>, ...],
#       "devices":           [<device entry>, ...]
#     },
#     ...
#   ]
# }
#
# Scene entry shapes (one of):
#   {"label": "Bright", "command": "living room bright"}      # NL
#   {"label": "Movie",  "scene":   "scene.movie_time"}        # HA scene
#   {"label": "Plex",   "script":  "script.launch_plex"}      # HA script
#
# Device entry:
#   {"label": "Stair Light", "entity": "light.stair_light"}
# State for the entity comes from HA directly (subscribe to its state
# changes via WebSocket). Click semantics are domain-inferred:
# light.*/switch.* → toggle, lock.* → lock/unlock, etc.

def get_entity_label(entity_id: str) -> str:
    """Return the display label for an entity ID.

    Checks explicit overrides first; falls back to the heuristic."""
    return ENTITY_LABEL_OVERRIDES.get(entity_id) or _pretty_label_from_entity(entity_id)


def _pretty_label_from_entity(entity_id: str) -> str:
    """Heuristic display label from an entity ID for clients that don't
    carry their own labels. e.g. 'media_player.living_room' -> 'Living Room'.
    Clients can override with nicer formatting on their side."""
    s = (entity_id or "").strip()
    if "." in s:
        s = s.split(".", 1)[1]
    # Common token fix-ups for nicer display.
    parts = [p for p in s.replace("-", "_").split("_") if p]
    fixups = {"tv": "TV", "av": "AV", "ai": "AI"}
    return " ".join(fixups.get(p.lower(), p.capitalize()) for p in parts)


def _media_players_for_room(room: Dict[str, Any]) -> List[Dict[str, str]]:
    """Build the media_players list for a room.

    If the room has an explicit "media_players" list, that order is used as-is.
    Otherwise: audio_outputs first (Sonos / speakers), then the TV media player
    if defined and not already in audio_outputs."""
    explicit = room.get("media_players")
    if isinstance(explicit, list) and explicit:
        result = []
        seen: set = set()
        for entry in explicit:
            if not isinstance(entry, dict):
                continue
            e = str(entry.get("entity") or "").strip()
            if e and e not in seen:
                seen.add(e)
                label = str(entry.get("label") or "").strip() or get_entity_label(e)
                result.append({"entity": e, "label": label})
        return result

    entities: List[str] = []
    seen = set()

    for e in (room.get("audio_outputs") or []):
        e = str(e or "").strip()
        if e and e not in seen:
            seen.add(e)
            entities.append(e)

    tv = (room.get("defaults") or {}).get("tv")
    if isinstance(tv, str) and tv.strip() and tv not in seen:
        seen.add(tv)
        entities.append(tv)

    return [{"entity": e, "label": get_entity_label(e)} for e in entities]


def _validated_scene_entry(entry: Any) -> Optional[Dict[str, str]]:
    """Pass through valid scene entries; drop invalid ones."""
    if not isinstance(entry, dict):
        return None
    label = str(entry.get("label") or "").strip()
    if not label:
        return None
    # Discriminated union — exactly one of command / scene / script.
    for key in ("command", "scene", "script"):
        v = entry.get(key)
        if isinstance(v, str) and v.strip():
            return {"label": label, key: v.strip()}
    return None


def _validated_device_entry(entry: Any) -> Optional[Dict[str, str]]:
    if not isinstance(entry, dict):
        return None
    label = str(entry.get("label") or "").strip()
    entity = str(entry.get("entity") or "").strip()
    if not label or not entity:
        return None
    return {"label": label, "entity": entity}


def _room_to_manifest_entry(room_id: str, room: Dict[str, Any]) -> Dict[str, Any]:
    defaults = room.get("defaults") or {}
    brightness_target = defaults.get("brightness_target")
    if not isinstance(brightness_target, dict):
        legacy_brightness = defaults.get("brightness_number") or defaults.get("brightness_light")
        brightness_target = (
            {"type": "entity", "entity_id": legacy_brightness}
            if isinstance(legacy_brightness, str) and legacy_brightness.strip()
            else None
        )

    brightness = None
    if isinstance(brightness_target, dict) and brightness_target.get("type") == "entity":
        candidate = brightness_target.get("entity_id")
        if isinstance(candidate, str) and candidate.strip():
            brightness = candidate.strip()

    scenes_raw = room.get("scenes") or []
    devices_raw = room.get("devices") or []

    return {
        "id":                 room_id,
        "display_name":       room.get("label") or _pretty_label_from_entity(room_id),
        "aliases":            list(room.get("aliases") or []),
        "ha_area_id":         room.get("ha_area_id"),
        "media_players":      _media_players_for_room(room),
        "brightness_entity":  brightness if isinstance(brightness, str) and brightness.strip() else None,
        "brightness_target":  brightness_target,
        "scenes":             [s for s in (_validated_scene_entry(e) for e in scenes_raw) if s],
        "devices":            [d for d in (_validated_device_entry(e) for e in devices_raw) if d],
    }


def build_manifest() -> Dict[str, Any]:
    """Return a JSON-serializable manifest describing every room and what
    each room makes available to client UIs. See module docstring for
    schema details."""
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "default_room":   DEFAULT_ROOM,
        "rooms":          [_room_to_manifest_entry(rid, r) for rid, r in ROOMS.items()],
    }
