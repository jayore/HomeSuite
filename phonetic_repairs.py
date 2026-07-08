from typing import Dict, Tuple
import re

_PHONETIC_CACHE = {
    "routing": None,
    "device": None,
    "rooms": None,
}


def basic_norm_for_repairs(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("’", "'").replace("‘", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def compile_phonetic_pairs(pairs: dict) -> list:
    compiled = []
    if not isinstance(pairs, dict):
        return compiled
    items = sorted(pairs.items(), key=lambda kv: (-len(str(kv[0]).split()), str(kv[0])))
    for intended, mishears in items:
        if not isinstance(intended, str) or not intended.strip():
            continue
        if not isinstance(mishears, (list, tuple, set)):
            continue
        intended_n = basic_norm_for_repairs(intended)
        for mis in mishears:
            if not isinstance(mis, str) or not mis.strip():
                continue
            mis_n = basic_norm_for_repairs(mis)
            if mis_n in ("^[[A", "^[[B", "^[[C", "^[[D"):
                compiled.append(("LITERAL", mis_n, intended_n))
                continue
            pat = r"(?<!\w)" + re.escape(mis_n).replace(r"\ ", r"\s+") + r"(?!\w)"
            compiled.append((re.compile(pat, re.IGNORECASE), intended_n))
    return compiled


def load_phonetic_cache(*, sonos_players: Dict[str, str]):
    if _PHONETIC_CACHE.get("rooms") is None:
        rooms = []
        try:
            rooms = list((sonos_players or {}).keys())
        except Exception:
            rooms = []
        rooms = [r for r in [basic_norm_for_repairs(x) for x in rooms] if r]
        rooms = sorted(set(rooms), key=lambda x: (-len(x), x))
        _PHONETIC_CACHE["rooms"] = rooms

    if _PHONETIC_CACHE.get("routing") is None or _PHONETIC_CACHE.get("device") is None:
        try:
            from app_config import PHONETIC_ROUTING_REPAIRS, PHONETIC_DEVICE_REPAIRS
        except Exception:
            PHONETIC_ROUTING_REPAIRS = {}
            PHONETIC_DEVICE_REPAIRS = {}
        _PHONETIC_CACHE["routing"] = compile_phonetic_pairs(PHONETIC_ROUTING_REPAIRS)
        _PHONETIC_CACHE["device"] = compile_phonetic_pairs(PHONETIC_DEVICE_REPAIRS)


def contains_known_room(t: str, *, sonos_players: Dict[str, str]) -> bool:
    load_phonetic_cache(sonos_players=sonos_players)
    tl = basic_norm_for_repairs(t)
    for r in (_PHONETIC_CACHE.get("rooms") or []):
        if re.search(r"(?<!\w)" + re.escape(r).replace(r"\ ", r"\s+") + r"(?!\w)", tl):
            return True
    return False


def should_apply_routing_repairs(text: str, *, sonos_players: Dict[str, str]) -> bool:
    tl = basic_norm_for_repairs(text)
    if not tl:
        return False

    try:
        load_phonetic_cache(sonos_players=sonos_players)
        if len(tl.split()) <= 2:
            for entry in (_PHONETIC_CACHE.get("routing") or []):
                if isinstance(entry, tuple) and len(entry) == 3 and entry[0] == "LITERAL":
                    _tag, mis_lit, _intended = entry
                    if tl == (mis_lit or ""):
                        return True
                elif isinstance(entry, tuple) and len(entry) == 2:
                    pat, _intended = entry
                    try:
                        if pat.fullmatch(tl):
                            return True
                    except Exception:
                        pass
    except Exception:
        pass

    if tl in ("^[[A", "^[[B", "^[[C", "^[[D"):
        return True

    if re.fullmatch(r"\x1b\[[abcd]", tl):
        return True

    if re.search(r"\b(pause|resume|play|stop|next|previous|prev|skip|rewind|forward|volume|mute|unmute|brightness|color|kelvin|rgb|lock|unlock|group|ungroup|tv|apple\s*tv|sonos|announce|timer|alarm|__alarm_fire__)\b", tl):
        return True

    if len(tl.split()) <= 4 and contains_known_room(tl, sonos_players=sonos_players):
        return True

    if re.search(r"\b(is|are|was|were|do|does|did)\b", tl) and re.search(r"\b(on|off|locked|unlocked|open|closed|playing|paused|temperature|temp|humidity|battery|status)\b", tl):
        return True

    return False


def apply_phonetic_repairs(text: str, *, kind: str, sonos_players: Dict[str, str]) -> str:
    load_phonetic_cache(sonos_players=sonos_players)
    tl = basic_norm_for_repairs(text)

    if kind == "routing":
        _m = re.fullmatch(r"\x1b\[([abcd])", tl)
        if _m:
            return {"a": "up", "b": "down", "c": "right", "d": "left"}[_m.group(1)]

    compiled = _PHONETIC_CACHE.get(kind) or []
    out = tl
    for entry in compiled:
        if isinstance(entry, tuple) and len(entry) == 3 and entry[0] == "LITERAL":
            _tag, mis_lit, intended = entry
            if out == mis_lit:
                out = intended
            continue
        try:
            pat, repl = entry
            out = pat.sub(repl, out)
        except Exception:
            continue
    out = re.sub(r"\s+", " ", out).strip()
    return out


def apply_phonetic_routing_repairs(text: str, *, sonos_players: Dict[str, str]) -> str:
    return apply_phonetic_repairs(text, kind="routing", sonos_players=sonos_players)


def apply_phonetic_device_repairs(text: str, *, sonos_players: Dict[str, str]) -> str:
    return apply_phonetic_repairs(text, kind="device", sonos_players=sonos_players)


def should_try_device_repairs_pass2(text: str, *, sonos_players: Dict[str, str]) -> Tuple[bool, str]:
    tl = basic_norm_for_repairs(text)
    if not tl:
        return (False, "empty")

    if re.search(r"\b(tell me about|explain|history of|who is|what is|why does|how does|define)\b", tl):
        if not re.search(r"\b(light|lamp|door|lock|thermostat|temperature|tv|apple\s*tv|sonos|volume|brightness|color|scene|script|play|pause|resume|stop|announce|timer|alarm)\b", tl):
            return (False, "chatgptish_opener")

    if re.search(r"\b(turn\s+on|turn\s+off|set|dim|brighten|brightness|volume|mute|unmute|color|kelvin|rgb|lock|unlock|group|ungroup|play|pause|resume|stop|watch|announce)\b", tl):
        return (True, "device_keyword")

    if re.search(r"\b(is|are|was|were|do|does|did)\b", tl) and re.search(r"\b(on|off|locked|unlocked|open|closed|playing|paused|temperature|temp|humidity|battery|status)\b", tl):
        return (True, "status_query")

    if len(tl.split()) <= 4 and contains_known_room(tl, sonos_players=sonos_players):
        return (True, "short_with_room")

    return (False, "no_device_signal")
