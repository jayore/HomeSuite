"""Handle absolute and relative brightness for verified light targets.

The parser supports percentages, spoken numbers, dim/brighten adjustments, and
multiple targets. Generic room-wide "lights 50" forms are intentionally left to
``room_lights_controls``; this module handles configured helper entities and
resolved named lights. Service calls occur only after target resolution.
"""

import re
from typing import Optional

from multi_target_utils import split_targets
from room_brightness import (
    apply_room_brightness,
    apply_room_brightness_step,
    get_room_brightness_target,
    resolve_room_id,
)


def handle_brightness_controls(
    *,
    tl: str,
    states_snapshot,
    call_ha_service,
    maybe_say,
    resolve_light_target,
    remember_light,
    get_recent_light,
    entity_exists,
    set_number_value,
    default_brightness_number: str,
    brightness_numbers: dict,
    light_phrase_overrides: dict,
) -> Optional[str]:
    """Parse brightness language and update one or more resolved targets."""
    """
    Handles all brightness-related commands.
    Returns:
      - string (possibly empty) if handled
      - None if not a brightness command
    """

    # --------------------------------------------------
    # 0) Relative brightness: "brighter", "dimmer", "brightness up/down", etc.
    # --------------------------------------------------
    _STEP_PCT = 10  # percentage points per relative step

    _up_pat = re.search(
        r"\b(?:"
        r"bright(?:er|en(?:\s+up)?)|"
        r"(?:more|get)\s+bright(?:er)?|"
        r"make\s+(?:it\s+|the\s+\S+\s+)?bright(?:er)?|"
        r"(?:turn|crank)\s+(?:(?:it|the\s+\S+)\s+)?up\s+(?:the\s+)?bright(?:ness)?|"
        r"brightness\s+up|"
        r"increase\s+(?:the\s+)?bright(?:ness)?|"
        r"up\s+(?:the\s+)?bright(?:ness)?"
        r")\b",
        tl,
    )
    _dn_pat = re.search(
        r"\b(?:"
        r"dim(?:mer|mer\s+down)?|dark(?:er)?\b|"
        r"(?:less|more)\s+dim(?:mer)?|"
        r"less\s+bright(?:er)?|"
        r"(?:more|get)\s+dim(?:mer)?|"
        r"make\s+(?:it\s+|the\s+\S+\s+)?dim(?:mer)?|"
        r"(?:turn|crank)\s+(?:(?:it|the\s+\S+)\s+)?down\s+(?:the\s+)?bright(?:ness)?|"
        r"brightness\s+down|"
        r"decrease\s+(?:the\s+)?bright(?:ness)?|"
        r"down\s+(?:the\s+)?bright(?:ness)?|"
        r"lower\s+(?:the\s+)?bright(?:ness)?"
        r")\b",
        tl,
    )

    if _up_pat or _dn_pat:
        # Check for "by N" override, e.g. "increase brightness by 20"
        _by_m = re.search(r"\bby\s+(\d{1,3})\b", tl)
        _by_amt = max(1, min(100, int(_by_m.group(1)))) if _by_m else None
        step = (_by_amt if _by_amt is not None else _STEP_PCT) * (1 if _up_pat else -1)

        # Check for an explicit target in the phrase, e.g. "make the kitchen brighter"
        # Require "the <target>" pattern to avoid capturing verb phrases like "make it".
        _target_m = re.search(
            r"\bthe\s+([a-zA-Z0-9][a-zA-Z0-9 \-']*?)\s+(?:brighter|dimmer|brightness)\b", tl
        )
        _explicit_target = (_target_m.group(1).strip() if _target_m else None)

        resolved_eid = None
        used_ctx = False

        _NON_TARGETS = {"it", "the", "a", "that", "this", "light", "lights"}
        if _explicit_target and _explicit_target not in _NON_TARGETS:
            room_id = resolve_room_id(_explicit_target)
            if room_id and get_room_brightness_target(room_id):
                if apply_room_brightness_step(
                    room_id,
                    step,
                    call_ha_service=call_ha_service,
                    states_snapshot=states_snapshot,
                    remember_light=remember_light,
                ):
                    direction = "brighter" if step > 0 else "dimmer"
                    return maybe_say(f"Making it {direction}.")
                return None
            resolved_eid, used_ctx = resolve_light_target(_explicit_target)

        if not resolved_eid:
            # Fall back: recent light → room default → hard default (living room brightness)
            resolved_eid = get_recent_light()
            if resolved_eid:
                used_ctx = True
            else:
                room_target = get_room_brightness_target()
                if room_target:
                    if apply_room_brightness_step(
                        room_target["room_id"],
                        step,
                        call_ha_service=call_ha_service,
                        states_snapshot=states_snapshot,
                        remember_light=remember_light,
                    ):
                        direction = "brighter" if step > 0 else "dimmer"
                        return maybe_say(f"Making it {direction}.")
                    return None
                resolved_eid = light_phrase_overrides.get("living room brightness")

        if resolved_eid:
            if call_ha_service(
                "light/turn_on",
                {"entity_id": resolved_eid, "brightness_step_pct": step},
            ):
                remember_light(resolved_eid)
                direction = "brighter" if step > 0 else "dimmer"
                return maybe_say(f"Making it {direction}.")
        return None

    # Avoid stealing color / kelvin / rgb / hex commands
    if (
        re.search(r"\bto\s+\d{4,5}\s*k\b", tl)
        or re.search(r"\bto\s+#?[0-9a-f]{6}\b", tl)
        or re.search(r"\bto\s+rgb\b", tl)
        or re.search(r"\bcolor\b", tl)
    ):
        return None
    # Avoid stealing volume commands
    if re.search(r"\bvolume\b", tl):
        return None

    # --------------------------------------------------
    # 1) Explicit "<room> brightness to N"
    # --------------------------------------------------
    m_explicit = re.search(
        r"\bset\s+(?:the\s+)?([a-zA-Z0-9 \-']+?)\s+brightness(?:es)?\s+(?:to\s+)?(\d{1,3})\s*%?\b",
        tl,
    )
    if m_explicit:
        raw = m_explicit.group(1).strip()
        val = max(0, min(100, int(m_explicit.group(2))))

        targets = split_targets(raw)
        any_ok = False
        used_ctx_any = False

        for t in targets:
            # PIPHONE_POSSESSIVE_STRIP
            t = re.sub(r"(?:'s|’s)$", "", (t or "").strip()).strip()
            room_id = resolve_room_id(t)
            if room_id and get_room_brightness_target(room_id):
                if apply_room_brightness(
                    room_id,
                    val,
                    call_ha_service=call_ha_service,
                    remember_light=remember_light,
                ):
                    any_ok = True
                continue
            phrase_key = f"{t} brightness".lower()
            if phrase_key in light_phrase_overrides:
                eid = light_phrase_overrides[phrase_key]
                if call_ha_service("light/turn_on", {"entity_id": eid, "brightness_pct": val}):
                    remember_light(eid)
                    any_ok = True
                    continue

            eid, used_ctx = resolve_light_target(t)
            if eid:
                if call_ha_service("light/turn_on", {"entity_id": eid, "brightness_pct": val}):
                    remember_light(eid)
                    any_ok = True
                    used_ctx_any = used_ctx_any or bool(used_ctx)

        if any_ok:
            if len(targets) == 1:
                return maybe_say(
                    f"Setting it to {val} percent."
                    if used_ctx_any
                    else f"Setting {raw} to {val} percent."
                )
            return maybe_say("Okay.")
        return None

    # --------------------------------------------------
    # 2) Global: "brightness 50" / "set brightness to 50"
    # --------------------------------------------------
    m_global = (
        re.search(r"\bset\s+brightness(?:es)?\s+(?:to\s+)?(\d{1,3})\s*%?\b", tl)
        or re.search(r"\bbrightness(?:es)?\s+(\d{1,3})\s*%?\b", tl)
    )
    if m_global:
        val = max(0, min(100, int(m_global.group(1))))

        # First, use the active room's configured strategy. The strategy may be
        # a proxy entity, an HA area, or an explicit entity list.
        request_target = get_room_brightness_target()
        if request_target:
            if apply_room_brightness(
                request_target["room_id"],
                val,
                call_ha_service=call_ha_service,
                remember_light=remember_light,
            ):
                return maybe_say(f"Brightness {val} percent.")
            return None

        # Fall back to the legacy global default behavior if no request-local
        # brightness default applies.
        if default_brightness_number and entity_exists(default_brightness_number, states_snapshot):
            if set_number_value(default_brightness_number, val):
                return maybe_say(f"Brightness {val} percent.")

        lr_key = "living room brightness"
        if lr_key in light_phrase_overrides:
            eid = light_phrase_overrides[lr_key]
            if call_ha_service("light/turn_on", {"entity_id": eid, "brightness_pct": val}):
                remember_light(eid)
                return maybe_say(f"Brightness {val} percent.")
        return None

    # --------------------------------------------------
    # 3) Room shorthand: "set kitchen to 40%"
    # --------------------------------------------------
    m_room = re.search(
        r"\bset\s+([a-zA-Z0-9 \-']+?)\s+(?:brightness(?:es)?\s+)?(?:to\s+)?(\d{1,3})\s*%?\b",
        tl,
    )
    if m_room:
        room = m_room.group(1).strip()
        val = max(0, min(100, int(m_room.group(2))))

        room_id = resolve_room_id(room)
        if room_id and get_room_brightness_target(room_id):
            if apply_room_brightness(
                room_id,
                val,
                call_ha_service=call_ha_service,
                remember_light=remember_light,
            ):
                return maybe_say(f"{room.title()} brightness {val} percent.")

    # --------------------------------------------------
    # 4) Generic: "set <device> to 20%"  (brightness fallback)
    # --------------------------------------------------
    m_set_to_pct = re.search(
        r"\bset\s+(?:the\s+)?(.+?)\s+to\s+(\d{1,3})\s*%?\b",
        tl,
    )
    if m_set_to_pct:
        raw = m_set_to_pct.group(1).strip()
        val = max(0, min(100, int(m_set_to_pct.group(2))))

        targets = split_targets(raw)
        any_ok = False
        used_ctx_any = False

        for t in targets:
            # PIPHONE_POSSESSIVE_STRIP
            t = re.sub(r"(?:'s|’s)$", "", (t or "").strip()).strip()
            eid, used_ctx = resolve_light_target(t)
            if not eid:
                continue
            if call_ha_service("light/turn_on", {"entity_id": eid, "brightness_pct": val}):
                remember_light(eid)
                any_ok = True
                used_ctx_any = used_ctx_any or bool(used_ctx)

        if any_ok:
            if len(targets) == 1:
                return maybe_say(
                    f"Setting it to {val} percent."
                    if used_ctx_any
                    else f"Setting {raw} to {val} percent."
                )
            return maybe_say("Okay.")
        return None


    # --------------------------------------------------
    # 4b) Relaxed: "<target> 20" / "<target> 20%"
    #     Examples:
    #       "dining light 20"
    #       "stair light 5%"
    #       "kitchen 40"   (room shorthand, if your resolver supports it)
    #
    # Safety:
    #   - only triggers when it ends with a 0-100 number
    #   - skips 'now 50' (contextual shorthand)
    #   - requires light-ish target or resolvable light entity
    # --------------------------------------------------
    if tl.strip().startswith("now "):
        pass
    else:
        m_relaxed = re.fullmatch(r"(.+?)\s+(\d{1,3})\s*%?\s*", tl)
        if m_relaxed:
            raw = (m_relaxed.group(1) or "").strip()
            if raw.lower() not in ("brightness", "brightnes", "bright"):
                val = max(0, min(100, int(m_relaxed.group(2))))

                targets = split_targets(raw)

                # If this is exactly a configured room, prefer its brightness
                # strategy instead of relying on light-name resolver ambiguity.
                if len(targets) == 1:
                    room = targets[0].strip().lower()
                    room_id = resolve_room_id(room)
                    if room_id and get_room_brightness_target(room_id):
                        if apply_room_brightness(
                            room_id,
                            val,
                            call_ha_service=call_ha_service,
                            remember_light=remember_light,
                        ):
                            return maybe_say(f"{room.title()} brightness {val} percent.")
                        return None

                any_ok = False
                used_ctx_any = False

                for t in targets:
                    # PIPHONE_POSSESSIVE_STRIP
                    t = re.sub(r"(?:'s|’s)$", "", (t or "").strip()).strip()
                    if not t:
                        continue

                    # Only attempt if target looks light-ish or is one of common rooms or is resolvable.
                    looks_lightish = bool(re.search(r"\b(light|lamp)\b", t))
                    is_common_room = bool(resolve_room_id(t))

                    eid = None
                    used_ctx = False
                    if looks_lightish or is_common_room:
                        eid, used_ctx = resolve_light_target(t)
                    else:
                        # still allow resolver as a last check, but don't broaden too much
                        eid, used_ctx = resolve_light_target(t)

                    if not eid:
                        continue

                    if call_ha_service("light/turn_on", {"entity_id": eid, "brightness_pct": val}):
                        remember_light(eid)
                        any_ok = True
                        used_ctx_any = used_ctx_any or bool(used_ctx)

                if any_ok:
                    if len(targets) == 1:
                        return maybe_say(
                            f"Setting it to {val} percent."
                            if used_ctx_any
                            else f"Setting {raw} to {val} percent."
                        )
                    return maybe_say("Okay.")
                return None

    # --------------------------------------------------
    # 4) Contextual shorthand: "now 50%" / "50%"
    # --------------------------------------------------
    m_pct = re.fullmatch(r"(?:now\s+)?(\d{1,3})\s*%?\s*", tl)
    if m_pct:
        eid = get_recent_light()
        if eid:
            val = max(0, min(100, int(m_pct.group(1))))
            if call_ha_service("light/turn_on", {"entity_id": eid, "brightness_pct": val}):
                return maybe_say(f"Setting it to {val} percent.")
        return None

    return None
