"""Handle explicit RGB tuples and hexadecimal light-color commands.

This narrow parser converts unambiguous numeric color notation and applies it
only after resolving a real light entity. Named colors and white temperature
phrases intentionally fall through to their dedicated handlers.
"""

import re
from typing import Optional

from multi_target_utils import split_targets


def handle_rgb_hex_controls(
    *,
    tl: str,
    call_ha_service,
    maybe_say,
    resolve_light_target,
    remember_light,
    try_light_turn_on,
) -> Optional[str]:
    """Parse RGB/hex notation and update a resolved light entity."""
    """
    Handles explicit RGB and HEX color commands.

    Examples:
      - "set lamp to #ff00aa"
      - "set lamp to rgb 255 0 170"
    """

    # --------------------------------------------------
    # HEX color: "#ff00aa"
    # --------------------------------------------------
    hex_match = re.search(
        r"\b(?:set\s+(?:the\s+)?)?([a-zA-Z0-9 \-']+?)\s+(?:to\s+)?#?([0-9a-f]{6})\b",
        tl,
    )
    if hex_match:
        raw = hex_match.group(1).strip()
        targets = split_targets(raw)
        resolved = []
        for target in targets:
            eid, used_ctx = resolve_light_target(target)
            if not eid:
                return None
            resolved.append((eid, used_ctx))

        hx = hex_match.group(2)
        r = int(hx[0:2], 16)
        g = int(hx[2:4], 16)
        b = int(hx[4:6], 16)

        for eid, _used_ctx in resolved:
            success = try_light_turn_on(
                eid,
                [{"rgb_color": [r, g, b]}],
            )
            if not success:
                return None
            remember_light(eid)

        if len(resolved) > 1:
            return maybe_say("Okay.")
        return maybe_say(
            "Setting it."
            if resolved[0][1]
            else f"Setting {raw} color."
        )

    # --------------------------------------------------
    # Explicit RGB: "rgb 255 0 170"
    # --------------------------------------------------
    rgb_match = re.search(
        r"\b(?:set\s+(?:the\s+)?)?([a-zA-Z0-9 \-']+?)\s+(?:to\s+)?rgb[\s\(]+(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})\)?",
        tl,
    )
    if rgb_match:
        raw = rgb_match.group(1).strip()
        targets = split_targets(raw)
        resolved = []
        for target in targets:
            eid, used_ctx = resolve_light_target(target)
            if not eid:
                return None
            resolved.append((eid, used_ctx))

        r = max(0, min(255, int(rgb_match.group(2))))
        g = max(0, min(255, int(rgb_match.group(3))))
        b = max(0, min(255, int(rgb_match.group(4))))

        for eid, _used_ctx in resolved:
            success = try_light_turn_on(
                eid,
                [{"rgb_color": [r, g, b]}],
            )
            if not success:
                return None
            remember_light(eid)

        if len(resolved) > 1:
            return maybe_say("Okay.")
        return maybe_say(
            "Setting it."
            if resolved[0][1]
            else f"Setting {raw} color."
        )

    return None
