"""Resolve and execute a room's configured volume-control strategy.

Each room may route volume language to a Home Assistant number/input_number
helper or directly to a media_player. This keeps provider and entity choices in
the room registry instead of making the parser special-case a default room.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from home_registry import get_room_volume_target


def _ok(value: Any) -> bool:
    return True if value is None else bool(value)


def _state(states_snapshot, entity_id: str) -> Optional[dict]:
    for state in states_snapshot or []:
        if isinstance(state, dict) and state.get("entity_id") == entity_id:
            return state
    return None


def _current_percent(states_snapshot, entity_id: str) -> Optional[float]:
    state = _state(states_snapshot, entity_id)
    if not state:
        return None
    try:
        if entity_id.startswith("media_player."):
            return float((state.get("attributes") or {}).get("volume_level")) * 100.0
        return float(state.get("state"))
    except (TypeError, ValueError):
        return None


def apply_room_volume(
    room_hint: Optional[str],
    value: int,
    *,
    call_ha_service: Callable[[str, dict], Any],
) -> bool:
    """Set a room's configured volume target to an absolute percentage."""
    target = get_room_volume_target(room_hint)
    if not target:
        return False
    entity_id = target["entity_id"]
    value = max(0, min(100, int(value)))
    if entity_id.startswith("media_player."):
        return _ok(call_ha_service(
            "media_player/volume_set",
            {"entity_id": entity_id, "volume_level": value / 100.0},
        ))
    domain = entity_id.split(".", 1)[0]
    return _ok(call_ha_service(
        f"{domain}/set_value",
        {"entity_id": entity_id, "value": value},
    ))


def apply_room_volume_step(
    room_hint: Optional[str],
    step_percent: int,
    *,
    call_ha_service: Callable[[str, dict], Any],
    states_snapshot=None,
) -> bool:
    """Adjust a room's configured target by a signed percentage step."""
    target = get_room_volume_target(room_hint)
    if not target:
        return False
    entity_id = target["entity_id"]
    current = _current_percent(states_snapshot, entity_id)
    if current is None:
        return False
    value = max(0, min(100, int(round(current + int(step_percent)))))
    return apply_room_volume(
        room_hint,
        value,
        call_ha_service=call_ha_service,
    )
