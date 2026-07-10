"""Answer read-only device, room, temperature, and volume state questions.

The handler reads one Home Assistant state snapshot and uses the injected
resolver for named devices. Room-wide light questions enumerate actual entities
registered to the room's Home Assistant area. No service calls occur here, and
unresolved or unavailable state produces an explicit answer or falls through.
"""

from __future__ import annotations
import re
import logging
from typing import Optional, Callable, Tuple, Any, List, Dict

from home_registry import find_room_by_alias, get_room
from ha_client import ha_get_light_entities_for_area

# resolve_device_entity: phrase -> (entity_id, domain) or (entity_id, domain, via...)
ResolveFn = Callable[[str], Optional[Tuple[Any, ...]]]

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[?.!]+$", "", s).strip()
    s = re.sub(r"\s+", " ", s).strip()
    return s

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
    if not st:
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

    if not st:
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
        if eid in state_by_entity:
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

def _answer_temperature(t: str, *, states_snapshot: Optional[list]) -> Optional[str]:
    # "what's the temperature inside" / "what is the temperature"
    if not re.search(r"\btemperature\b", t) and not re.search(r"\btemp\b", t):
        return None
    if not re.search(r"^(what|whats|what's)\b", t) and not t.startswith("is ") and not t.startswith("are "):
        # keep conservative
        pass

    if not states_snapshot:
        return "I couldn't read temperatures right now."

    # pick sensors with device_class temperature OR unit looks like degrees
    candidates = []
    for st in (states_snapshot or []):
        eid = st.get("entity_id") or ""
        if not isinstance(eid, str) or not eid.startswith("sensor."):
            continue
        attrs = st.get("attributes") or {}
        dev_class = str(attrs.get("device_class") or "").lower()
        unit = str(attrs.get("unit_of_measurement") or "")
        if dev_class != "temperature" and "°" not in unit and unit not in ("C", "F"):
            continue
        val = st.get("state")
        try:
            f = float(val)
        except Exception:
            continue
        fn = str(attrs.get("friendly_name") or eid)
        candidates.append((eid, f, unit, fn))

    if not candidates:
        return "I couldn't find any temperature sensors."

    # If user said "inside/indoor", prefer those; else take all.
    prefer_inside = bool(re.search(r"\b(inside|indoor)\b", t))
    if prefer_inside:
        inside = []
        for (eid, f, unit, fn) in candidates:
            hay = (eid + " " + fn).lower()
            if "inside" in hay or "indoor" in hay or "home" in hay:
                inside.append((eid, f, unit, fn))
        if inside:
            candidates = inside

    # Aggregate: median-ish (sort and pick middle)
    candidates_sorted = sorted(candidates, key=lambda x: x[1])
    mid = candidates_sorted[len(candidates_sorted)//2]
    median_val = mid[1]
    unit = mid[2] or "°"

    # Return short answer
    return f"It's about {median_val:.0f}{unit}."


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

    # 1) on/off query
    out = _answer_is_on_off(t, states_snapshot=states_snapshot, resolve_device_entity=resolve_device_entity)
    if out is not None:
        logging.info("CLAIM: state_query_controls kind=onoff text=%r -> %r", text, out)
        return out

    # 2) locked/unlocked query
    out = _answer_is_locked(t, states_snapshot=states_snapshot, resolve_device_entity=resolve_device_entity)
    if out is not None:
        logging.info("CLAIM: state_query_controls kind=lock text=%r -> %r", text, out)
        return out

    # 3) all lights query
    out = _answer_all_lights_on_off(t, states_snapshot=states_snapshot)
    if out is not None:
        logging.info("CLAIM: state_query_controls kind=all_lights text=%r -> %r", text, out)
        return out

    # 4) temperature query (best effort)
    out = _answer_temperature(t, states_snapshot=states_snapshot)
    if out is not None:
        logging.info("CLAIM: state_query_controls kind=temp text=%r -> %r", text, out)
        return out

    # 5) volume query (Sonos; default room if unspecified)
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
