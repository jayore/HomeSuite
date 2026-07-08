from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

from home_registry import (
    get_source,
    get_source_room,
    get_source_room_key,
    get_room_default,
    is_source_mobile,
    ROOMS,
)
from source_room_state import get_current_room as _get_remembered_room


@dataclass(frozen=True)
class RequestContext:
    source_id: Optional[str] = None
    source_type: Optional[str] = None
    origin: Optional[str] = None
    source_room: Optional[str] = None
    effective_target_room: Optional[str] = None

    def to_log_dict(self) -> Dict[str, Any]:
        return asdict(self)


_CURRENT_REQUEST_CONTEXT: Optional[RequestContext] = None


def _clean_optional(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def build_request_context(
    *,
    source_id: Optional[str] = None,
    source_type: Optional[str] = None,
    origin: Optional[str] = None,
    source_room: Optional[str] = None,
    effective_target_room: Optional[str] = None,
) -> RequestContext:
    source_id = _clean_optional(source_id)
    source_type = _clean_optional(source_type)
    origin = _clean_optional(origin)
    source_room = _clean_optional(source_room)
    effective_target_room = _clean_optional(effective_target_room)

    src = get_source(source_id) if source_id else None

    if not source_type and isinstance(src, dict):
        source_type = _clean_optional(src.get("type"))

    if not source_room:
        source_room = get_source_room(source_id)

    # Portable sources remember a sticky room set via "I'm in the <room>".
    # Only fill it in when the caller supplied no explicit room of any kind, so
    # an explicit per-request target_room from a client still wins.
    if not source_room and not effective_target_room and is_source_mobile(source_id):
        try:
            remembered = _clean_optional(_get_remembered_room(get_source_room_key(source_id)))
        except Exception:
            remembered = None
        if remembered:
            source_room = remembered

    if not effective_target_room:
        effective_target_room = source_room

    return RequestContext(
        source_id=source_id,
        source_type=source_type,
        origin=origin,
        source_room=source_room,
        effective_target_room=effective_target_room,
    )


def set_current_request_context(ctx: Optional[RequestContext]) -> None:
    global _CURRENT_REQUEST_CONTEXT
    _CURRENT_REQUEST_CONTEXT = ctx


def replace_current_request_context(ctx: Optional[RequestContext]) -> Optional[RequestContext]:
    global _CURRENT_REQUEST_CONTEXT
    previous = _CURRENT_REQUEST_CONTEXT
    _CURRENT_REQUEST_CONTEXT = ctx
    return previous


def get_current_request_context() -> Optional[RequestContext]:
    return _CURRENT_REQUEST_CONTEXT


def clear_current_request_context() -> None:
    global _CURRENT_REQUEST_CONTEXT
    _CURRENT_REQUEST_CONTEXT = None

# Sources that are currently allowed to supply implicit room-local context.
# This is intentionally conservative for now.
_ROOM_LOCAL_SOURCE_IDS = {
    "default_piphone",
    "physical_button",
}


def get_current_source_id() -> Optional[str]:
    ctx = get_current_request_context()
    if not ctx:
        return None
    return _clean_optional(ctx.source_id)


def get_current_source_room() -> Optional[str]:
    ctx = get_current_request_context()
    if not ctx:
        return None
    return _clean_optional(ctx.source_room)


def get_current_effective_target_room() -> Optional[str]:
    ctx = get_current_request_context()
    if not ctx:
        return None
    return _clean_optional(ctx.effective_target_room)


def request_has_room_local_context() -> bool:
    """
    Return True only for sources currently allowed to supply implicit room-local
    defaults.

    This is intentionally conservative:
    * default_piphone -> room-local (fixed room)
    * physical_button -> room-local (fixed room)
    * mobile sources (telegram / http / menubar / raycast) -> room-local only
      once they've set a sticky room via "I'm in the <room>"
    * scheduler -> never room-local
    """
    source_id = get_current_source_id()
    source_room = get_current_source_room()

    if not source_id or not source_room:
        return False

    if source_id in _ROOM_LOCAL_SOURCE_IDS:
        return True

    # A mobile source that has resolved a sticky room (present in source_room
    # above) is also supplying room-local context for this request.
    try:
        return is_source_mobile(source_id)
    except Exception:
        return False


def get_active_room_for_request_defaults() -> Optional[str]:
    """
    Resolve the active room for request-aware room defaults.

    Precedence:
    1. explicit/effective target room, if present
    2. room-local source room, if allowed by current source policy
    3. no implicit room default
    """
    effective_room = get_current_effective_target_room()
    if effective_room:
        return effective_room

    if request_has_room_local_context():
        return get_current_source_room()

    return None


def get_room_default_for_request(key: str, fallback=None):
    """
    Return a room default for the active request when a usable room context is
    available.

    This helper now prefers the effective target room when explicitly present,
    and otherwise falls back to allowed room-local source context.
    """
    key = _clean_optional(key)
    if not key:
        return fallback

    room = get_active_room_for_request_defaults()
    if not room:
        return fallback

    return get_room_default(room, key, fallback)



def get_area_id_for_current_request() -> Optional[str]:
    """
    Return the Home Assistant area_id for the active request only when the
    current request is allowed to supply implicit room-local context.
    """
    if not request_has_room_local_context():
        return None

    room = get_current_source_room()
    if not room:
        return None

    room_cfg = ROOMS.get(room) or {}
    return _clean_optional(room_cfg.get("ha_area_id"))
