from __future__ import annotations

from typing import Any, Dict, List, Optional


# ============================================================================
# Home registry
#
# Purpose:
# * define room-level defaults/capabilities in one place
# * define known source instances and their room/policy association
# * provide light lookup helpers for future source-aware request handling
# * define per-room scene/device lists for client manifests (menubar app,
#   Raycast extension, future dashboards — all consume build_manifest() via
#   GET /manifest on piphone_http)
#
# Notes:
# * This is intentionally a first-pass foundation file.
# * Keep data concrete and deployment-oriented.
# * SOURCES here are source instances/endpoints, not abstract source classes.
# * Optional/default-missing values should be represented as None where helpful.
# ============================================================================


# Which room a client (menubar, raycast, etc.) defaults to when the user
# hasn't picked one explicitly.
DEFAULT_ROOM: str = "living_room"

# Schema version exposed in the manifest. Bump when the shape changes in a
# way clients need to adapt to (additive fields don't require a bump).
MANIFEST_SCHEMA_VERSION: int = 1


ROOMS: Dict[str, Dict[str, Any]] = {
    "living_room": {
        "label": "Living Room",
        "ha_area_id": "living_room",
        "aliases": ["living room"],
        "defaults": {
            "lights": "light.living_room_brightness",
            "color_light": "light.living_room_color",
            "brightness_number": None,
            "brightness_light": "light.living_room_brightness",
            "audio_output": "media_player.living_room",
            "announcements": "media_player.living_room",
            "tv": "media_player.living_room_apple_tv",
            "tv_remote": "remote.living_room_apple_tv",
            "tv_on_scene": "scene.tv_on",
            "plex_client_name": "Apple TV",
            "plex_launch_script": "script.launch_plex",
        },
        "media_players": [
            {"entity": "media_player.living_room",          "label": "Living Room"},
            {"entity": "media_player.living_room_apple_tv", "label": "Apple TV"},
            {"entity": "media_player.bookshelf",            "label": "Bookshelf"},
        ],
        "audio_outputs": [
            "media_player.living_room",
            "media_player.bookshelf",
        ],
        "focus_participants": [
            "media_player.living_room",
            "media_player.bookshelf",
        ],
        # Buttons surfaced in client UIs (menubar, raycast, etc.).
        # Each entry is one of:
        #   {"label": "X", "command": "<NL phrase>"}   → POST to PiPhone /command
        #   {"label": "X", "scene":   "scene.X"}       → direct HA scene.turn_on
        #   {"label": "X", "script":  "script.X"}      → direct HA script.turn_on
        # Mix and match freely.
        "scenes": [
            {"label": "Bright",       "command": "living room bright"},
            {"label": "Medium",       "command": "living room medium"},
            {"label": "Low",          "command": "living room low"},
            {"label": "Dim",          "command": "living room dim"},
            {"label": "Off",          "command": "living room off"},
            {"label": "Stair Light",  "command": "toggle stair light"},
            {"label": "Dining Light", "command": "toggle dining light"},
        ],
        # Devices listed in client UIs with their live state.
        # Each entry: {"label": "X", "entity": "<domain.entity_id>"}
        # Click toggles the entity via HA directly (light.toggle, switch.toggle,
        # lock.lock/unlock, etc.) — domain is inferred from the entity prefix.
        # Add devices you actually want to see at a glance; leave the list empty
        # if you don't want a device section for this room.
        "devices": [
            # Example shapes:
            # {"label": "Stair Light", "entity": "light.stair_light"},
            # {"label": "Side Lamp",   "entity": "light.side_lamp"},
            # {"label": "Front Door",  "entity": "lock.front_door"},
        ],
    },
    "bedroom": {
        "label": "Bedroom",
        "ha_area_id": "bedroom",
        "aliases": ["bedroom"],
        "defaults": {
            "lights": None,
            "color_light": "light.bedroom_color",
            "brightness_number": None,
            "brightness_light": "light.bedroom_brightness",
            "audio_output": "media_player.bedroom",
            "announcements": "media_player.bedroom",
            "tv": None,
        },
        "audio_outputs": [
            "media_player.bedroom",
        ],
        "focus_participants": [
            "media_player.bedroom",
        ],
        "scenes": [
            {"label": "Bright", "command": "bedroom bright"},
            {"label": "Medium", "command": "bedroom medium"},
            {"label": "Low",    "command": "bedroom low"},
            {"label": "Dim",    "command": "bedroom dim"},
            {"label": "Off",    "command": "bedroom off"},
        ],
        "devices": [],
    },
    "kitchen": {
        "label": "Kitchen",
        "ha_area_id": "kitchen",
        "aliases": ["kitchen"],
        "defaults": {
            "lights": None,
            "color_light": None,
            "brightness_number": None,
            "brightness_light": "light.kitchen_brightness",
            "audio_output": "media_player.kitchen",
            "announcements": "media_player.kitchen",
            "tv": None,
        },
        "audio_outputs": [
            "media_player.kitchen",
        ],
        "focus_participants": [
            "media_player.kitchen",
        ],
        "scenes": [
            {"label": "Bright",       "command": "kitchen bright"},
            {"label": "Medium",       "command": "kitchen medium"},
            {"label": "Low",          "command": "kitchen low"},
            {"label": "Dim",          "command": "kitchen dim"},
            {"label": "Off",          "command": "kitchen off"},
            {"label": "Stair Light",  "command": "toggle stair light"},
            {"label": "Dining Light", "command": "toggle dining light"},
        ],
        "devices": [],
    },
    "bathroom": {
        "label": "Bathroom",
        "ha_area_id": "bathroom",
        "aliases": ["bathroom"],
        "defaults": {
            "lights": None,
            "color_light": None,
            "brightness_number": None,
            "brightness_light": None,
            "audio_output": "media_player.bathroom",
            "announcements": "media_player.bathroom",
            "tv": None,
        },
        "audio_outputs": [
            "media_player.bathroom",
        ],
        "focus_participants": [
            "media_player.bathroom",
        ],
        "scenes": [
            {"label": "Bright", "command": "bathroom bright"},
            {"label": "Medium", "command": "bathroom medium"},
            {"label": "Low",    "command": "bathroom low"},
            {"label": "Dim",    "command": "bathroom dim"},
            {"label": "Off",    "command": "bathroom off"},
        ],
        "devices": [],
    },
    "office": {
        "label": "Office",
        "ha_area_id": "office",
        "aliases": ["office"],
        "defaults": {
            "lights": None,
            "color_light": "light.office_color",
            "brightness_number": None,
            "brightness_light": "light.office_brightness",
            "audio_output": "media_player.office",
            "announcements": "media_player.office",
            "tv": None,
        },
        "audio_outputs": [
            "media_player.office",
        ],
        "focus_participants": [
            "media_player.office",
        ],
        "scenes": [
            {"label": "Bright", "command": "office bright"},
            {"label": "Medium", "command": "office medium"},
            {"label": "Low",    "command": "office low"},
            {"label": "Dim",    "command": "office dim"},
            {"label": "Off",    "command": "office off"},
        ],
        "devices": [],
    },
}



SOURCES: Dict[str, Dict[str, Any]] = {
    # Local/default appliance source for the current PiPhone runtime.
    #
    # `mobile`: whether the source can change its own room focus at runtime via
    # an "I'm in the <room>" command. Stationary devices (the handset, physical
    # buttons, the out-loud wakeword option) are fixed to their room and must
    # refuse room changes. Portable frontends (the menubar app, Raycast,
    # Telegram) are mobile and remember a sticky room per `device_group`/id.
    "default_piphone": {
        "label": "Default PiPhone",
        "type": "piphone",
        "room": "living_room",
        "mobile": False,
        "default_scope": "room_local",
        "focus_policy": "sticky",
        "output_mode": "inherit_room",
    },

    # Room-agnostic sources.
    "telegram": {
        "label": "Telegram",
        "type": "telegram",
        "room": None,
        "mobile": True,
        "default_scope": "none",
        "focus_policy": "sticky_recent_room",
        "output_mode": "none",
    },
    "http": {
        "label": "HTTP",
        "type": "http",
        "room": None,
        "mobile": True,
        "default_scope": "none",
        "focus_policy": "explicit_or_recent_room",
        "output_mode": "inherit_request",
    },
    # Mac menubar app and Raycast extension run on the same laptop, so they
    # share one logical "laptop" room focus via `device_group`.
    "menubar": {
        "label": "Menubar app",
        "type": "remote",
        "room": None,
        "mobile": True,
        "device_group": "laptop",
        "default_scope": "none",
        "focus_policy": "explicit_or_recent_room",
        "output_mode": "inherit_request",
    },
    "raycast": {
        "label": "Raycast",
        "type": "remote",
        "room": None,
        "mobile": True,
        "device_group": "laptop",
        "default_scope": "none",
        "focus_policy": "explicit_or_recent_room",
        "output_mode": "inherit_request",
    },
    "scheduler": {
        "label": "Scheduler",
        "type": "scheduler",
        "room": None,
        "mobile": False,
        "default_scope": "none",
        "focus_policy": "none",
        "output_mode": "none",
    },
    "physical_button": {
        "label": "Physical Button",
        "type": "button",
        "room": "living_room",
        "mobile": False,
        "default_scope": "room_local",
        "focus_policy": "sticky",
        "output_mode": "none",
    },
}


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
    return SOURCES.get(str(source_id).strip())


def get_source_room(source_id: Optional[str]) -> Optional[str]:
    src = get_source(source_id)
    if not src:
        return None
    room = src.get("room")
    if room is None:
        return None
    room = str(room).strip()
    return room or None


def is_source_mobile(source_id: Optional[str]) -> bool:
    """
    Return True only for sources explicitly marked mobile in the registry.

    Unknown or unregistered sources default to False (not mobile) so that an
    unrecognized frontend can never silently change room focus.
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
    if not name:
        return None
    needle = str(name).strip().lower()
    if not needle:
        return None

    for room_id, room in ROOMS.items():
        aliases = room.get("aliases") or []
        alias_set = {str(room_id).strip().lower()}
        alias_set.update(str(a).strip().lower() for a in aliases if str(a).strip())
        if needle in alias_set:
            return room_id
    return None


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

# Explicit display-label overrides for entities where the heuristic produces
# a misleading result (e.g., "Living Room Apple TV" → "Apple TV").
_ENTITY_LABEL_OVERRIDES: Dict[str, str] = {
    "media_player.living_room_apple_tv": "Apple TV",
}


def get_entity_label(entity_id: str) -> str:
    """Return the display label for an entity ID.

    Checks explicit overrides first; falls back to the heuristic."""
    return _ENTITY_LABEL_OVERRIDES.get(entity_id) or _pretty_label_from_entity(entity_id)


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
    brightness = defaults.get("brightness_light")

    scenes_raw = room.get("scenes") or []
    devices_raw = room.get("devices") or []

    return {
        "id":                 room_id,
        "display_name":       room.get("label") or _pretty_label_from_entity(room_id),
        "aliases":            list(room.get("aliases") or []),
        "ha_area_id":         room.get("ha_area_id"),
        "media_players":      _media_players_for_room(room),
        "brightness_entity":  brightness if isinstance(brightness, str) and brightness.strip() else None,
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
