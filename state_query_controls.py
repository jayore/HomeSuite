"""Answer read-only device, room, temperature, and volume state questions.

The handler reads one Home Assistant state snapshot and uses the injected
resolver for named devices. Room-wide light questions enumerate actual entities
registered to the room's Home Assistant area. No service calls occur here, and
unresolved or unavailable state produces an explicit answer or falls through.
"""

from __future__ import annotations
import math
import re
import logging
from typing import Optional, Callable, Tuple, Any, List, Dict

from home_registry import (
    find_room_by_alias,
    get_room,
    get_room_alias_map,
    get_room_label,
    is_assistant_bulk_entity_allowed,
)
from ha_client import ha_get_entities_for_area, ha_get_light_entities_for_area

# resolve_device_entity: phrase -> (entity_id, domain) or (entity_id, domain, via...)
ResolveFn = Callable[[str], Optional[Tuple[Any, ...]]]

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[?.!]+$", "", s).strip()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def looks_like_state_query(text: str) -> bool:
    """Return whether text should enter the deterministic read-only path."""
    t = _norm(text)
    t = re.sub(r"^(hey|hi|okay|ok|um|uh|please)\s+", "", t).strip()
    if not t:
        return False

    if re.match(r"^(?:is|are)\s+.+\s+(?:on|off|locked|unlocked|open|closed)$", t):
        return True
    if re.match(r"^(?:what|which)\b.*\blights?\b.*\b(?:on|off)$", t):
        return True
    if re.match(
        r"^(?:is|are)\s+any\b.*\b(?:lights?|doors?|windows?|openings?)\b.*"
        r"\b(?:on|off|open|closed)$",
        t,
    ):
        return True
    if re.match(
        r"^(?:what|which)\b.*\b(?:doors?|windows?|openings?)\b.*\b(?:open|closed)$",
        t,
    ):
        return True

    sensor_words = bool(
        re.search(r"\b(?:temperature|temp|humidity|battery|charge)\b", t)
        or re.search(r"\bhow\s+(?:hot|cold|humid)\b", t)
    )
    if sensor_words and re.match(r"^(?:what|whats|what's|how|is|are)\b", t):
        return not bool(re.search(r"\b(?:set|change|raise|lower|increase|decrease)\b", t))

    if re.search(r"\b(?:volume|how\s+loud)\b", t):
        return not bool(re.search(r"\b(?:set|turn|increase|decrease|raise|lower)\b", t))
    return False

def _get_state_obj(states_snapshot: Optional[list], entity_id: str) -> Optional[dict]:
    if not states_snapshot or not entity_id:
        return None
    for st in states_snapshot:
        if st.get("entity_id") == entity_id:
            return st
    return None

def _get_state_str(states_snapshot: Optional[list], entity_id: str) -> str:
    st = _get_state_obj(states_snapshot, entity_id) or {}
    return str(st.get("state") or "").strip().lower()

def _get_attr(states_snapshot: Optional[list], entity_id: str, key: str, default=None):
    st = _get_state_obj(states_snapshot, entity_id) or {}
    attrs = st.get("attributes") or {}
    return attrs.get(key, default)

def _friendly_name(states_snapshot: Optional[list], entity_id: str) -> str:
    fn = _get_attr(states_snapshot, entity_id, "friendly_name", "") or ""
    return str(fn).strip() if fn else entity_id


def _room_scope_from_text(t: str) -> Tuple[Optional[str], Optional[str]]:
    """Return an explicitly mentioned configured room and its HA area ID."""
    aliases = get_room_alias_map()
    for alias in sorted(aliases, key=len, reverse=True):
        pattern = re.escape(alias).replace(r"\ ", r"\s+")
        if not re.search(rf"(?<!\w){pattern}(?!\w)", t):
            continue
        room_id = aliases[alias]
        room = get_room(room_id) or {}
        area_id = str(room.get("ha_area_id") or "").strip() or None
        return room_id, area_id
    return None, None


def _format_name_list(names: List[str], *, limit: int = 5) -> str:
    clean = sorted({str(name).strip() for name in names if str(name).strip()})
    if not clean:
        return ""
    if len(clean) > limit:
        shown = clean[:limit]
        return ", ".join(shown) + f", and {len(clean) - limit} more"
    if len(clean) == 1:
        return clean[0]
    if len(clean) == 2:
        return f"{clean[0]} and {clean[1]}"
    return ", ".join(clean[:-1]) + f", and {clean[-1]}"


def _states_in_room(
    states_snapshot: Optional[list],
    *,
    area_id: Optional[str],
    domains,
) -> List[dict]:
    rows = [row for row in (states_snapshot or []) if isinstance(row, dict)]
    if not area_id:
        return rows
    allowed = set(ha_get_entities_for_area(area_id, domains=domains))
    return [row for row in rows if row.get("entity_id") in allowed]

def _boolish_on(state: str) -> Optional[bool]:
    s = (state or "").strip().lower()
    if s in ("on", "open", "opened", "true", "locked"):  # "locked" handled separately too
        return True
    if s in ("off", "closed", "false", "unlocked"):
        return False
    return None

def _extract_thing_between(t: str, left: str, right: str) -> str:
    # naive but robust for spoken queries
    t2 = t
    if t2.startswith(left):
        t2 = t2[len(left):].strip()
    if t2.endswith(right):
        t2 = t2[: -len(right)].strip()
    return t2.strip()

def _resolve_phrase(resolve_device_entity: ResolveFn, phrase: str) -> Optional[Tuple[str, str]]:
    if not phrase:
        return None
    r = None
    try:
        r = resolve_device_entity(phrase)
    except Exception:
        r = None
    if not r:
        return None
    if isinstance(r, (tuple, list)) and len(r) >= 2:
        eid = r[0]
        dom = r[1]
        if isinstance(eid, str) and isinstance(dom, str):
            return (eid, dom)
    return None

def _answer_is_on_off(t: str, *, states_snapshot: Optional[list], resolve_device_entity: ResolveFn) -> Optional[str]:
    # "is side lamp on" / "are the patio lights off"
    #
    # Important:
    # Explicit all-lights room queries like
    #   "are all the living room lights off"
    # must be handled by _answer_all_lights_on_off(), not by the generic
    # fuzzy device resolver path here.
    if re.match(r"^are\s+all\s+(?:of\s+)?(?:the\s+)?(.+?)\s+lights\s+(on|off)$", t):
        return None

    m = re.match(r"^(is|are)\s+(.+?)\s+(on|off)$", t)
    if not m:
        return None

    thing = m.group(2).strip()
    want = m.group(3).strip()

    rr = _resolve_phrase(resolve_device_entity, thing)
    if not rr:
        return "I couldn't find that device."
    eid, _dom = rr
    st = _get_state_str(states_snapshot, eid)
    if st not in ("on", "off"):
        return "I couldn't read that device right now."

    # lights/switches use on/off; others best-effort
    is_on = (st == "on")
    if want == "on":
        return ("Yes." if is_on else "No.")
    else:
        return ("Yes." if not is_on else "No.")

def _answer_is_locked(t: str, *, states_snapshot: Optional[list], resolve_device_entity: ResolveFn) -> Optional[str]:
    # "is the front door locked"
    m = re.match(r"^(is|are)\s+(.+?)\s+(locked|unlocked)$", t)
    if not m:
        return None
    thing = m.group(2).strip()
    want = m.group(3).strip()

    rr = _resolve_phrase(resolve_device_entity, thing)
    if not rr:
        # try appending "lock" to help resolve
        rr = _resolve_phrase(resolve_device_entity, thing + " lock")
    if not rr:
        return "I couldn't find that lock."
    eid, dom = rr
    st = _get_state_str(states_snapshot, eid)

    if st not in ("locked", "unlocked"):
        return "I couldn't read that lock right now."

    # HA lock domain typically: locked/unlocked
    if want == "locked":
        return ("Yes." if st == "locked" else "No.")
    else:
        return ("Yes." if st == "unlocked" else "No.")

def _answer_all_lights_on_off(t: str, *, states_snapshot: Optional[list]) -> Optional[str]:
    # Explicit room-wide query only:
    #   "are all of the living room lights off"
    #   "are all the kitchen lights on"
    m = re.match(r"^are\s+all\s+(?:of\s+)?(?:the\s+)?(.+?)\s+lights\s+(on|off)$", t)
    if not m:
        return None

    room_phrase = m.group(1).strip()
    want = m.group(2).strip()

    room_id = find_room_by_alias(room_phrase)
    if not room_id:
        return "I couldn't find that room."

    room_cfg = get_room(room_id) or {}
    area_id = str(room_cfg.get("ha_area_id") or "").strip()
    if not area_id:
        return "I couldn't find that room."

    if not states_snapshot:
        return "I couldn't read lights right now."

    light_entity_ids = ha_get_light_entities_for_area(area_id)
    if not light_entity_ids:
        return "I couldn't find any matching lights."

    state_by_entity = {}
    for st in (states_snapshot or []):
        eid = st.get("entity_id") or ""
        if isinstance(eid, str):
            state_by_entity[eid] = str(st.get("state") or "").strip().lower()

    found_states = []
    for eid in light_entity_ids:
        if (
            is_assistant_bulk_entity_allowed(eid)
            and state_by_entity.get(eid) in ("on", "off")
        ):
            found_states.append((eid, state_by_entity[eid]))

    if not found_states:
        return "I couldn't read lights right now."

    on_count = sum(1 for (_eid, s) in found_states if s == "on")
    total = len(found_states)

    if want == "off":
        if on_count == 0:
            return "Yes."
        if on_count == total:
            return "No."
        return f"No. {on_count} of {total} lights are on."

    # want == "on"
    if on_count == total:
        return "Yes."
    if on_count == 0:
        return "No."
    return f"No. {on_count} of {total} lights are on."

def _answer_lights_on_off_summary(
    t: str,
    *,
    states_snapshot: Optional[list],
) -> Optional[str]:
    list_match = re.match(r"^(?:what|which)\b.*\blights?\b.*\b(on|off)$", t)
    any_match = re.match(r"^(?:is|are)\s+any\b.*\blights?\b.*\b(on|off)$", t)
    match = list_match or any_match
    if not match:
        return None

    want = match.group(1)
    room_id, area_id = _room_scope_from_text(t)
    if room_id and not area_id:
        return f"I couldn't map {get_room_label(room_id) or 'that room'} to a Home Assistant area."

    rows = _states_in_room(
        states_snapshot,
        area_id=area_id,
        domains={"light"},
    )
    lights = [
        row
        for row in rows
        if str(row.get("entity_id") or "").startswith("light.")
        and is_assistant_bulk_entity_allowed(row.get("entity_id"))
        and str(row.get("state") or "").strip().lower() in ("on", "off")
    ]
    if not lights:
        scope = f" in {get_room_label(room_id)}" if room_id else ""
        return f"I couldn't find any readable lights{scope}."

    matching = [
        row for row in lights
        if str(row.get("state") or "").strip().lower() == want
    ]
    if any_match:
        if not matching:
            return "No."
        count = len(matching)
        noun = "light is" if count == 1 else "lights are"
        return f"Yes. {count} {noun} {want}."

    if not matching:
        scope = f" in {get_room_label(room_id)}" if room_id else ""
        return f"No lights are {want}{scope}."

    names = [
        str((row.get("attributes") or {}).get("friendly_name") or row.get("entity_id"))
        for row in matching
    ]
    subject = _format_name_list(names)
    verb = "is" if len(set(names)) == 1 else "are"
    return f"{subject} {verb} {want}."


_OPENING_DEVICE_CLASSES = {"door", "window", "garage", "garage_door", "opening"}


def _opening_value(state: dict) -> Optional[bool]:
    entity_id = str(state.get("entity_id") or "")
    domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
    attrs = state.get("attributes") or {}
    device_class = str(attrs.get("device_class") or "").strip().lower()
    raw_state = str(state.get("state") or "").strip().lower()

    if domain == "binary_sensor" and device_class in _OPENING_DEVICE_CLASSES:
        if raw_state == "on":
            return True
        if raw_state == "off":
            return False
    if domain == "cover":
        if raw_state in ("open", "opening"):
            return True
        if raw_state in ("closed", "closing"):
            return False
    return None


def _answer_is_open_closed(
    t: str,
    *,
    states_snapshot: Optional[list],
    resolve_device_entity: ResolveFn,
) -> Optional[str]:
    match = re.match(r"^(?:is|are)\s+(.+?)\s+(open|closed)$", t)
    if not match:
        return None

    thing = match.group(1).strip()
    want_open = match.group(2) == "open"
    resolved = _resolve_phrase(resolve_device_entity, thing)
    if not resolved:
        return "I couldn't find that door, window, or cover."
    entity_id, _domain = resolved
    state = _get_state_obj(states_snapshot, entity_id)
    if not state:
        return "I couldn't read that opening right now."
    actual_open = _opening_value(state)
    if actual_open is None:
        name = _friendly_name(states_snapshot, entity_id)
        return f"I found {name}, but it doesn't report whether it's open."
    return "Yes." if actual_open == want_open else "No."


def _answer_opening_summary(t: str, *, states_snapshot: Optional[list]) -> Optional[str]:
    list_match = re.match(
        r"^(?:what|which)\b.*\b(?:doors?|windows?|openings?)\b.*\b(open|closed)$",
        t,
    )
    any_match = re.match(
        r"^(?:is|are)\s+any\b.*\b(?:doors?|windows?|openings?)\b.*\b(open|closed)$",
        t,
    )
    match = list_match or any_match
    if not match:
        return None

    want_open = match.group(1) == "open"
    room_id, area_id = _room_scope_from_text(t)
    if room_id and not area_id:
        return f"I couldn't map {get_room_label(room_id) or 'that room'} to a Home Assistant area."
    rows = _states_in_room(
        states_snapshot,
        area_id=area_id,
        domains={"binary_sensor", "cover"},
    )

    wants_garage = bool(re.search(r"\bgarage\b", t))
    wants_windows = bool(re.search(r"\bwindows?\b", t))
    wants_doors = bool(re.search(r"\bdoors?\b", t))

    candidates = []
    for row in rows:
        attrs = row.get("attributes") or {}
        device_class = str(attrs.get("device_class") or "").strip().lower()
        if device_class not in _OPENING_DEVICE_CLASSES:
            continue
        actual_open = _opening_value(row)
        if actual_open is None:
            continue
        if wants_garage and device_class not in ("garage", "garage_door"):
            continue
        if wants_windows and not wants_doors and device_class != "window":
            continue
        if wants_doors and not wants_windows and device_class == "window":
            continue
        candidates.append((row, actual_open))

    if not candidates:
        return "I couldn't find any readable doors or windows."
    matching = [row for row, actual_open in candidates if actual_open == want_open]
    if any_match:
        if not matching:
            return "No."
        if wants_windows and not wants_doors:
            noun = "window"
        elif wants_doors and not wants_windows:
            noun = "door"
        else:
            noun = "opening"
        count = len(matching)
        return f"Yes. {count} {noun if count == 1 else noun + 's'} {'is' if count == 1 else 'are'} {match.group(1)}."
    if not matching:
        return f"No matching doors or windows are {match.group(1)}."
    names = [
        str((row.get("attributes") or {}).get("friendly_name") or row.get("entity_id"))
        for row in matching
    ]
    subject = _format_name_list(names)
    verb = "is" if len(set(names)) == 1 else "are"
    return f"{subject} {verb} {match.group(1)}."


def _sensor_kind(t: str) -> Optional[str]:
    if re.search(r"\b(?:temperature|temp)\b", t) or re.search(r"\bhow\s+(?:hot|cold)\b", t):
        return "temperature"
    if re.search(r"\bhumidity\b", t) or re.search(r"\bhow\s+humid\b", t):
        return "humidity"
    if re.search(r"\b(?:battery|charge)\b", t):
        return "battery"
    return None


_SENSOR_QUERY_STOPWORDS = {
    "what", "whats", "is", "are", "the", "a", "an", "of", "in", "for",
    "at", "right", "now", "current", "currently", "level", "reading", "value",
    "how", "hot", "cold", "humid", "humidity", "temperature", "temp", "battery",
    "charge", "much", "does", "do", "has", "have", "it", "inside", "indoor",
    "sensor", "room", "percent", "percentage",
}


def _sensor_target_tokens(t: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", t.lower())
    return {word for word in words if len(word) > 1 and word not in _SENSOR_QUERY_STOPWORDS}


def _answer_numeric_sensor(t: str, *, states_snapshot: Optional[list]) -> Optional[str]:
    kind = _sensor_kind(t)
    if not kind:
        return None
    if not states_snapshot:
        return f"I couldn't read {kind} sensors right now."

    room_id, area_id = _room_scope_from_text(t)
    if room_id and not area_id:
        return f"I couldn't map {get_room_label(room_id) or 'that room'} to a Home Assistant area."
    rows = _states_in_room(states_snapshot, area_id=area_id, domains={"sensor"})

    candidates = []
    for row in rows:
        entity_id = str(row.get("entity_id") or "")
        if not entity_id.startswith("sensor."):
            continue
        attrs = row.get("attributes") or {}
        device_class = str(attrs.get("device_class") or "").strip().lower()
        unit = str(attrs.get("unit_of_measurement") or "").strip()
        name = str(attrs.get("friendly_name") or entity_id).strip()
        haystack = f"{entity_id} {name}".lower()
        if kind == "temperature":
            matches_kind = device_class == "temperature" or "°" in unit or unit in ("C", "F")
        elif kind == "humidity":
            matches_kind = device_class == "humidity" or ("humidity" in haystack and unit == "%")
        else:
            matches_kind = device_class == "battery" or ("battery" in haystack and unit == "%")
        if not matches_kind:
            continue
        try:
            value = float(row.get("state"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        candidates.append({
            "entity_id": entity_id,
            "value": value,
            "unit": unit,
            "name": name,
            "haystack": haystack,
        })

    if not candidates:
        scope = f" in {get_room_label(room_id)}" if room_id else ""
        return f"I couldn't find any readable {kind} sensors{scope}."

    target_tokens = _sensor_target_tokens(t)
    scored = []
    for candidate in candidates:
        hay_words = set(re.findall(r"[a-z0-9]+", candidate["haystack"]))
        scored.append((len(target_tokens & hay_words), candidate))
    best_score = max(score for score, _candidate in scored)
    selected = [candidate for score, candidate in scored if score == best_score] if best_score else candidates

    if kind == "battery":
        if len(selected) > 1 and not room_id and best_score == 0:
            return "Which device's battery do you want?"
        if len(selected) > 1:
            parts = [
                f"{candidate['name']} {candidate['value']:.0f} percent"
                for candidate in sorted(selected, key=lambda item: item["name"])[:4]
            ]
            suffix = f"; and {len(selected) - 4} more" if len(selected) > 4 else ""
            return "Battery levels: " + "; ".join(parts) + suffix + "."
        candidate = selected[0]
        return f"{candidate['name']} is at {candidate['value']:.0f} percent."

    selected = sorted(selected, key=lambda item: item["value"])
    candidate = selected[len(selected) // 2]
    value = candidate["value"]
    if kind == "temperature":
        unit = candidate["unit"] or "°"
        if unit in ("C", "F"):
            unit = "°" + unit
        reading = f"{value:.0f}{unit}"
    else:
        reading = f"{value:.0f} percent"

    if room_id:
        return f"It's about {reading} in {get_room_label(room_id)}."
    if best_score > 0 and len(selected) == 1:
        return f"{candidate['name']} is about {reading}."
    return f"It's about {reading}."


def _answer_volume(
    t: str,
    *,
    states_snapshot: Optional[list],
    sonos_players: Optional[dict],
    default_sonos_room: Optional[str],
) -> Optional[str]:
    # Match: "what's the volume", "what is the volume", "volume", optionally "... in kitchen"
    if not re.search(r"\bvolume\b", t) and not re.search(r"\bhow\s+loud\b", t):
        return None

    # Require it to look like a question / query, not a command
    # (Keeps us from stealing "set volume to 20" etc.)
    if re.search(r"\b(set|turn|increase|decrease|raise|lower)\b", t):
        return None

    # Extract optional room:
    #  A) "... volume in/on (the) kitchen"
    #  B) "what's the kitchen volume" / "kitchen volume"
    room = None

    # A) volume ... in/on kitchen
    m = re.search(r"\bvolume\b.*\b(?:in|on)\s+(?:the\s+)?([a-z0-9 _-]+)\s*$", t)
    if m:
        room = (m.group(1) or "").strip()
        room = re.sub(r"[^\w\s-]", "", room).strip()

    # B) kitchen volume / what's the kitchen volume
    if not room:
        # Normalize by stripping leading "what's/what is/the" prefixes so we don't capture them as the room.
        tt = t.strip()
        tt = re.sub(r"^what(?:'s| is)\s+", "", tt).strip()
        tt = re.sub(r"^the\s+", "", tt).strip()

        m2 = re.match(r"^(?:the\s+)?([a-z0-9 _-]+?)\s+volume\s*$", tt)
        if m2:
            room = (m2.group(1) or "").strip()
            room = re.sub(r"[^\w\s-]", "", room).strip()

    # Choose target Sonos entity
    eid = None
    if isinstance(sonos_players, dict) and sonos_players:
        def _norm_room(s: str) -> str:
            s = (s or "").strip().lower()
            s = re.sub(r"[^a-z0-9\s-]", "", s)
            s = re.sub(r"\s+", " ", s).strip()
            return s

        want = _norm_room(room) if room else None

        if want:
            # exact key match
            if want in sonos_players:
                eid = sonos_players.get(want)
            else:
                # substring fallback: allow "living room" vs "livingroom" etc
                for k, v in sonos_players.items():
                    if not isinstance(k, str):
                        continue
                    kk = _norm_room(k)
                    if want == kk or want in kk or kk in want:
                        eid = v
                        break

        if not eid:
            dk = _norm_room(default_sonos_room or "")
            if dk and dk in sonos_players:
                eid = sonos_players.get(dk)

        if not eid:
            # deterministic "first" if default missing
            try:
                first_key = sorted([k for k in sonos_players.keys() if isinstance(k, str)])[0]
                eid = sonos_players.get(first_key)
            except Exception:
                eid = None

    if not eid:
        # We recognized the intent, but can't map a speaker
        return "I couldn't figure out which speaker you meant."

    st = _get_state_obj(states_snapshot, eid)
    if not st:
        name = _friendly_name(states_snapshot, eid) or eid
        return f"I couldn't read the volume for {name}."

    attrs = st.get("attributes") or {}
    vl = attrs.get("volume_level", None)
    if vl is None:
        name = _friendly_name(states_snapshot, eid) or eid
        return f"I couldn't read the volume for {name}."

    try:
        pct = int(round(float(vl) * 100.0))
    except Exception:
        pct = None

    # best-effort mute detection (varies by integration)
    muted = attrs.get("is_volume_muted")
    if muted is None:
        muted = attrs.get("volume_muted")
    if muted is None:
        muted = attrs.get("muted")

    if pct is None:
        return "I couldn't read the volume right now."

    if muted is True:
        return f"It's muted at about {pct} percent."
    return f"It's at about {pct} percent."

def handle_state_query_controls(
    text: str,
    *,
    states_snapshot: Optional[list],
    resolve_device_entity: ResolveFn,
    maybe_say=None,
    sonos_players: Optional[dict] = None,
    default_sonos_room: Optional[str] = None,
) -> Optional[str]:
    """Return a state answer when claimed, otherwise ``None`` for dispatch."""
    t = _norm(text)

    # strip leading filler
    t = re.sub(r"^(hey|hi|okay|ok|um|uh|please)\s+", "", t).strip()

    # Aggregate queries go first so phrases such as "are any lights on" never
    # enter the single-device fuzzy resolver.
    out = _answer_all_lights_on_off(t, states_snapshot=states_snapshot)
    if out is not None:
        logging.info("CLAIM: state_query_controls kind=all_lights text=%r -> %r", text, out)
        return out

    out = _answer_lights_on_off_summary(t, states_snapshot=states_snapshot)
    if out is not None:
        logging.info("CLAIM: state_query_controls kind=light_summary text=%r -> %r", text, out)
        return out

    out = _answer_opening_summary(t, states_snapshot=states_snapshot)
    if out is not None:
        logging.info("CLAIM: state_query_controls kind=opening_summary text=%r -> %r", text, out)
        return out

    # Named entity state queries.
    out = _answer_is_on_off(t, states_snapshot=states_snapshot, resolve_device_entity=resolve_device_entity)
    if out is not None:
        logging.info("CLAIM: state_query_controls kind=onoff text=%r -> %r", text, out)
        return out

    out = _answer_is_locked(t, states_snapshot=states_snapshot, resolve_device_entity=resolve_device_entity)
    if out is not None:
        logging.info("CLAIM: state_query_controls kind=lock text=%r -> %r", text, out)
        return out

    out = _answer_is_open_closed(
        t,
        states_snapshot=states_snapshot,
        resolve_device_entity=resolve_device_entity,
    )
    if out is not None:
        logging.info("CLAIM: state_query_controls kind=open_closed text=%r -> %r", text, out)
        return out

    out = _answer_numeric_sensor(t, states_snapshot=states_snapshot)
    if out is not None:
        logging.info("CLAIM: state_query_controls kind=sensor text=%r -> %r", text, out)
        return out

    # Sonos volume; default room if unspecified.
    out = _answer_volume(
        t,
        states_snapshot=states_snapshot,
        sonos_players=sonos_players,
        default_sonos_room=default_sonos_room,
    )
    if out is not None:
        logging.info("CLAIM: state_query_controls kind=volume text=%r -> %r", text, out)
        return out


    return None
