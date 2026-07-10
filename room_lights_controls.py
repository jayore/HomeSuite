"""Handle generic room-wide light level, color, and temperature commands.

Brightness levels use the room's configured brightness strategy, so phrasing
does not decide whether Home Suite controls a proxy, HA area, or explicit light
list. Color and temperature continue to target the room's HA area directly.

Scope (claims only these):
    * "lights 50" / "lights to 50%" / "set the lights to 50 percent"   -> brightness
    * "lights blue" / "set the lights to red" / "make the lights green"  -> color
    * optionally with an explicit room prefix: "kitchen lights blue"

Deliberately does NOT claim:
    * "lights off" / "lights on"        -> on_off_controls (area turn_on/off)
    * "set brightness to X" / "color X" -> dedicated handlers
    * "stair light blue" (named light)  -> color_controls per-light handling
    * "dim the lights" / "brighter"     -> relative brightness (no explicit level)

Routing requires a resolvable room area_id (scoped room focus or an explicit
room name); otherwise it returns None and lets normal handling continue.
"""

import re
from typing import Optional, Tuple

from color_resolver import is_known_css_color
from home_registry import get_room
from kelvin_controls import NAMED_TEMPS
from on_off_controls import (
    _is_ok,
    _say_or_blank,
)
from room_brightness import apply_room_brightness, resolve_room_id

# Single-word color names accepted for relaxed phrasing. All are valid CSS3
# color names (HA's light.turn_on color_name accepts CSS3 names).
_COMMON_COLOR_NAMES = {
    "red", "blue", "green", "orange", "yellow", "purple", "pink", "white",
    "cyan", "magenta", "teal", "lime", "amber", "violet", "indigo", "gold",
    "turquoise", "maroon", "navy", "olive", "salmon", "coral", "crimson",
}

# Verb prefixes we tolerate ahead of the "lights" target. "dim"/"brighten" are
# intentionally excluded so relative brightness still flows to brightness_controls.
_LIGHTS_CMD = re.compile(
    r"^(?:please\s+)?(?:set|turn|make|change|put|adjust)?\s*"
    r"(?P<target>(?:[a-z][a-z' ]*?\s+)?lights?)\s+"
    r"(?:to\s+|at\s+|=\s*)?(?P<value>.+?)\s*$",
    re.IGNORECASE,
)

_LEVEL_RE = re.compile(r"^(\d{1,3})\s*(?:%|percent)?$", re.IGNORECASE)
_KELVIN_RE = re.compile(r"^(\d{4,5})\s*k$", re.IGNORECASE)


def _resolve_room(target: str) -> Optional[str]:
    """Return the room id for a generic or explicit 'lights' target.

    Bare generic lights ("lights" / "the lights") -> the current request's
    scoped room area. "<room> lights" -> that room's area.
    """
    s = re.sub(r"^the\s+", "", (target or "").strip(), flags=re.IGNORECASE).strip()
    if s in ("light", "lights"):
        return resolve_room_id()
    room_phrase = re.sub(r"\s+lights?$", "", s, flags=re.IGNORECASE).strip()
    return resolve_room_id(room_phrase)


def _parse_value(value: str) -> Optional[Tuple[str, object]]:
    """Classify the value as ("level", int), ("kelvin", K), or ("color", name).

    Returns None for on/off and unknown words so the command falls through.
    """
    v = (value or "").strip().lower().rstrip(".")
    if not v or v in ("on", "off"):
        return None

    m = _LEVEL_RE.match(v)
    if m:
        n = int(m.group(1))
        if 0 <= n <= 100:
            return ("level", n)
        return None

    # Numeric color temperature: "3000k".
    mk = _KELVIN_RE.match(v)
    if mk:
        return ("kelvin", max(1500, min(9000, int(mk.group(1)))))

    # Named color temperatures ("warm white", "soft white", "daylight", ...).
    # Checked before plain color so "warm white" maps to a temperature rather
    # than letting the trailing "white" become a flat white color_name.
    if v in NAMED_TEMPS:
        return ("kelvin", NAMED_TEMPS[v])

    if is_known_css_color(v) or v in _COMMON_COLOR_NAMES:
        return ("color", v)

    return None


def handle_room_lights_controls(
    *,
    tl: str,
    call_ha_service,
    maybe_say,
    remember_light=None,
) -> Optional[str]:
    t = (tl or "").strip().lower()
    if "light" not in t:
        return None

    m = _LIGHTS_CMD.match(t)
    if not m:
        return None

    parsed = _parse_value(m.group("value"))
    if not parsed:
        return None

    room_id = _resolve_room(m.group("target"))
    if not room_id:
        return None

    kind, val = parsed
    if kind == "level":
        ok = apply_room_brightness(
            room_id,
            val,
            call_ha_service=call_ha_service,
            remember_light=remember_light,
        )
    else:
        room = get_room(room_id) or {}
        area_id = str(room.get("ha_area_id") or "").strip()
        if not area_id:
            return None

    if kind == "kelvin":
        ok = _is_ok(call_ha_service(
            "light/turn_on", {"area_id": area_id, "color_temp_kelvin": val}
        ))
    elif kind == "color":
        ok = _is_ok(call_ha_service(
            "light/turn_on", {"area_id": area_id, "color_name": val}
        ))

    return _say_or_blank(maybe_say, "Okay.") if ok else None
