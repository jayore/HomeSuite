import re
from typing import Optional


# --------------------------------------------------
# Named color temperatures
# --------------------------------------------------
# These phrases are kelvin values expressed by name. Recognizing them in
# the kelvin handler — which runs before color_controls — prevents the
# relaxed color regex from grabbing just the trailing 'white' and
# applying a flat white color, ignoring the temperature modifier.
NAMED_TEMPS = {
    "warm white":    2700,
    "soft white":    3000,
    "neutral white": 4000,
    "cool white":    5000,
    "daylight":      5500,
    "incandescent":  2700,
    "candlelight":   2200,
    "candle light":  2200,
}

# Sort longest first so multi-word phrases match before any future
# single-word alias (e.g. 'soft white' beats a hypothetical 'soft').
_NAMED_TEMP_ALT = "|".join(
    re.escape(k) for k in sorted(NAMED_TEMPS.keys(), key=len, reverse=True)
)


def handle_kelvin_controls(
    *,
    tl: str,
    call_ha_service,
    maybe_say,
    resolve_light_target,
    remember_light,
    try_light_turn_on,
) -> Optional[str]:
    """
    Handles Kelvin / color temperature commands.

    Examples:
      - "set lamp to 3000k"
      - "set living room lamp to 4500 k"
      - "now 2700k"

    Returns:
      - string (possibly empty) if handled
      - None if not a kelvin command
    """

    # --------------------------------------------------
    # Kelvin: "set X to 3000k"
    # --------------------------------------------------
    kelvin_match = re.search(
        r"\b(?:set\s+(?:the\s+)?)?([a-zA-Z0-9 \-']+?)\s+(?:to\s+)?(\d{4,5})\s*k\b",
        tl,
    )
    if kelvin_match:
        raw = kelvin_match.group(1).strip()
        # If user says "now 3000k", skip this targeted block so the
        # contextual handler below catches it. Must NOT 'return None' here —
        # that would exit the function entirely before m_now runs.
        if raw.lower() not in ("now",):
            eid, used_ctx = resolve_light_target(raw)
            if eid:
                kelvin = int(kelvin_match.group(2))
                kelvin = max(1500, min(9000, kelvin))
                mired = int(round(1_000_000 / kelvin))

                success = try_light_turn_on(
                    eid,
                    [
                        {"color_temp_kelvin": kelvin},
                        {"color_temp": mired},
                        {"kelvin": kelvin},
                    ],
                )
                if success:
                    remember_light(eid)
                    return maybe_say(
                        f"Setting it to {kelvin}K."
                        if used_ctx
                        else f"Setting {raw} to {kelvin}K."
                    )
                return None

    # --------------------------------------------------
    # Contextual shorthand: "now 3000k"
    # --------------------------------------------------
    m_now = re.fullmatch(r"(?:now\s+)?(\d{4,5})\s*k\s*", tl)
    if m_now:
        eid = resolve_light_target("it")[0]
        if not eid:
            return None

        kelvin = int(m_now.group(1))
        kelvin = max(1500, min(9000, kelvin))
        mired = int(round(1_000_000 / kelvin))

        success = try_light_turn_on(
            eid,
            [
                {"color_temp_kelvin": kelvin},
                {"color_temp": mired},
                {"kelvin": kelvin},
            ],
        )
        if success:
            remember_light(eid)
            return maybe_say(f"Setting it to {kelvin}K.")
        return None

    # --------------------------------------------------
    # Named color temperatures (targeted):
    #   "set side lamp to warm white"
    #   "set the kitchen lights cool white"
    #   "side lamp warm white"
    # --------------------------------------------------
    named_match = re.search(
        rf"\b(?:set\s+(?:the\s+)?)?([a-zA-Z0-9 \-']+?)\s+(?:to\s+)?({_NAMED_TEMP_ALT})\b",
        tl,
    )
    if named_match:
        raw = named_match.group(1).strip()
        name = named_match.group(2).strip()

        # Let the contextual "now <name>" path below own its case — skip the
        # targeted block without exiting the function.
        if raw.lower() not in ("now",):
            eid, used_ctx = resolve_light_target(raw)
            if not eid:
                # Pattern matched — intent is clear. Don't fall through to
                # other handlers (color_controls would mis-grab 'white').
                return ""

            kelvin = NAMED_TEMPS[name]
            mired = int(round(1_000_000 / kelvin))

            success = try_light_turn_on(
                eid,
                [
                    {"color_temp_kelvin": kelvin},
                    {"color_temp": mired},
                    {"kelvin": kelvin},
                ],
            )
            if success:
                remember_light(eid)
                return maybe_say(
                    f"Setting it to {name}."
                    if used_ctx
                    else f"Setting {raw} to {name}."
                )
            return ""

    # --------------------------------------------------
    # Named color temperatures (contextual):
    #   "warm white"
    #   "now cool white"
    # --------------------------------------------------
    m_named_now = re.fullmatch(rf"(?:now\s+)?({_NAMED_TEMP_ALT})\s*", tl)
    if m_named_now:
        eid = resolve_light_target("it")[0]
        if not eid:
            return None
        name = m_named_now.group(1).strip()
        kelvin = NAMED_TEMPS[name]
        mired = int(round(1_000_000 / kelvin))

        success = try_light_turn_on(
            eid,
            [
                {"color_temp_kelvin": kelvin},
                {"color_temp": mired},
                {"kelvin": kelvin},
            ],
        )
        if success:
            remember_light(eid)
            return maybe_say(f"Setting it to {name}.")
        return None

    return None
