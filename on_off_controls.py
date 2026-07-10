"""Handle deterministic on, off, toggle, and runnable-entity commands.

Targets may be explicit entities, configured aliases, or a room's verified
Home Assistant area. Generic room lights are resolved through request context;
named devices must resolve through the injected entity resolver. An unresolved
phrase returns ``None`` and never becomes a fabricated entity ID.

The handler is intentionally limited to binary actions. Brightness, color, and
media transport are claimed by their more specific modules.
"""

import re
import os

try:
    from env_compat import install_homesuite_env_aliases
    install_homesuite_env_aliases()
except Exception:
    pass
import logging
from typing import Optional, Tuple

from request_context import get_area_id_for_current_request
from home_registry import find_room_by_alias, get_room

try:
    from app_config import TURN_ON_PHRASE_OVERRIDES, TURN_OFF_PHRASE_OVERRIDES
except Exception:
    TURN_ON_PHRASE_OVERRIDES = {}
    TURN_OFF_PHRASE_OVERRIDES = {}


def _norm_target(raw: str) -> str:
    t = (raw or "").strip().lower()
    t = re.sub(r"[^a-z0-9\s]+", " ", t)
    t = re.sub(r"\b(the|a|an)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t



def _is_ok(x) -> bool:
    # In pptest, call_ha_service often returns None (dry-run). Treat that as success.
    return True if x is None else bool(x)

def _run_runnable_entity(entity_id: str, *, call_ha_service) -> bool:
    if not isinstance(entity_id, str) or "." not in entity_id:
        return False
    if entity_id.startswith("scene."):
        return _is_ok(call_ha_service("scene/turn_on", {"entity_id": entity_id}))
    if entity_id.startswith("script."):
        return _is_ok(call_ha_service("script/turn_on", {"entity_id": entity_id}))
    return False



def _say_or_blank(maybe_say, text: str) -> str:
    """Return speech text; in pptest return a visible CLAIM string so the REPL shows routing."""
    try:
        out = maybe_say(text) if maybe_say else None
    except Exception:
        out = None

    if out is None:
        # pptest / dry-run: show something in the REPL instead of printing nothing
        if os.environ.get("PIPHONE_LIVE") != "1":
            return f"CLAIM: on_off_controls — {text}"
        return ""

    return out


def _extract_explicit_room_lights_target(raw: str) -> Optional[str]:
    """
    Return HA area_id for explicit room-wide light phrases like:
      * living room lights
      * the kitchen lights
      * office light
    Returns None if this is not an explicit room-wide light target.
    """
    s = (raw or "").strip().lower()
    if not s:
        return None

    s = re.sub(r"^the\s+", "", s).strip()

    m = re.fullmatch(r"(.+?)\s+lights?", s)
    if not m:
        return None

    room_phrase = (m.group(1) or "").strip()
    if not room_phrase:
        return None

    room_id = find_room_by_alias(room_phrase)
    if not room_id:
        return None

    room_cfg = get_room(room_id) or {}
    area_id = room_cfg.get("ha_area_id")
    if not isinstance(area_id, str):
        return None

    area_id = area_id.strip()
    return area_id or None
def handle_on_off_controls(
    *,
    tl: str,
    call_ha_service,
    maybe_say,
    resolve_device_entity,
) -> Optional[str]:
    """Claim and execute explicit binary device or room-area commands."""
    """
    Handles:
        - "turn on <thing>"
        - "turn off <thing>"

    Supports phrase overrides via app_config:
        TURN_ON_PHRASE_OVERRIDES / TURN_OFF_PHRASE_OVERRIDES
    Values can be "scene.*" or "script.*".
    """
    t = (tl or "").strip().lower()

    m_on = re.search(r"\bturn on (?:the )?(.+)\b", t)
    if m_on:
        raw = m_on.group(1).strip()

        # Explicit room-wide lights via mapped HA area_id.
        explicit_area_id = _extract_explicit_room_lights_target(raw)
        if explicit_area_id:
            ok = _is_ok(call_ha_service("light/turn_on", {"area_id": explicit_area_id}))
            return _say_or_blank(maybe_say, "Okay.") if ok else None

        # Narrow room-local generic light control via current request context.
        if _norm_target(raw) in ("light", "lights"):
            area_id = get_area_id_for_current_request()
            if area_id:
                ok = _is_ok(call_ha_service("light/turn_on", {"area_id": area_id}))
                return _say_or_blank(maybe_say, "Okay.") if ok else None

        key = _norm_target(raw)
        forced = (TURN_ON_PHRASE_OVERRIDES or {}).get(key)
        if forced:
            ok = _run_runnable_entity(forced, call_ha_service=call_ha_service)
            return _say_or_blank(maybe_say, "Okay.") if ok else None

        resolved = resolve_device_entity(raw)
        if not resolved:
            if os.environ.get("PIPHONE_LIVE") != "1":
                return f"CLAIM: on_off_controls — no match for '{raw}'"
            return None
        eid, domain = resolved
        ok = _is_ok(call_ha_service(f"{domain}/turn_on", {"entity_id": eid}))
        return _say_or_blank(maybe_say, f"Turning on {raw}.") if ok else None

    m_off = re.search(r"\bturn off (?:the )?(.+)\b", t)
    if m_off:
        raw = m_off.group(1).strip()

        # Explicit room-wide lights via mapped HA area_id.
        explicit_area_id = _extract_explicit_room_lights_target(raw)
        if explicit_area_id:
            ok = _is_ok(call_ha_service("light/turn_off", {"area_id": explicit_area_id}))
            return _say_or_blank(maybe_say, "Okay.") if ok else None

        # Narrow room-local generic light control via current request context.
        if _norm_target(raw) in ("light", "lights"):
            area_id = get_area_id_for_current_request()
            if area_id:
                ok = _is_ok(call_ha_service("light/turn_off", {"area_id": area_id}))
                return _say_or_blank(maybe_say, "Okay.") if ok else None

        key = _norm_target(raw)
        forced = (TURN_OFF_PHRASE_OVERRIDES or {}).get(key)
        if forced:
            ok = _run_runnable_entity(forced, call_ha_service=call_ha_service)
            return _say_or_blank(maybe_say, "Okay.") if ok else None

        resolved = resolve_device_entity(raw)
        if not resolved:
            if os.environ.get("PIPHONE_LIVE") != "1":
                return f"CLAIM: on_off_controls — no match for '{raw}'"
            return None
        eid, domain = resolved
        ok = _is_ok(call_ha_service(f"{domain}/turn_off", {"entity_id": eid}))
        return _say_or_blank(maybe_say, f"Turning off {raw}.") if ok else None

    # --------------------------------------------------
    # Relaxed forms: "<thing> off" / "<thing> on"
    # Examples:
    #   "dining light off"
    #   "tv off"
    # --------------------------------------------------
    m_bare_off = re.fullmatch(r"(?:the\s+)?(.+?)\s+off\b", t)
    if m_bare_off and not re.search(r"\bturn\s+off\b", t):
        raw = m_bare_off.group(1).strip()

        # Explicit room-wide lights via mapped HA area_id.
        explicit_area_id = _extract_explicit_room_lights_target(raw)
        if explicit_area_id:
            ok = _is_ok(call_ha_service("light/turn_off", {"area_id": explicit_area_id}))
            return _say_or_blank(maybe_say, "Okay.") if ok else None

        # Narrow room-local generic light control via current request context.
        if _norm_target(raw) in ("light", "lights"):
            area_id = get_area_id_for_current_request()
            if area_id:
                ok = _is_ok(call_ha_service("light/turn_off", {"area_id": area_id}))
                return _say_or_blank(maybe_say, "Okay.") if ok else None

        key = _norm_target(raw)
        forced = (TURN_OFF_PHRASE_OVERRIDES or {}).get(key)
        if forced:
            ok = _run_runnable_entity(forced, call_ha_service=call_ha_service)
            return _say_or_blank(maybe_say, "Okay.") if ok else None

        resolved = resolve_device_entity(raw)
        if resolved:
            eid, domain = resolved
            ok = _is_ok(call_ha_service(f"{domain}/turn_off", {"entity_id": eid}))
            return _say_or_blank(maybe_say, f"Turning off {raw}.") if ok else None
        if os.environ.get("PIPHONE_LIVE") != "1":
            return f"CLAIM: on_off_controls — no match for '{raw}'"
        return None

    m_bare_on = re.fullmatch(r"(?:the\s+)?(.+?)\s+on\b", t)
    if m_bare_on and not re.search(r"\bturn\s+on\b", t):
        raw = m_bare_on.group(1).strip()

        # Explicit room-wide lights via mapped HA area_id.
        explicit_area_id = _extract_explicit_room_lights_target(raw)
        if explicit_area_id:
            ok = _is_ok(call_ha_service("light/turn_on", {"area_id": explicit_area_id}))
            return _say_or_blank(maybe_say, "Okay.") if ok else None

        # Narrow room-local generic light control via current request context.
        if _norm_target(raw) in ("light", "lights"):
            area_id = get_area_id_for_current_request()
            if area_id:
                ok = _is_ok(call_ha_service("light/turn_on", {"area_id": area_id}))
                return _say_or_blank(maybe_say, "Okay.") if ok else None

        key = _norm_target(raw)
        forced = (TURN_ON_PHRASE_OVERRIDES or {}).get(key)
        if forced:
            ok = _run_runnable_entity(forced, call_ha_service=call_ha_service)
            return _say_or_blank(maybe_say, "Okay.") if ok else None

        resolved = resolve_device_entity(raw)
        if resolved:
            eid, domain = resolved
            ok = _is_ok(call_ha_service(f"{domain}/turn_on", {"entity_id": eid}))
            return _say_or_blank(maybe_say, f"Turning on {raw}.") if ok else None
        if os.environ.get("PIPHONE_LIVE") != "1":
            return f"CLAIM: on_off_controls — no match for '{raw}'"
        return None


    return None


# Domains that support a native HA `<domain>/toggle` service. Atomic on HA's
# side — no need for PiPhone to read state first, which would also create a
# read/write race.
_TOGGLEABLE_DOMAINS = {
    "light", "switch", "fan", "media_player", "cover",
    "input_boolean", "automation", "script", "siren", "humidifier",
    "climate",
}


def handle_toggle_controls(
    *,
    tl: str,
    call_ha_service,
    maybe_say,
    resolve_device_entity,
) -> Optional[str]:
    """Toggle one verified target when Home Assistant exposes toggle semantics."""
    """
    Handles "toggle <thing>" — flips the entity to the opposite state via
    HA's native `<domain>/toggle` service. Atomic; no state read needed.

    Supported domains: lights, switches, fans, media_players, covers,
    input_booleans, automations, scripts, sirens, humidifiers, climate.
    Domains without a toggle service (e.g., locks) fall through unhandled.
    """
    t = (tl or "").strip().lower()

    m = re.search(r"\btoggle (?:the )?(.+)\b", t)
    if not m:
        return None

    raw = m.group(1).strip()

    # Explicit room-wide lights via mapped HA area_id — "toggle living room lights".
    explicit_area_id = _extract_explicit_room_lights_target(raw)
    if explicit_area_id:
        ok = _is_ok(call_ha_service("light/toggle", {"area_id": explicit_area_id}))
        return _say_or_blank(maybe_say, "Okay.") if ok else None

    # Narrow room-local "toggle the light(s)" via current request context.
    if _norm_target(raw) in ("light", "lights"):
        area_id = get_area_id_for_current_request()
        if area_id:
            ok = _is_ok(call_ha_service("light/toggle", {"area_id": area_id}))
            return _say_or_blank(maybe_say, "Okay.") if ok else None

    resolved = resolve_device_entity(raw)
    if not resolved:
        if os.environ.get("PIPHONE_LIVE") != "1":
            return f"CLAIM: toggle_controls — no match for '{raw}'"
        return None

    eid, domain = resolved
    if domain not in _TOGGLEABLE_DOMAINS:
        if os.environ.get("PIPHONE_LIVE") != "1":
            return f"CLAIM: toggle_controls — domain '{domain}' has no toggle service ({eid})"
        return None

    ok = _is_ok(call_ha_service(f"{domain}/toggle", {"entity_id": eid}))
    return _say_or_blank(maybe_say, f"Toggling {raw}.") if ok else None
