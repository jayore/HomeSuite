"""room_lights_controls.py — generic room "lights" level/color commands.

Handles commands that target a room's *real* light devices (via HA area_id),
as opposed to the virtual brightness/color helper entities that
`brightness_controls` / `color_controls` drive.

Scope (claims only these):
    * "lights 50" / "lights to 50%" / "set the lights to 50 percent"   -> brightness
    * "lights blue" / "set the lights to red" / "make the lights green"  -> color
    * optionally with an explicit room prefix: "kitchen lights blue"

Deliberately does NOT claim:
    * "lights off" / "lights on"        -> on_off_controls (area turn_on/off)
    * "set brightness to X" / "color X" -> virtual brightness/color helpers
    * "stair light blue" (named light)  -> color_controls per-light handling
    * "dim the lights" / "brighter"     -> relative brightness (no explicit level)

Routing requires a resolvable room area_id (scoped room focus or an explicit
room name); otherwise it returns None and lets normal handling continue.
"""

import re
from typing import Optional, Tuple

from request_context import get_area_id_for_current_request
from color_resolver import is_known_css_color
from kelvin_controls import NAMED_TEMPS
from on_off_controls import (
    _extract_explicit_room_lights_target,
    _is_ok,
    _say_or_blank,
)

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


def _resolve_area(target: str) -> Optional[str]:
    """Return the HA area_id for a 'lights' target.

    Bare generic lights ("lights" / "the lights") -> the current request's
    scoped room area. "<room> lights" -> that room's area.
    """
    s = re.sub(r"^the\s+", "", (target or "").strip(), flags=re.IGNORECASE).strip()
    if s in ("light", "lights"):
        return get_area_id_for_current_request()
    return _extract_explicit_room_lights_target(s)


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

    area_id = _resolve_area(m.group("target"))
    if not area_id:
        return None

    kind, val = parsed
    if kind == "level":
        if val == 0:
            ok = _is_ok(call_ha_service("light/turn_off", {"area_id": area_id}))
        else:
            ok = _is_ok(call_ha_service(
                "light/turn_on", {"area_id": area_id, "brightness_pct": val}
            ))
    elif kind == "kelvin":
        ok = _is_ok(call_ha_service(
            "light/turn_on", {"area_id": area_id, "color_temp_kelvin": val}
        ))
    else:  # color
        ok = _is_ok(call_ha_service(
            "light/turn_on", {"area_id": area_id, "color_name": val}
        ))

    return _say_or_blank(maybe_say, "Okay.") if ok else None
