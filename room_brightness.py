"""Resolve and execute a room's configured brightness-control strategy.

Room-wide brightness language should not select a backend by phrasing. Each
room may target one helper/entity, its Home Assistant area, or an explicit list
of lights. Legacy brightness_number/brightness_light keys remain supported.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from home_registry import (
    find_room_by_alias,
    get_default_room_id,
    get_room,
    get_room_defaults,
)
from request_context import get_active_room_for_request_defaults


def resolve_room_id(room_hint: Optional[str] = None) -> Optional[str]:
    """Resolve an explicit room hint or the active request room."""
    raw = str(room_hint or "").strip()
    if not raw:
        raw = str(get_active_room_for_request_defaults() or "").strip()
    if not raw:
        return get_default_room_id()

    if get_room(raw):
        return raw
    underscored = raw.lower().replace(" ", "_")
    if get_room(underscored):
        return underscored
    return find_room_by_alias(raw)


def _normalize_target(room_id: str, raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None

    target_type = str(raw.get("type") or "").strip().lower()
    if target_type == "area":
        room = get_room(room_id) or {}
        area_id = str(raw.get("area_id") or room.get("ha_area_id") or "").strip()
        return (
            {"type": "area", "area_id": area_id, "room_id": room_id}
            if area_id
            else None
        )

    if target_type == "entity":
        entity_id = str(raw.get("entity_id") or "").strip()
        if entity_id.startswith(("light.", "number.", "input_number.")):
            return {"type": "entity", "entity_id": entity_id, "room_id": room_id}
        return None

    if target_type == "entities":
        values = raw.get("entity_ids")
        if not isinstance(values, (list, tuple)):
            return None
        entity_ids = [str(value or "").strip() for value in values]
        if not entity_ids or any(not value.startswith("light.") for value in entity_ids):
            return None
        if len(set(entity_ids)) != len(entity_ids):
            return None
        return {"type": "entities", "entity_ids": entity_ids, "room_id": room_id}

    return None


def get_room_brightness_target(room_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return a validated target, preferring the new schema over legacy keys."""
    room_id = resolve_room_id(room_hint)
    if not room_id:
        return None

    defaults = get_room_defaults(room_id)
    if "brightness_target" in defaults:
        return _normalize_target(room_id, defaults.get("brightness_target"))

    # Backward compatibility: the number helper historically won over a light
    # helper when both were configured.
    legacy_number = str(defaults.get("brightness_number") or "").strip()
    if legacy_number:
        return _normalize_target(
            room_id,
            {"type": "entity", "entity_id": legacy_number},
        )

    legacy_light = str(defaults.get("brightness_light") or "").strip()
    if legacy_light:
        return _normalize_target(
            room_id,
            {"type": "entity", "entity_id": legacy_light},
        )
    return None


def _ok(value: Any) -> bool:
    # Dry-run callers commonly return None after recording the intended call.
    return True if value is None else bool(value)


def _set_number(call_ha_service, entity_id: str, value: int) -> bool:
    domain = entity_id.split(".", 1)[0]
    return _ok(call_ha_service(
        f"{domain}/set_value",
        {"entity_id": entity_id, "value": value},
    ))


def apply_room_brightness(
    room_hint: Optional[str],
    value: int,
    *,
    call_ha_service: Callable[[str, dict], Any],
    remember_light: Optional[Callable[[str], None]] = None,
) -> bool:
    """Apply an absolute 0-100 brightness value to a configured room target."""
    target = get_room_brightness_target(room_hint)
    if not target:
        return False

    value = max(0, min(100, int(value)))
    target_type = target["type"]

    if target_type == "area":
        service = "light/turn_off" if value == 0 else "light/turn_on"
        payload = {"area_id": target["area_id"]}
        if value:
            payload["brightness_pct"] = value
        return _ok(call_ha_service(service, payload))

    if target_type == "entities":
        service = "light/turn_off" if value == 0 else "light/turn_on"
        payload = {"entity_id": target["entity_ids"]}
        if value:
            payload["brightness_pct"] = value
        return _ok(call_ha_service(service, payload))

    entity_id = target["entity_id"]
    if entity_id.startswith(("number.", "input_number.")):
        return _set_number(call_ha_service, entity_id, value)

    ok = _ok(call_ha_service(
        "light/turn_on",
        {"entity_id": entity_id, "brightness_pct": value},
    ))
    if ok and remember_light:
        remember_light(entity_id)
    return ok


def _state_value(states_snapshot, entity_id: str) -> Optional[float]:
    for state in states_snapshot or []:
        if not isinstance(state, dict) or state.get("entity_id") != entity_id:
            continue
        try:
            return float(state.get("state"))
        except (TypeError, ValueError):
            return None
    return None


def apply_room_brightness_step(
    room_hint: Optional[str],
    step_pct: int,
    *,
    call_ha_service: Callable[[str, dict], Any],
    states_snapshot=None,
    remember_light: Optional[Callable[[str], None]] = None,
) -> bool:
    """Apply a relative brightness step to a configured room target."""
    target = get_room_brightness_target(room_hint)
    if not target:
        return False

    step_pct = max(-100, min(100, int(step_pct)))
    target_type = target["type"]

    if target_type == "area":
        return _ok(call_ha_service(
            "light/turn_on",
            {"area_id": target["area_id"], "brightness_step_pct": step_pct},
        ))

    if target_type == "entities":
        return _ok(call_ha_service(
            "light/turn_on",
            {"entity_id": target["entity_ids"], "brightness_step_pct": step_pct},
        ))

    entity_id = target["entity_id"]
    if entity_id.startswith(("number.", "input_number.")):
        current = _state_value(states_snapshot, entity_id)
        if current is None:
            return False
        return _set_number(
            call_ha_service,
            entity_id,
            max(0, min(100, int(round(current + step_pct)))),
        )

    ok = _ok(call_ha_service(
        "light/turn_on",
        {"entity_id": entity_id, "brightness_step_pct": step_pct},
    ))
    if ok and remember_light:
        remember_light(entity_id)
    return ok
