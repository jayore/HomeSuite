"""Translate request rooms and spoken aliases into media target defaults.

The registry uses stable room IDs while Sonos maps, Apple TV configuration, and
spoken commands may use different aliases. These helpers bridge those forms and
derive the default Sonos or TV target for the active request. Explicit room
language wins over request context, which wins over global defaults.

The remembered Sonos coordinator is short-lived routing state, not a substitute
for current Home Assistant group state.
"""

import re
from typing import Optional

from app_config import SONOS_PLAYERS, DEFAULT_SONOS_ROOM, APPLE_TV_ENTITY, APPLE_TV_REMOTE
from home_registry import get_room, find_room_by_alias
from request_context import get_active_room_for_request_defaults


def _norm_sonos_room_key(room: Optional[str]) -> str:
    s = (room or "").strip().lower()
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _request_room_to_sonos_room(room_id: Optional[str]) -> Optional[str]:
    """Convert request-context room ids/aliases into SONOS_PLAYERS keys."""
    if not room_id:
        return None

    raw = str(room_id).strip()
    if not raw:
        return None

    direct = raw.strip().lower()
    if direct in SONOS_PLAYERS:
        return direct

    spoken = _norm_sonos_room_key(raw)
    if spoken in SONOS_PLAYERS:
        return spoken

    try:
        room_cfg = get_room(raw)
    except Exception:
        room_cfg = None

    if isinstance(room_cfg, dict):
        aliases = room_cfg.get("aliases") or []
        for alias in aliases:
            a = _norm_sonos_room_key(str(alias))
            if a in SONOS_PLAYERS:
                return a

    return None


def _request_default_sonos_room(room_override: Optional[str] = None) -> str:
    """Resolve the effective Sonos room using override, request, then default."""
    """
    Resolve the default Sonos/audio room for this request.

    Precedence:
      1) explicit room mentioned in this utterance, if it maps to a Sonos player
      2) request effective/default room, if it maps to a Sonos player
      3) legacy DEFAULT_SONOS_ROOM
    """
    mapped = _request_room_to_sonos_room(room_override)
    if mapped:
        return mapped

    try:
        req_room = get_active_room_for_request_defaults()
    except Exception:
        req_room = None

    mapped = _request_room_to_sonos_room(req_room)
    if mapped:
        return mapped

    fallback = _norm_sonos_room_key(DEFAULT_SONOS_ROOM)
    if fallback in SONOS_PLAYERS:
        return fallback

    return DEFAULT_SONOS_ROOM


def _registry_room_id_from_any(room: Optional[str]) -> Optional[str]:
    """
    Normalize room ids/aliases for registry lookup.

    Handles: living_room, living room, kitchen, etc.
    """
    raw = (room or "").strip()
    if not raw:
        return None

    try:
        if get_room(raw):
            return raw
    except Exception:
        pass

    underscored = raw.replace(" ", "_").strip()
    try:
        if underscored and get_room(underscored):
            return underscored
    except Exception:
        pass

    try:
        by_alias = find_room_by_alias(raw)
        if by_alias:
            return by_alias
    except Exception:
        pass

    spoken = raw.replace("_", " ").strip()
    try:
        by_alias = find_room_by_alias(spoken)
        if by_alias:
            return by_alias
    except Exception:
        pass

    return None


def _known_room_aliases_for_text():
    """
    Return (room_id, alias) pairs for explicit room phrase detection.

    Defensive — room parsing must never break command handling.
    """
    out = []

    try:
        import home_registry as _hr
        rooms = getattr(_hr, "ROOMS", {}) or {}
    except Exception:
        rooms = {}

    try:
        if isinstance(rooms, dict):
            for room_id, cfg in rooms.items():
                rid = str(room_id or "").strip()
                if not rid:
                    continue

                aliases = set()
                aliases.add(rid)
                aliases.add(rid.replace("_", " "))

                if isinstance(cfg, dict):
                    for alias in (cfg.get("aliases") or []):
                        a = str(alias or "").strip()
                        if a:
                            aliases.add(a)

                for alias in aliases:
                    a = str(alias or "").strip().lower()
                    if a:
                        out.append((rid, a))
    except Exception:
        pass

    # Supplement from SONOS_PLAYERS — media routing still uses it as source of truth.
    try:
        for room in (SONOS_PLAYERS or {}).keys():
            r = str(room or "").strip().lower()
            if not r:
                continue
            rid = _registry_room_id_from_any(r) or r.replace(" ", "_")
            out.append((rid, r))
    except Exception:
        pass

    # Deduplicate, longest aliases first.
    seen = set()
    deduped = []
    for rid, alias in out:
        key = (rid, alias)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((rid, alias))

    deduped.sort(key=lambda x: len(x[1]), reverse=True)
    return deduped


def _extract_explicit_room_id_from_text(text: str) -> Optional[str]:
    """
    Extract an explicit room mention from an utterance.

    Conservative and phrase-based — only affects the current command,
    not persistent room focus.

    Examples:
      what's playing in the kitchen -> kitchen
      pause in the bedroom          -> bedroom
      kitchen volume                -> kitchen
      set bedroom brightness to 50  -> bedroom
    """
    t = (text or "").strip().lower()
    if not t:
        return None

    t = t.replace("’", "'").replace("‘", "'")
    t = re.sub(r"[?!.]+$", "", t).strip()
    t = re.sub(r"\s+", " ", t).strip()

    if not t:
        return None

    media_or_device_nouns = (
        "tv", "television", "apple tv",
        "speaker", "speakers", "sonos", "music",
        "volume", "brightness", "light", "lights",
        "lamp", "lamps", "color", "colour",
    )

    room_aliases = _known_room_aliases_for_text()

    for room_id, alias in room_aliases:
        a = re.escape(alias)

        if re.search(rf"\b(?:in|on|to|for)\s+(?:the\s+)?{a}\b", t):
            return _registry_room_id_from_any(room_id) or room_id

        noun_alt = "|".join(re.escape(x) for x in media_or_device_nouns)
        if re.search(rf"\b(?:the\s+)?{a}\s+(?:{noun_alt})\b", t):
            return _registry_room_id_from_any(room_id) or room_id

        if re.search(rf"\b(?:pause|play|resume|stop|next|previous|prev)\s+(?:the\s+)?{a}\b", t):
            return _registry_room_id_from_any(room_id) or room_id

    return None


def _request_default_tv_context(room_override: Optional[str] = None) -> dict:
    """Return effective room plus configured Apple TV player/remote entities."""
    """
    Resolve request-aware TV / Apple TV / Plex defaults.

    Rules:
    * explicit room mentioned in the utterance wins for this command
    * roomless/default requests preserve legacy living-room TV behavior
    * request rooms only get TV behavior when the registry defines a TV
    * rooms without TV config must not silently fall through to living-room TV
    """
    room_id = _registry_room_id_from_any(room_override)

    if not room_id:
        try:
            req_room = get_active_room_for_request_defaults()
        except Exception:
            req_room = None

        room_id = _registry_room_id_from_any(req_room)

    # Preserve legacy behavior for roomless sources / default physical phone.
    if not room_id:
        room_id = "living_room"

    room_cfg = get_room(room_id) or {}
    defaults = room_cfg.get("defaults") or {}
    if not isinstance(defaults, dict):
        defaults = {}

    tv_entity = defaults.get("tv")
    tv_remote = defaults.get("tv_remote")
    tv_on_scene = defaults.get("tv_on_scene")
    plex_client_name = defaults.get("plex_client_name")
    plex_launch_script = defaults.get("plex_launch_script")

    # Backward-compatible fallback for the known living-room setup.
    if room_id == "living_room":
        tv_entity = tv_entity or APPLE_TV_ENTITY
        tv_remote = tv_remote or APPLE_TV_REMOTE
        tv_on_scene = tv_on_scene or "scene.tv_on"
        plex_client_name = plex_client_name or "Apple TV"
        plex_launch_script = plex_launch_script or "script.launch_plex"

    def clean(v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    return {
        "room_id": room_id,
        "tv_entity": clean(tv_entity),
        "tv_remote": clean(tv_remote),
        "tv_on_scene": clean(tv_on_scene),
        "plex_client_name": clean(plex_client_name),
        "plex_launch_script": clean(plex_launch_script),
    }


# Mutable state: last Sonos room that was used as a group master.
# Accessed via the getter/setter below, which are passed as callbacks into sonos_controls.
last_sonos_master_room: Optional[str] = None


def _get_last_sonos_master_room() -> Optional[str]:
    return last_sonos_master_room


def _set_last_sonos_master_room(room: str):
    global last_sonos_master_room
    last_sonos_master_room = (room or "").strip().lower()
