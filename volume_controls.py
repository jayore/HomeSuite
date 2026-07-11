"""Handle absolute, relative, mute, and unmute media volume commands.

Spoken rooms resolve through the configured player map, with request context
providing the default room. Relative changes read the supplied Home Assistant
state snapshot before issuing a clamped service call. Light brightness and
other numeric controls are intentionally outside this module.
"""

import re
from typing import Optional, Dict, Any

from home_registry import get_room_alias_map, get_room_volume_target, resolve_room_id
from multi_target_utils import split_targets
from room_volume import apply_room_volume, apply_room_volume_step


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[.!,?]+$", "", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _find_room_in_text(tl: str, players_map: Dict[str, str]) -> Optional[str]:
    t = _norm(tl)
    candidates = set(players_map.keys())
    candidates.update(get_room_alias_map().keys())
    for room in sorted(candidates, key=len, reverse=True):
        if re.search(rf"\b{re.escape(room)}\b", t):
            return room
    return None


def _get_state(entity_id: str, states_snapshot) -> Optional[str]:
    if not states_snapshot:
        return None
    for st in states_snapshot:
        if st.get("entity_id") == entity_id:
            return st.get("state")
    return None


def _get_attr(entity_id: str, attr: str, states_snapshot) -> Any:
    if not states_snapshot:
        return None
    for st in states_snapshot:
        if st.get("entity_id") == entity_id:
            return (st.get("attributes") or {}).get(attr)
    return None


def _clamp_int(v: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(v)))


def _clamp_float(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(v)))



_NUM_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "to": 2,
    "too": 2,
    "three": 3,
    "four": 4,
    "for": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "couple": 2,
    "few": 3,
}

_TENS_WORDS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

def _parse_amount_phrase(phrase: str) -> Optional[int]:
    phrase = (phrase or "").strip().lower()
    phrase = phrase.replace("-", " ")
    phrase = re.sub(r"[^a-z0-9\s]+", " ", phrase)
    phrase = re.sub(r"\s+", " ", phrase).strip()
    if not phrase:
        return None

    if phrase.isdigit():
        try:
            return int(phrase)
        except Exception:
            return None

    toks = phrase.split()
    if not toks:
        return None

    # "one hundred"
    if len(toks) == 2 and toks[0] == "one" and toks[1] == "hundred":
        return 100

    # one token: "three"
    if len(toks) == 1:
        tok = toks[0]
        if tok in _TENS_WORDS:
            return _TENS_WORDS[tok]
        return _NUM_WORDS.get(tok)

    # two tokens: "fifty three"
    if len(toks) == 2:
        t0, t1 = toks
        if t0 in _TENS_WORDS:
            unit = _NUM_WORDS.get(t1)
            if unit is None:
                unit = 0
            return _TENS_WORDS[t0] + int(unit)
        # fallback: try first
        if t0 in _NUM_WORDS:
            return _NUM_WORDS[t0]
        if t0 in _TENS_WORDS:
            return _TENS_WORDS[t0]
        return None

    # fallback
    t0 = toks[0]
    if t0 in _NUM_WORDS:
        return _NUM_WORDS[t0]
    if t0 in _TENS_WORDS:
        return _TENS_WORDS[t0]
    return None

def handle_volume_controls(
    *,
    tl: str,
    call_ha_service,
    maybe_say,
    states_snapshot=None,
    sonos_players: Optional[Dict[str, str]] = None,
    default_sonos_room: Optional[str] = None,
    default_volume_room: Optional[str] = None,
    step_percent: int = 5,
) -> Optional[str]:
    """Parse media-volume language and update one resolved player."""
    """
    Volume controls.

    Supports:
      - "set volume to 20" / "volume 20%"
      - "set kitchen volume to 20%"
      - "volume up/down" / "louder/quieter" / "turn it up/down"
      - "increase volume by 10" / "decrease volume by 5"
      - "mute" / "unmute" (Sonos native mute, preserves prior volume)

    Returns:
      - None if not a volume command
      - ""/str if handled (silent unless maybe_say speaks)
    """

    t = _norm(tl)
    if not t:
        return None

    players = sonos_players or {}
    room = _find_room_in_text(t, players) if players else None

    def target_media_entity() -> Optional[str]:
        if not players:
            return None
        if room:
            return players.get(room)
        if default_sonos_room and default_sonos_room in players:
            return players[default_sonos_room]
        return None

    # ----------------------------
    # Mute / Unmute / Toggle
    # ----------------------------
    _mute_toggle = re.search(
        r"\b(?:toggle\s+mute|mute\s+toggle|mute\s+unmute|unmute\s+mute|toggle\s+muting)\b", t
    )
    if _mute_toggle:
        eid = target_media_entity()
        if not eid:
            return None
        cur_muted = _get_attr(eid, "is_volume_muted", states_snapshot)
        if cur_muted is None:
            return None
        new_muted = not cur_muted
        ok = call_ha_service(
            "media_player/volume_mute",
            {"entity_id": eid, "is_volume_muted": new_muted},
        )
        if not ok:
            return None
        return maybe_say("Unmuted." if not new_muted else "Muted.")

    if re.search(r"\b(mute|unmute)\b", t):
        verb = "unmute" if "unmute" in t else "mute"
        eid = target_media_entity()
        if not eid:
            return None
        ok = call_ha_service(
            "media_player/volume_mute",
            {"entity_id": eid, "is_volume_muted": (verb == "mute")},
        )
        if not ok:
            return None
        return maybe_say("Muted." if verb == "mute" else "Unmuted.")

    # ----------------------------
    # Absolute set
    # ----------------------------
    # Supports:
    #   - "set volume to 20" / "volume 20%"
    #   - "set kitchen volume to 20%"
    #   - "set kitchen and bathroom volume(s) to 20%"
    #   - "set kitchen and bathroom's volume to 20%"
    #   - "set volume in kitchen (and bathroom) to 20%"
    #   - "set volume on bookshelf (and here) to 5%"
    #
    # Notes:
    # - "here"/"this room" maps to default_sonos_room when available.
    # - For multi-room lists we are strict: if any token can't be resolved, we do nothing.

    players = sonos_players or {}
    volume_room = default_volume_room or default_sonos_room
    default_room = _norm(volume_room)

    def _resolve_room_token(tok: str) -> Optional[str]:
        tok = (tok or "").strip()
        tok = re.sub(r"(?:'s|’s)$", "", tok).strip()
        tok = re.sub(r"^the\s+", "", tok).strip()
        tn = _norm(tok)
        if tn in ("here", "this room"):
            return default_room
        room_id = resolve_room_id(tn)
        if room_id:
            return room_id.replace("_", " ")
        if tn in players:
            return tn
        return _find_room_in_text(tn, players)

    def _resolve_room_list_strict(text: str) -> Optional[list]:
        toks = split_targets((text or "").strip())
        if not toks:
            return None
        out = []
        seen = set()
        for t0 in toks:
            rr = _resolve_room_token(t0)
            if not rr:
                return None
            rn = _norm(rr)
            if rn not in seen:
                out.append(rn)
                seen.add(rn)
        return out if out else None

    def _set_room_volume(room_name: str, pct: int) -> bool:
        if not room_name:
            return False
        configured_target = get_room_volume_target(room_name)
        if configured_target:
            return apply_room_volume(
                room_name,
                pct,
                call_ha_service=call_ha_service,
            )
        if not players:
            return False
        rn = _norm(room_name)
        eid = players.get(rn)
        if not eid:
            return False
        return bool(call_ha_service(
            "media_player/volume_set",
            {"entity_id": eid, "volume_level": _clamp_float(pct / 100.0)},
        ))

    # 1) Explicit "set volume in/on <rooms> to N"
    m_abs_in = re.search(
        r"\bset\s+volumes?\s+(?:in|on)\s+(?:the\s+)?(.+?)\s+(?:to\s+)?(\d{1,3})\s*%?\b",
        t,
    )
    if m_abs_in:
        raw_rooms = (m_abs_in.group(1) or "").strip()
        pct = _clamp_int(int(m_abs_in.group(2)))
        rooms = _resolve_room_list_strict(raw_rooms)
        if not rooms:
            return None
        any_ok = False
        for rr in rooms:
            any_ok = _set_room_volume(rr, pct) or any_ok
        if not any_ok:
            return None
        if len(rooms) == 1:
            return maybe_say(f"{rooms[0].title()} volume {pct} percent.")
        return maybe_say("Okay.")

    # 2) Explicit "set <rooms> volume(s) to N" (supports possessive)
    m_abs_rooms = re.search(
        r"\bset\s+(.+?)\s*(?:'s|’s)?\s+volumes?\s+(?:to\s+)?(\d{1,3})\s*%?\b",
        t,
    )
    if m_abs_rooms:
        raw_rooms = (m_abs_rooms.group(1) or "").strip()
        pct = _clamp_int(int(m_abs_rooms.group(2)))
        rooms = _resolve_room_list_strict(raw_rooms)
        if not rooms:
            return None
        any_ok = False
        for rr in rooms:
            any_ok = _set_room_volume(rr, pct) or any_ok
        if not any_ok:
            return None
        if len(rooms) == 1:
            return maybe_say(f"{rooms[0].title()} volume {pct} percent.")
        return maybe_say("Okay.")

    # 3) Global: adjust the request room's configured volume target.
    m_abs_global = re.search(r"\b(?:set\s+)?volumes?\s+(?:to\s+)?(\d{1,3})\s*%?\b", t)
    if m_abs_global:
        pct = _clamp_int(int(m_abs_global.group(1)))

        configured_target = get_room_volume_target(volume_room)
        if configured_target:
            ok = apply_room_volume(
                volume_room,
                pct,
                call_ha_service=call_ha_service,
            )
            if not ok:
                return None
            return maybe_say(f"Volume {pct} percent.")

        eid = target_media_entity()
        if not eid:
            return None
        ok = call_ha_service(
            "media_player/volume_set",
            {"entity_id": eid, "volume_level": _clamp_float(pct / 100.0)},
        )
        if not ok:
            return None
        return maybe_say(f"Volume {pct} percent.")
    # ----------------------------
    # Relative: up/down/louder/quieter (+ optional by N)
    # ----------------------------
    # Examples:
    #   "volume up", "volume down", "turn it up", "louder", "quieter"
    #   "increase volume by 10", "decrease kitchen volume by 5"
    m_rel = re.search(
        r"\b(?:(increase|decrease)\s+(?:the\s+)?(?:.+?\s+)?volume\s*(?:by\s+)?(\d{1,3})?\b|"
        r"(volume)\s+(up|down)\b|"
        r"(louder|quieter)\b|"
        r"turn\s+(?:it\s+)?(up|down)\b)\b",
        t,
    )
    if m_rel:
        direction = None  # "up" or "down"
        amt = None

        if m_rel.group(1):  # increase/decrease
            direction = "up" if m_rel.group(1) == "increase" else "down"
            if m_rel.group(2):
                amt = _clamp_int(int(m_rel.group(2)), 1, 100)
        elif m_rel.group(4):  # volume up/down
            direction = "up" if m_rel.group(4) == "up" else "down"
        elif m_rel.group(5):  # louder/quieter
            direction = "up" if m_rel.group(5) == "louder" else "down"
        elif m_rel.group(6):  # turn it up/down
            direction = "up" if m_rel.group(6) == "up" else "down"

        if not direction:
            return None

        # If we got here via increase/decrease and the regex didn't capture the number reliably,
        # fall back to a simple "by N" extractor.
        if amt is None and ("increase" in t or "decrease" in t):
            # Accept digits ("3") and word numbers ("three", "fifty three")
            m_by = re.search(r"\bby\s+([a-z0-9\-]+(?:\s+[a-z0-9\-]+)?)\b", t)
            if m_by:
                parsed = _parse_amount_phrase(m_by.group(1))
                if parsed is not None:
                    amt = _clamp_int(int(parsed), 1, 100)

        delta = int(amt) if amt is not None else int(step_percent)

        # Room-specific: honor the room's configured helper/media target.
        if room:
            configured_target = get_room_volume_target(room)
            if configured_target:
                signed_delta = delta if direction == "up" else -delta
                ok = apply_room_volume_step(
                    room,
                    signed_delta,
                    call_ha_service=call_ha_service,
                    states_snapshot=states_snapshot,
                )
                return maybe_say("Okay.") if ok else None

            eid = target_media_entity()
            if not eid:
                return None

            # Prefer precise step sizing via volume_set (so room-specific behaves like the default 5% step),
            # falling back to volume_up/down only if we cannot read current volume_level.
            cur = _get_attr(eid, "volume_level", states_snapshot)
            if isinstance(cur, (int, float)):
                new_level = _clamp_float(
                    float(cur) + (delta / 100.0) * (1.0 if direction == "up" else -1.0)
                )
                ok = call_ha_service("media_player/volume_set", {"entity_id": eid, "volume_level": new_level})
                if not ok:
                    return None
                return maybe_say("Okay.")

            # Fallback: integration-defined step (often 2%)
            if amt is None:
                svc = "media_player/volume_up" if direction == "up" else "media_player/volume_down"
                ok = call_ha_service(svc, {"entity_id": eid})
                if not ok:
                    return None
                return maybe_say("Okay.")

            # If we can't read volume_level but user asked "by N", approximate with repeated up/down.
            steps = max(1, min(20, round(delta / 5.0)))
            svc = "media_player/volume_up" if direction == "up" else "media_player/volume_down"
            ok_all = True
            for _ in range(steps):
                ok_all = bool(call_ha_service(svc, {"entity_id": eid})) and ok_all
            if not ok_all:
                return None
            return maybe_say("Okay.")

        # No explicit room: adjust the request room's configured target.
        configured_target = get_room_volume_target(volume_room)
        if configured_target:
            signed_delta = delta if direction == "up" else -delta
            ok = apply_room_volume_step(
                volume_room,
                signed_delta,
                call_ha_service=call_ha_service,
                states_snapshot=states_snapshot,
            )
            return maybe_say("Okay.") if ok else None

        eid = target_media_entity()
        if not eid:
            return None

        cur = _get_attr(eid, "volume_level", states_snapshot)
        if isinstance(cur, (int, float)):
            new_level = _clamp_float(
                float(cur) + (delta / 100.0) * (1.0 if direction == "up" else -1.0)
            )
            ok = call_ha_service("media_player/volume_set", {"entity_id": eid, "volume_level": new_level})
            if not ok:
                return None
            return maybe_say("Okay.")

        # Fallback if state is unavailable.
        if amt is None:
            svc = "media_player/volume_up" if direction == "up" else "media_player/volume_down"
            ok = call_ha_service(svc, {"entity_id": eid})
            if not ok:
                return None
            return maybe_say("Okay.")

        steps = max(1, min(20, round(delta / 5.0)))
        svc = "media_player/volume_up" if direction == "up" else "media_player/volume_down"
        ok_all = True
        for _ in range(steps):
            ok_all = bool(call_ha_service(svc, {"entity_id": eid})) and ok_all
        if not ok_all:
            return None
        return maybe_say("Okay.")

    return None
