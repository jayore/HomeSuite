"""ha_room_state.py — Shared room state builder for HTTP and WebSocket paths.

Converts an entity_cache dict {entity_id: {"state", "attrs", "lu"}} into the
structured room state event that piphone_ws broadcasts and /state/<room> returns.

The HTTP path builds the cache with ha_states_to_cache() from a one-shot
ha_get_states() call. The WebSocket path (piphone_ws.py) maintains a live cache
from the HA subscribe_entities stream.

Focus arbitration priority (mirrors now_playing_controls.py fallback):
  1. Real Sonos music playing (most recently updated)
  2. Apple TV with metadata
  3. Sonos music paused
  4. Any active Sonos (TV passthrough → Apple TV wins if it has metadata)
  5. TV entity if present in cache
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from home_registry import ROOMS, DEFAULT_ROOM, get_entity_label

try:
    from private_config import HA_URL as _HA_URL
except Exception:
    _HA_URL = ""

# ---------------------------------------------------------------------------
# Room entity index (pre-computed)
# ---------------------------------------------------------------------------

_ROOM_ENTITIES: Dict[str, Dict[str, Any]] = {}
_TV_ENTITIES: Set[str] = set()

# Reverse lookup: entity_id -> room_id. Built in _init() from focus_participants
# and tv entries. Lets process_ha_event credit a state transition to the right
# room without scanning ROOMS on every event.
_ENTITY_TO_ROOM: Dict[str, str] = {}

# Per-room last user-interacted entity. Populated by state transitions detected
# in process_ha_event(). Persists for the life of the process — no TTL. Cleared
# implicitly by process restart. The arbitration in _arbitrate_focus() prefers
# this over the tiered fallback when set and still resolvable.
_last_interacted: Dict[str, str] = {}        # room_id -> entity_id
_last_interacted_ts: Dict[str, float] = {}   # room_id -> epoch (logs/debug)


def _init() -> None:
    for room_id, room in ROOMS.items():
        defaults = room.get("defaults") or {}
        tv = defaults.get("tv")
        focus_parts = list(room.get("focus_participants") or [])
        _ROOM_ENTITIES[room_id] = {
            "audio_outputs": list(room.get("audio_outputs") or []),
            "focus_participants": focus_parts,
            "tv": tv,
            "brightness_light": defaults.get("brightness_light"),
            "devices": [d["entity"] for d in (room.get("devices") or []) if d.get("entity")],
        }
        if tv:
            _TV_ENTITIES.add(tv)
            _ENTITY_TO_ROOM.setdefault(tv, room_id)
        for eid in focus_parts:
            _ENTITY_TO_ROOM.setdefault(eid, room_id)


_init()

# ---------------------------------------------------------------------------
# Cache conversion — HA REST → entity_cache format
# ---------------------------------------------------------------------------


def cache_to_states_list(cache: Dict[str, Dict[str, Any]]) -> list:
    """Convert entity_cache back to ha_get_states() REST-shape list.

    Inverse of ha_states_to_cache. Used to let consumers that expect the REST
    shape ([{entity_id, state, attributes, last_updated, ...}]) read from the
    live WebSocket-fed cache without changing their code.
    """
    out: list = []
    for eid, entry in (cache or {}).items():
        if not eid or not isinstance(entry, dict):
            continue
        lu = entry.get("lu")
        try:
            lu_iso = datetime.utcfromtimestamp(float(lu)).isoformat() + "+00:00" if lu else ""
        except Exception:
            lu_iso = ""
        out.append({
            "entity_id": eid,
            "state": entry.get("state") or "",
            "attributes": entry.get("attrs") or {},
            "last_updated": lu_iso,
            "last_changed": lu_iso,
        })
    return out


def ha_states_to_cache(ha_states: list) -> Dict[str, Dict[str, Any]]:
    """Convert a ha_get_states() response list to entity_cache format.

    HA REST gives last_updated as an ISO-8601 string; we convert to a float
    POSIX timestamp so it matches the format piphone_ws uses from the WS stream.
    """
    cache: Dict[str, Dict[str, Any]] = {}
    for s in ha_states or []:
        eid = (s.get("entity_id") or "").strip()
        if not eid:
            continue
        lu_str = s.get("last_updated") or s.get("last_changed") or ""
        try:
            lu = datetime.fromisoformat(lu_str.replace("Z", "+00:00")).timestamp()
        except Exception:
            lu = 0.0
        cache[eid] = {
            "state": str(s.get("state") or ""),
            "attrs": s.get("attributes") or {},
            "lu": lu,
        }
    return cache


# ---------------------------------------------------------------------------
# Cache accessors
# ---------------------------------------------------------------------------


def _clean(v: Any) -> str:
    return str(v or "").strip()


def _s(eid: str, cache: dict) -> str:
    return _clean((cache.get(eid) or {}).get("state"))


def _a(eid: str, cache: dict) -> dict:
    return (cache.get(eid) or {}).get("attrs") or {}


def _lu(eid: str, cache: dict) -> float:
    lu = (cache.get(eid) or {}).get("lu")
    return float(lu) if lu is not None else 0.0


def _is_playing(eid: str, cache: dict) -> bool:
    return _s(eid, cache).lower() == "playing"


def _is_paused(eid: str, cache: dict) -> bool:
    return _s(eid, cache).lower() == "paused"


def _is_active(eid: str, cache: dict) -> bool:
    return _is_playing(eid, cache) or _is_paused(eid, cache)


def _is_tv_passthrough(eid: str, cache: dict) -> bool:
    """Sonos playing TV audio (soundbar mode) — not real music metadata."""
    a = _a(eid, cache)
    source = _clean(a.get("source")).lower()
    cid = _clean(a.get("media_content_id"))
    title = _clean(a.get("media_title")).lower()
    artist = _clean(a.get("media_artist"))
    station = _clean(a.get("media_station")) or _clean(a.get("media_channel"))
    album = _clean(a.get("media_album_name"))

    if (title and title != "tv") or artist or station or album:
        return False

    cid_l = cid.lower()
    if cid_l.startswith("x-sonos-htastream:"):
        return True
    if "spdif" in cid_l and (source == "tv" or title == "tv"):
        return True
    if source == "tv" and title == "tv":
        return True
    if title == "tv" and not artist and not station:
        return True
    return False


def _tv_has_metadata(eid: str, cache: dict) -> bool:
    """Apple TV can retain useful metadata even in non-playing states."""
    a = _a(eid, cache)
    if _clean(a.get("media_title")):
        return True
    if _clean(a.get("media_series_title")):
        return True
    if _clean(a.get("media_artist")):
        return True
    if _clean(a.get("app_name")) or _clean(a.get("source")):
        return True
    if _clean(a.get("media_content_type")).lower() == "video":
        return True
    return False


# ---------------------------------------------------------------------------
# Focus arbitration
# ---------------------------------------------------------------------------


_FOCUS_EXCLUDED_STATES = {"", "unavailable", "unknown"}


def _arbitrate_focus(room_id: str, cache: dict) -> Tuple[Optional[str], Optional[str]]:
    re_info = _ROOM_ENTITIES.get(room_id) or {}
    focus_participants: List[str] = re_info.get("focus_participants") or []
    tv_eid: Optional[str] = re_info.get("tv")

    # Preferred path: the user's last interacted entity in this room, when
    # still resolvable. Falls through to the tiered logic below when there's
    # no interaction signal yet (cold start, fresh process) or the tracked
    # entity has dropped offline / out of cache.
    tracked = _last_interacted.get(room_id)
    if tracked and tracked in cache:
        tracked_state = _s(tracked, cache).lower()
        if tracked_state not in _FOCUS_EXCLUDED_STATES:
            return tracked, get_entity_label(tracked)

    sonos_playing: List[str] = []
    sonos_paused: List[str] = []
    sonos_any_active: List[str] = []

    for eid in focus_participants:
        if eid == tv_eid or eid not in cache:
            continue
        if _is_active(eid, cache):
            sonos_any_active.append(eid)
        if _is_tv_passthrough(eid, cache):
            continue
        if _is_playing(eid, cache):
            sonos_playing.append(eid)
        elif _is_paused(eid, cache):
            sonos_paused.append(eid)

    def _pick(lst: List[str]) -> Optional[str]:
        return max(lst, key=lambda e: _lu(e, cache)) if lst else None

    def _lbl(eid: Optional[str]) -> Optional[str]:
        return get_entity_label(eid) if eid else None

    if sonos_playing:
        e = _pick(sonos_playing)
        return e, _lbl(e)

    if tv_eid and (_is_active(tv_eid, cache) or _tv_has_metadata(tv_eid, cache)):
        return tv_eid, _lbl(tv_eid)

    if sonos_paused:
        e = _pick(sonos_paused)
        return e, _lbl(e)

    if sonos_any_active:
        e = _pick(sonos_any_active)
        if _is_tv_passthrough(e, cache) and tv_eid and (_is_active(tv_eid, cache) or _tv_has_metadata(tv_eid, cache)):
            return tv_eid, _lbl(tv_eid)
        return e, _lbl(e)

    if tv_eid and tv_eid in cache:
        return tv_eid, _lbl(tv_eid)

    return None, None


# ---------------------------------------------------------------------------
# Player builder
# ---------------------------------------------------------------------------


def _as_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_epoch(v: Any) -> Optional[float]:
    """Coerce HA's media_position_updated_at to a POSIX float (seconds since
    epoch). HA delivers it as a datetime over the WebSocket and as an ISO-8601
    string over REST; either is normalized here. Returns None on missing /
    unparseable input."""
    if v is None:
        return None
    # Already numeric (float/int epoch).
    if isinstance(v, (int, float)):
        try:
            return float(v)
        except Exception:
            return None
    # datetime instance.
    ts = getattr(v, "timestamp", None)
    if callable(ts):
        try:
            return float(ts())
        except Exception:
            return None
    # ISO-8601 string.
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None
    return None


def build_player(eid: str, cache: dict) -> Dict[str, Any]:
    a = _a(eid, cache)
    artwork = _clean(a.get("entity_picture"))
    if artwork and artwork.startswith("/") and _HA_URL:
        artwork = _HA_URL.rstrip("/") + artwork

    def _c(v: Any) -> Optional[str]:
        s = _clean(v)
        return s or None

    return {
        "entity": eid,
        "label": get_entity_label(eid),
        "state": _s(eid, cache),
        "title": _c(a.get("media_title")) or _c(a.get("media_channel")),
        "artist": _c(a.get("media_artist")),
        "album": _c(a.get("media_album_name")),
        "series": _c(a.get("media_series_title")),
        "app": _c(a.get("app_name")) or _c(a.get("source")),
        "volume": a.get("volume_level"),
        "artwork_url": artwork or None,
        "is_tv": eid in _TV_ENTITIES,
        "last_updated": _lu(eid, cache),
        "position": _as_float(a.get("media_position")),
        "duration": _as_float(a.get("media_duration")),
        "position_updated_at": _as_epoch(a.get("media_position_updated_at")),
    }


# ---------------------------------------------------------------------------
# Room state builder
# ---------------------------------------------------------------------------


def build_room_state(room_id: str, cache: dict) -> Dict[str, Any]:
    """Build the room state event dict (same shape as piphone_ws broadcasts).

    Returns an empty players list and null focused fields if no relevant
    entities are in the cache yet.
    """
    re_info = _ROOM_ENTITIES.get(room_id) or {}
    focus_participants: List[str] = re_info.get("focus_participants") or []
    tv_eid: Optional[str] = re_info.get("tv")
    brightness_eid: Optional[str] = re_info.get("brightness_light")

    player_eids: List[str] = []
    seen: Set[str] = set()
    for eid in focus_participants:
        if eid not in seen:
            seen.add(eid)
            player_eids.append(eid)
    if tv_eid and tv_eid not in seen:
        player_eids.append(tv_eid)

    players = [build_player(e, cache) for e in player_eids if e in cache]
    focused_eid, focused_label = _arbitrate_focus(room_id, cache)

    brightness_pct: Optional[float] = None
    if brightness_eid and brightness_eid in cache:
        if _s(brightness_eid, cache) == "off":
            brightness_pct = 0.0
        else:
            bv = _a(brightness_eid, cache).get("brightness")
            if bv is not None:
                try:
                    brightness_pct = round(float(bv) / 255.0 * 100.0, 1)
                except (TypeError, ValueError):
                    pass

    return {
        "event": "state",
        "room": room_id,
        "focused_entity": focused_eid,
        "focused_label": focused_label,
        "players": players,
        "brightness_pct": brightness_pct,
    }


# ---------------------------------------------------------------------------
# HA subscribe_entities event processing
# ---------------------------------------------------------------------------


def _maybe_mark_interaction(eid: str, old_state: str, new_state: str, cache: dict) -> None:
    """Record an interaction for the room owning eid, if this state transition
    qualifies as user activity. Called by process_ha_event after the cache has
    been updated with the new entry.

    Skips:
      - non-transitions (old == new)
      - baseline population / connectivity glitches (old or new in excluded set)
      - entities not tied to any room (not a focus participant or TV)
      - Sonos entering TV-passthrough mode (reaction to TV, not Sonos action)
    """
    if old_state == new_state:
        return
    if old_state in _FOCUS_EXCLUDED_STATES or new_state in _FOCUS_EXCLUDED_STATES:
        return
    room_id = _ENTITY_TO_ROOM.get(eid)
    if not room_id:
        return
    if _is_tv_passthrough(eid, cache):
        return
    _last_interacted[room_id] = eid
    _last_interacted_ts[room_id] = time.time()
    logging.info("FOCUS_INTERACTION room=%s eid=%s %s→%s", room_id, eid, old_state, new_state)


def process_ha_event(event_data: dict, cache: dict) -> Set[str]:
    """Merge a HA subscribe_entities event into cache. Returns changed entity IDs.

    HA compressed format:
      "a" → full entity additions / initial snapshot
      "c" → diffs: {eid: {"+" : {field: val}, "-": {"a": [removed_attr_keys]}}}
      "r" → list of removed entity ids
    """
    changed: Set[str] = set()

    for eid, edata in (event_data.get("a") or {}).items():
        prev = cache.get(eid)
        new = {
            "state": _clean(edata.get("s")),
            "attrs": edata.get("a") or {},
            "lu": float(edata["lu"]) if edata.get("lu") is not None else 0.0,
        }
        if prev != new:
            cache[eid] = new
            changed.add(eid)
            # Skip interaction marking when prev is None — that's baseline
            # population (initial snapshot), not a user transition.
            if prev is not None:
                _maybe_mark_interaction(eid, prev.get("state", ""), new["state"], cache)

    for eid, diff in (event_data.get("c") or {}).items():
        plus = diff.get("+") or {}
        existing = cache.get(eid) or {"state": "", "attrs": {}, "lu": 0.0}
        new = {
            "state": existing["state"],
            "attrs": dict(existing.get("attrs") or {}),
            "lu": existing.get("lu", 0.0),
        }
        state_in_diff = "s" in plus
        if state_in_diff:
            new["state"] = _clean(plus["s"])
        if "a" in plus and isinstance(plus["a"], dict):
            new["attrs"].update(plus["a"])
        if plus.get("lu") is not None:
            new["lu"] = float(plus["lu"])
        for attr_key in ((diff.get("-") or {}).get("a") or []):
            new["attrs"].pop(attr_key, None)
        if new != existing:
            cache[eid] = new
            changed.add(eid)
            if state_in_diff:
                _maybe_mark_interaction(eid, existing.get("state", ""), new["state"], cache)

    for eid in (event_data.get("r") or []):
        if eid in cache:
            cache.pop(eid)
            changed.add(eid)

    return changed


def changed_rooms(changed_eids: Set[str]) -> Set[str]:
    """Return room IDs that have at least one relevant entity in changed_eids."""
    rooms: Set[str] = set()
    for room_id, re_info in _ROOM_ENTITIES.items():
        watched: Set[str] = set(re_info.get("focus_participants") or [])
        watched.update(re_info.get("audio_outputs") or [])
        watched.update(re_info.get("devices") or [])
        for key in ("tv", "brightness_light"):
            v = re_info.get(key)
            if v:
                watched.add(v)
        if changed_eids & watched:
            rooms.add(room_id)
    return rooms
