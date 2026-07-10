"""Resolve named colors and apply them to one or more verified lights.

This handler owns named-light color phrases and configured color helper
entities. Room-area color phrases are handled by ``room_lights_controls``;
Kelvin and RGB/hex formats have dedicated handlers. Unknown color words or
unresolved devices fall through without issuing a Home Assistant call.
"""

import re
from typing import Callable, Optional

from multi_target_utils import split_targets
from request_context import get_room_default_for_request
from color_resolver import is_known_css_color


# Conservative color name whitelist for "relaxed" phrasing (no 'set' / no 'to').
# If you want to support more, add here (single-word only).
_COMMON_COLOR_NAMES = {
    "red","blue","green","orange","yellow","purple","pink","white","black",
    "cyan","magenta","teal","lime","amber","violet","indigo","gold","turquoise",
}

def _is_probable_color_name(w: str) -> bool:
    w = (w or "").strip().lower()
    return bool(w) and w in _COMMON_COLOR_NAMES


def handle_color_controls(
    *,
    tl: str,
    call_ha_service,
    maybe_say,
    resolve_light_target,
    remember_light,
    color_lights: dict,
    default_color_room: str,
    resolve_color: Optional[Callable[[str], Optional[str]]] = None,
) -> Optional[str]:
    """Apply a recognized named color to resolved light targets."""
    """
    Handles all color-related commands.
    Returns:
      - string (possibly empty) if handled
      - None if not a color command
    """

    def _maybe_resolve(color: str) -> str:
        """If color is not a known CSS name and a resolver is available, ask AI."""
        if resolve_color and not is_known_css_color(color):
            resolved = resolve_color(color)
            if resolved:
                return resolved
        return color

    # --------------------------------------------------
    # 1) Explicit room color
    # "set living room color to red"
    # --------------------------------------------------
    m_room = re.search(r"\bset\s+(.+?)\s+color\s+to\s+([a-z]+)\b", tl)
    if m_room:
        room = m_room.group(1).strip()
        room = re.sub(r"(?:’s|’s)$", "", room).strip()
        color = _maybe_resolve(m_room.group(2))

        rooms = split_targets(room)
        any_ok = False
        last_eid = None

        for r in rooms:
            room_key = (r or "").strip().lower()
            eid = color_lights.get(room_key)
            if not eid:
                continue
            if call_ha_service("light/turn_on", {"entity_id": eid, "color_name": color}):
                remember_light(eid)
                any_ok = True
                last_eid = eid

        if any_ok:
            # Keep confirmations minimal; most users rely on light feedback anyway.
            if len(rooms) == 1:
                return maybe_say(f"Setting {room} color to {color}.")
            return maybe_say("Okay.")
        # If not a known room key, fall through to per-light handlers.
        # (e.g., "set stair light color to red")


    
    # --------------------------------------------------
    # 1b) Explicit per-light color (supports multi-target)
    # "set stair light color to blue"
    # "set stair light and side lamp's color to blue"
    # --------------------------------------------------
    m_light_color_to = re.search(
        r"\bset\s+(?:the\s+)?(.+?)\s+colors?\s+to\s+([a-z]+)\b",
        tl,
    )
    if m_light_color_to:
        raw = m_light_color_to.group(1).strip()
        color = m_light_color_to.group(2)

        # Avoid stealing brightness / numbers / on-off
        if color.isdigit() or color in ("on", "off"):
            return None
        color = _maybe_resolve(color)

        targets = split_targets(raw)
        any_ok = False
        used_ctx_any = False

        for t in targets:
            t = re.sub(r"(?:'s|’s)$", "", (t or "").strip()).strip()
            # If this target is a room name we recognize, use the room-mapped color light.
            t_key = (t or "").strip().lower()
            eid = color_lights.get(t_key)
            used_ctx = False
            if not eid:
                eid, used_ctx = resolve_light_target(t)
            if not eid:
                continue
            if call_ha_service("light/turn_on", {"entity_id": eid, "color_name": color}):
                remember_light(eid)
                any_ok = True
                used_ctx_any = used_ctx_any or bool(used_ctx)

        if any_ok:
            if len(targets) == 1:
                return maybe_say(
                    f"Setting it to {color}."
                    if used_ctx_any
                    else f"Setting {raw} to {color}."
                )
            return maybe_say("Okay.")
        return None

    # --------------------------------------------------
    # 2) Global color
    # "set color to red"
    # "color red"
    # --------------------------------------------------
    m_global = (
        re.search(r"\bset\s+color\s+to\s+([a-z]+)\b", tl)
        or re.search(r"^\s*color\s+(?!to\b)([a-z]+)\b", tl)
    )
    if m_global:
        color = _maybe_resolve(m_global.group(1))

        # First, try a request-local room default from the room registry.
        eid = get_room_default_for_request("color_light", fallback=None)

        # Fall back to the legacy global default behavior if no request-local
        # room color default applies.
        if not eid:
            eid = color_lights.get(default_color_room)

        if eid:
            if call_ha_service("light/turn_on", {"entity_id": eid, "color_name": color}):
                remember_light(eid)
                return maybe_say(f"Setting color to {color}.")
        return None


    # --------------------------------------------------
    # 2b) Relaxed per-light color (more forgiving phrasing)
    #
    # Supports:
    #   "set stair light blue"
    #   "stair light blue"
    #   "stair light to blue"
    #
    # Safety:
    #   - only fires for whitelisted common color words
    #   - requires target to look light-ish (contains 'light'/'lamp') or be a known room key
    # --------------------------------------------------

    # Helper: apply color to one-or-more targets, sharing behavior with other handlers.
    def _apply_color_to_targets(raw_target: str, color: str) -> Optional[str]:
        if (not raw_target) or (not color):
            return None
        if color.isdigit() or color in ("on", "off"):
            return None
        if not _is_probable_color_name(color):
            return None

        raw_target = raw_target.strip()
        # Avoid accidental captures like "set color orange" (global color is handled elsewhere)
        if raw_target.lower() in ("color", "colors"):
            return None

        targets = split_targets(raw_target)
        any_ok = False
        used_ctx_any = False

        for t in targets:
            t = re.sub(r"(?:'s|’s)$", "", (t or "").strip()).strip()
            if not t:
                continue

            # If this target is a room name we recognize, use the room-mapped color light.
            t_key = (t or "").strip().lower()
            eid = color_lights.get(t_key)
            used_ctx = False
            if not eid:
                eid, used_ctx = resolve_light_target(t)
            if not eid:
                continue

            if call_ha_service("light/turn_on", {"entity_id": eid, "color_name": color}):
                remember_light(eid)
                any_ok = True
                used_ctx_any = used_ctx_any or bool(used_ctx)

        if any_ok:
            if len(targets) == 1:
                return maybe_say(
                    f"Setting it to {color}."
                    if used_ctx_any
                    else f"Setting {raw_target} to {color}."
                )
            return maybe_say("Okay.")
        return None

    def _apply_explicit_color_to_targets(raw_target: str, color: str) -> Optional[str]:
        if (not raw_target) or (not color):
            return None
        if color.isdigit() or color in ("on", "off"):
            return None

        raw_target = raw_target.strip()
        color = _maybe_resolve(color.strip().lower())

        targets = split_targets(raw_target)
        any_ok = False
        used_ctx_any = False

        for t in targets:
            t = re.sub(r"(?:'s|’s)$", "", (t or "").strip()).strip()
            if not t:
                continue

            t_key = (t or "").strip().lower()
            eid = color_lights.get(t_key)
            used_ctx = False
            if not eid:
                eid, used_ctx = resolve_light_target(t)
            if not eid:
                continue

            if call_ha_service("light/turn_on", {"entity_id": eid, "color_name": color}):
                remember_light(eid)
                any_ok = True
                used_ctx_any = used_ctx_any or bool(used_ctx)

        if any_ok:
            if len(targets) == 1:
                return maybe_say(
                    f"Setting it to {color}."
                    if used_ctx_any
                    else f"Setting {raw_target} to {color}."
                )
            return maybe_say("Okay.")
        return None

    # A) "set <target> to <color>"
    # Keep this before relaxed "set <target> <color>" so "to" never becomes
    # part of the target phrase.
    m_explicit_to = re.search(
        r"\bset\s+(?:the\s+)?([a-zA-Z0-9 \-']+?)\s+to\s+([a-z]+)\b$",
        tl,
    )
    if m_explicit_to:
        raw_target = m_explicit_to.group(1).strip()
        color = m_explicit_to.group(2).strip().lower()
        out = _apply_explicit_color_to_targets(raw_target, color)
        if out is not None:
            return out

    # B) "set <target> <color>"  (no "to")
    m_relaxed_set = re.search(r"\bset\s+(?:the\s+)?(.+?)\s+([a-z]+)\b$", tl)
    if m_relaxed_set:
        raw_target = m_relaxed_set.group(1).strip()
        color = m_relaxed_set.group(2).strip().lower()

        # Only accept if target looks light-ish OR is a known room key
        looks_lightish = bool(re.search(r"\b(light|lamp)\b", raw_target))
        is_known_room = (raw_target.strip().lower() in color_lights)

        if looks_lightish or is_known_room:
            out = _apply_color_to_targets(raw_target, color)
            if out is not None:
                return out

    # C) "<target> to <color>"  (no "set")
    m_relaxed_to = re.search(r"\b(.+?)\s+to\s+([a-z]+)\b$", tl)
    if m_relaxed_to:
        raw_target = m_relaxed_to.group(1).strip()
        color = m_relaxed_to.group(2).strip().lower()

        looks_lightish = bool(re.search(r"\b(light|lamp)\b", raw_target))
        is_known_room = (raw_target.strip().lower() in color_lights)

        if looks_lightish or is_known_room:
            out = _apply_color_to_targets(raw_target, color)
            if out is not None:
                return out

    # D) "<target> <color>"  (no "set", no "to") — most permissive, so keep it strict
    m_relaxed_bare = re.search(r"\b(.+?)\s+([a-z]+)\b$", tl)
    if m_relaxed_bare:
        raw_target = m_relaxed_bare.group(1).strip()
        color = m_relaxed_bare.group(2).strip().lower()

        looks_lightish = bool(re.search(r"\b(light|lamp)\b", raw_target))
        is_known_room = (raw_target.strip().lower() in color_lights)

        # Very strict: require "light/lamp" OR known room key (don’t try to resolve arbitrary phrases).
        if looks_lightish or is_known_room:
            out = _apply_color_to_targets(raw_target, color)
            if out is not None:
                return out

    # --------------------------------------------------
    # 2c) AI phrase color: "set <target> to [the] color [of] <description>"
    # Handles multi-word descriptions like "the color of the ocean",
    # "the color of barney the dinosaur", etc.
    # Only fires when resolve_color is available; falls through otherwise.
    # --------------------------------------------------
    m_color_phrase = re.search(
        r"\bset\s+(?:the\s+)?(.+?)\s+to\s+(?:the\s+)?color\s+(?:of\s+(?:the\s+)?)?(.+)$",
        tl,
    )
    if m_color_phrase and resolve_color:
        raw = m_color_phrase.group(1).strip()
        description = m_color_phrase.group(2).strip()
        color = resolve_color(description)
        if color:
            targets = split_targets(raw)
            any_ok = False
            used_ctx_any = False
            for t in targets:
                t = re.sub(r"(?:'s|'s)$", "", (t or "").strip()).strip()
                t_key = (t or "").strip().lower()
                eid = color_lights.get(t_key)
                used_ctx = False
                if not eid:
                    eid, used_ctx = resolve_light_target(t)
                if not eid:
                    continue
                if call_ha_service("light/turn_on", {"entity_id": eid, "color_name": color}):
                    remember_light(eid)
                    any_ok = True
                    used_ctx_any = used_ctx_any or bool(used_ctx)
            if any_ok:
                if len(targets) == 1:
                    return maybe_say(
                        f"Setting it to {color}."
                        if used_ctx_any
                        else f"Setting {raw} to {color}."
                    )
                return maybe_say("Okay.")
        # Resolver returned None or no targets matched — fall through to m_single.

    # --------------------------------------------------
    # 3) Individual light color
    # "set stair light to blue"
    # --------------------------------------------------
    m_single = re.search(
        r"\bset\s+(?:the\s+)?([a-zA-Z0-9 \-']+?)\s+to\s+([a-z]+)\b",
        tl,
    )
    if m_single:
        raw = m_single.group(1).strip()
        color = m_single.group(2)
        return _apply_explicit_color_to_targets(raw, color)

    return None
