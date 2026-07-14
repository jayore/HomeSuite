"""Deterministic device-command routing and shared dispatch state.

``process_device_commands`` applies normalization and bounded repair before
walking the direct handlers for lights, media, rooms, schedules, applets, and
other local integrations. Handlers return response text when they claimed the
request and ``None`` when the next handler may try. A claimed command must
resolve a real configured/Home Assistant entity before performing an action;
the dispatcher must not fabricate a plausible device from uncertain speech.

The module also owns short-lived referent and confirmation state used across
commands. ``main.py`` injects the live Home Assistant service/state callbacks
during startup, while test and REPL runtimes can install safe substitutes.
"""

import os

try:
    from env_compat import install_homesuite_env_aliases
    install_homesuite_env_aliases()
except Exception:
    pass
import re
import logging
import time
import threading
import difflib
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List

from weather_utils import (
    WeatherQuery,
    _ha_daily_forecasts,
    _ha_hourly_forecasts,
    _ha_local_weather,
    _open_meteo_report,
    _open_meteo_weather,
    forecast_days_needed,
    format_forecast_response,
    format_hourly_response,
    parse_weather_query,
)
from location_utils import geocode_location
from location_controls import answer_location_query, parse_location_query
from date_controls import format_date_response, parse_date_query
from normalize_helpers import (
    _looks_like_device_command,
    _parse_number_words,
    _normalize_device_text,
)
from app_config import (
    APPLE_TV_DEFAULT_SKIP_SECONDS,
    ASSISTANT_PROFILE,
    DEFAULT_SONOS_ROOM,
    HOME_LOCATION,
    SONOS_PLAYERS,
)
from room_context import (
    _norm_sonos_room_key,
    _request_room_to_sonos_room,
    _request_default_sonos_room,
    _registry_room_id_from_any,
    _known_room_aliases_for_text,
    _extract_explicit_room_id_from_text,
    _request_default_tv_context,
    _get_last_sonos_master_room,
    _set_last_sonos_master_room,
)
from command_preamble import (
    apply_routing_repairs as _apply_routing_repairs,
    resolve_request_context as _resolve_request_context,
)
from transport_helpers import (
    get_transport_focus as _get_transport_focus,
    set_transport_focus as _set_transport_focus,
    ha_ok as _ha_ok,
    get_state as _get_state,
    is_playing as _is_playing,
    is_activeish as _is_activeish,
    pick_sonos_player as _pick_sonos_player,
    mark_transport as _mark_transport,
    focus_is_valid as _focus_is_valid,
    call_media_transport as _call_media_transport,
    get_state_obj as _get_state_obj,
    get_attr as _get_attr,
    get_state_str as _get_state_str,
    ensure_apple_tv_awake as _ensure_apple_tv_awake,
    maybe_turn_on_tv_scene as _maybe_turn_on_tv_scene,
    ensure_apple_tv_app as _ensure_apple_tv_app,
    get_local_transport_context as _get_local_transport_context,
    decide_local_play_pause_toggle as _decide_local_play_pause_toggle,
)
from phonetic_repairs import (
    should_apply_routing_repairs as _should_apply_routing_repairs,
    apply_phonetic_routing_repairs as _apply_phonetic_routing_repairs,
    apply_phonetic_device_repairs as _apply_phonetic_device_repairs,
    should_try_device_repairs_pass2 as _should_try_device_repairs_pass2,
)
from device_phrase_helpers import (
    sanitize_device_phrase as _sanitize_device_phrase,
    light_entity_id as _light_entity_id,
    resolve_light_target as _resolve_light_target,
    try_light_turn_on as _try_light_turn_on,
    normalize_scene_phrase as _normalize_scene_phrase,
)
from request_context import (
    build_request_context,
    replace_current_request_context,
    set_current_request_context,
    get_active_room_for_request_defaults,
    get_current_source_id,
    get_room_default_for_request,
)
from dialogue_state import (
    forget_referent,
    remember_referent,
    resolve_referent,
    reset_dialogue_state,
    restore_scope as restore_dialogue_scope,
    snapshot_scope as snapshot_dialogue_scope,
)
from home_registry import (
    get_brightness_light_phrase_overrides,
    get_default_room_id,
    get_room,
    get_room_color_light_map,
    find_room_by_alias,
    get_room_spotcast_device_name,
    get_spotcast_device_aliases,
    get_source_room,
    get_source_room_key,
    get_room_label,
    get_source,
    is_source_mobile,
)
from source_room_state import (
    get_current_room as _get_focus_room,
    set_current_room as _set_focus_room,
    clear_current_room as _clear_focus_room,
)
from sonos_utils import sonos_play_media
from color_resolver import resolve_color_description
from volume_controls import handle_volume_controls
from announcement_controls import handle_announcement_controls
from local_say_controls import handle_local_say_controls
from media_referents import rewrite_media_pronoun_command
from brightness_controls import handle_brightness_controls
from color_controls import handle_color_controls
from on_off_controls import (
    handle_on_off_controls,
    handle_toggle_controls,
    supports_binary_action,
    supports_toggle_action,
)
from room_lights_controls import handle_room_lights_controls
from youtube_controls import handle_youtube_controls
from lock_controls import handle_lock_controls
from ha_capability_controls import handle_ha_capability_controls
from state_query_controls import handle_state_query_controls, looks_like_state_query
from alarm_controls import handle_alarm_controls, set_command_executor as set_alarm_command_executor
from schedule_controls import handle_schedule_controls
from solar_utils import resolve_solar_event
from astronomy_controls import handle_astronomy_query, parse_astronomy_query
from stock_quote_controls import handle_stock_quote_query
from homelab_controls import handle_homelab_controls
from now_playing_controls import handle_now_playing_controls
from kelvin_controls import handle_kelvin_controls
from rgb_hex_controls import handle_rgb_hex_controls
from applet_controls import handle_applet_controls
from apple_tv_controls import handle_apple_tv_controls
from plex_controls import handle_plex_controls
from spotify_controls import (
    handle_spotcast_play_controls,
    resolve_typed_play_request,
    handle_spotify_controls,
    resolve_play_request,
)
from sonos_controls import handle_sonos_controls
from sonos_source_controls import handle_sonos_source_controls
from sonos_spotify_browse_controls import handle_sonos_spotify_browse_play
from sonos_my_sonos_controls import handle_sonos_my_sonos_controls
from radio_controls import handle_pinned_radio_controls
from play_by_name_controls import handle_play_by_name_controls
from spotify_resolver import resolve_spotify_description
from plex_resolver import resolve_plex_description
from scene_script_controls import (
    refresh_runnable_cache,
    try_run_runnable_from_text,
    get_runnable_cache_size,
)

# =========================
# CONSTANTS
# =========================

CONTEXT_TTL_SECONDS = 30 * 60

# Spoken confirmations (pref-backed; see app_config). ACTION = device control
# (default off); MEDIA = now-playing content across Plex/YouTube/music (default on).
try:
    from app_config import SPEAK_ACTION_CONFIRMATIONS
except Exception:
    SPEAK_ACTION_CONFIRMATIONS = False
try:
    from app_config import SPEAK_MEDIA_CONFIRMATIONS
except Exception:
    SPEAK_MEDIA_CONFIRMATIONS = True

# Prefer module-based light handlers over legacy inline logic
USE_MODULE_LIGHT_CONTROLS = True

# =========================
# CALLBACKS (assigned by main.py at startup)
# =========================

# Set to the call_ha_service wrapper from main.py before first command.
call_ha_service = None

# Set to ha_get_states from ha_client after main.py configures HA.
ha_get_states = None

# Set to ha_get_state (single-entity fetch) from ha_client after main.py configures HA.
ha_get_state = None

# Set to gpio_ptt.tts_generate_audio after gpio_ptt defines it.
tts_generate_audio = None

# Set to the OpenAI client from gpio_ptt (None if OpenAI unavailable).
OPENAI_CLIENT = None

# Plex/HA credentials — imported from private_config if available.
try:
    from private_config import PLEX_URL, PLEX_TOKEN, HA_URL
except Exception:
    PLEX_URL = None
    PLEX_TOKEN = None
    HA_URL = None

# =========================
# MODULE STATE
# =========================

# Pronoun resolution memory for lights
last_light_entity_id: Optional[str] = None
last_light_updated_ts: float = 0

# Multi-light group tracking (command-ID-based, not time-based)
_current_cmd_id: int = 0
_last_light_group: list = []
_last_light_group_cmd_id: int = -1
_last_light_group_ts: float = 0.0

# Last city/place referenced in a time or weather query. Lets deterministic
# follow-ups like "what time is it there" or "and the weather" resolve
# without escalating to AI. Reset on session timeout.
_last_location_query: Optional[str] = None
_last_location_query_ts: float = 0.0

# Tracks whether a real action (HA / local) actually succeeded this command
_ACTION_OCCURRED: bool = False

# Tracks last spoken response for "say that again"
last_spoken_text: Optional[str] = None

# Last STT normalization output (set for diagnostic logging)
_LAST_STT_NORM_OUT: Optional[str] = None

# Text-facing confirmation context for ppchat / future text clients.
_TEXT_CONFIRM_CONTEXT: Dict[str, Any] = {}

# Passive accelerator for exact entity-name resolution. This is not a source
# of truth: it is derived from the current HA state snapshot and the resolver
# falls back to the old scan/token/fuzzy path whenever it is unavailable.
_RESOLVE_EXACT_INDEX_CACHE: Dict[str, Any] = {
    "ts": 0.0,
    "index": None,
}
_RESOLVE_EXACT_INDEX_TTL_SEC = 10.0
_DISPATCH_TIMING_LOCAL = threading.local()


def _dispatch_timing_enabled() -> bool:
    try:
        return os.getenv("PIPHONE_DISPATCH_TIMING", "slow").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
    except Exception:
        return True


def _dispatch_timing_mode() -> str:
    try:
        raw = os.getenv("PIPHONE_DISPATCH_TIMING", "slow").strip().lower()
    except Exception:
        raw = "slow"
    if raw in ("0", "false", "no", "off"):
        return "off"
    if raw in ("1", "true", "yes", "on", "full", "all"):
        return "full"
    return "slow"


def _dispatch_timing_min_ms() -> int:
    try:
        return max(0, int(os.getenv("PIPHONE_DISPATCH_TIMING_MIN_MS", "250")))
    except Exception:
        return 250


def _dispatch_timing_begin(text: str, repair_pass: int) -> None:
    if not _dispatch_timing_enabled():
        _DISPATCH_TIMING_LOCAL.trace = None
        return
    try:
        parent = getattr(_DISPATCH_TIMING_LOCAL, "trace", None)
        now = time.monotonic()
        _DISPATCH_TIMING_LOCAL.trace = {
            "text": (text or "")[:120],
            "repair_pass": repair_pass,
            "start": now,
            "last": now,
            "phases": [],
            "parent": parent if isinstance(parent, dict) else None,
        }
    except Exception:
        _DISPATCH_TIMING_LOCAL.trace = None


def _dispatch_timing_mark(name: str, **kv) -> None:
    try:
        trace = getattr(_DISPATCH_TIMING_LOCAL, "trace", None)
        if not isinstance(trace, dict):
            return
        now = time.monotonic()
        last = float(trace.get("last") or trace.get("start") or now)
        trace["last"] = now
        parts = [f"{name}={int((now - last) * 1000)}ms"]
        for k, v in kv.items():
            if v is None:
                continue
            parts.append(f"{k}={v}")
        trace.setdefault("phases", []).append(",".join(parts))
    except Exception:
        pass


def _dispatch_timing_end(result, error: Optional[BaseException] = None) -> None:
    try:
        trace = getattr(_DISPATCH_TIMING_LOCAL, "trace", None)
        if not isinstance(trace, dict):
            return
        total_ms = int((time.monotonic() - float(trace.get("start") or time.monotonic())) * 1000)
        phases = " ".join(trace.get("phases") or [])
        handled = result is not None
        if error is not None:
            handled = False
        mode = _dispatch_timing_mode()
        min_ms = _dispatch_timing_min_ms()
        if mode != "full" and error is None and total_ms < min_ms:
            return
        logging.info(
            "DISPATCH_TIMING total=%sms handled=%s pass=%s text=%r phases=%s%s",
            total_ms,
            handled,
            trace.get("repair_pass"),
            trace.get("text"),
            phases,
            f" error={type(error).__name__}" if error is not None else "",
        )
    except Exception:
        pass
    finally:
        try:
            parent = trace.get("parent") if isinstance(trace, dict) else None
            _DISPATCH_TIMING_LOCAL.trace = parent if isinstance(parent, dict) else None
        except Exception:
            pass


def clear_text_confirm_context():
    global _TEXT_CONFIRM_CONTEXT
    _TEXT_CONFIRM_CONTEXT = {}


def set_text_confirm_context(**kwargs):
    global _TEXT_CONFIRM_CONTEXT
    _TEXT_CONFIRM_CONTEXT = dict(kwargs or {})


def get_text_confirm_context() -> Dict[str, Any]:
    try:
        return dict(_TEXT_CONFIRM_CONTEXT or {})
    except Exception:
        return {}


PIPHONE_DEBUG_FOCUS = os.getenv('PIPHONE_DEBUG_FOCUS', '0') == '1'


def _dbg_focus(msg: str):
    if not PIPHONE_DEBUG_FOCUS:
        return
    try:
        logging.info(f"[FOCUSDBG] {msg}")
    except Exception:
        pass
    try:
        print(f"[FOCUSDBG] {msg}")
    except Exception:
        pass


def _ha_headers_safe():
    try:
        import ha_client
        h2 = getattr(ha_client, "HEADERS", None)
        if isinstance(h2, dict) and h2.get("Authorization"):
            return h2
    except Exception:
        pass
    tok = None
    try:
        from private_config import HA_TOKEN as _TOK
        tok = _TOK
    except Exception:
        tok = None
    if tok:
        return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    return None


def _ha_session_safe():
    try:
        import ha_client
        s2 = getattr(ha_client, "HA_SESSION", None)
        if s2 is not None:
            return s2
    except Exception:
        pass
    live = False
    try:
        live = str(os.environ.get("PIPHONE_LIVE", "")).strip() == "1"
    except Exception:
        live = False
    if not live:
        return None
    try:
        import requests
        return requests.Session()
    except Exception:
        return None


def _entity_exists(eid: str, states_snapshot: Optional[list] = None) -> bool:
    states = states_snapshot if states_snapshot is not None else ha_get_states()
    if not states:
        return False
    return any(isinstance(s.get("entity_id"), str) and s["entity_id"] == eid for s in states)


def _get_entity_state(entity_id: str, states_snapshot: Optional[list]) -> Optional[dict]:
    if not states_snapshot:
        return None
    return next((st for st in states_snapshot if st.get("entity_id") == entity_id), None)


def _is_media_active(entity_id: str, states_snapshot: Optional[list]) -> bool:
    st = _get_entity_state(entity_id, states_snapshot)
    if not st:
        return False
    state = st.get("state")
    if state in ("playing", "paused"):
        return True
    attrs = st.get("attributes") or {}
    return bool(attrs.get("media_title") or attrs.get("media_content_id"))


def _set_number_value(entity_id: str, value: int) -> bool:
    pct = max(0, min(100, int(value)))
    return call_ha_service("number/set_value", {
        "entity_id": entity_id,
        "value": pct,
    })


def _now_ts() -> float:
    return time.time()

def mark_action_occurred():
    # For non-HA actions that still represent a real success (e.g., Plex direct HTTP control)
    global _ACTION_OCCURRED
    _ACTION_OCCURRED = True


def _strip_for_tts(s: str) -> str:
    """
    Legacy name kept for the few command-dispatch call sites that use this as
    a light display-text cleanup. Actual speech normalization now happens at
    the TTS boundary in spoken_text.normalize_for_tts().
    """
    if not s:
        return s
    return re.sub(r"\s+", " ", str(s).strip())


def _device_referent_capabilities(domain: str) -> set[str]:
    capabilities = {"device_target"}
    if supports_binary_action(domain):
        capabilities.add("binary_control")
    if supports_toggle_action(domain):
        capabilities.add("toggle_control")
    if domain == "light":
        capabilities.update({"light_target", "brightness_control", "color_control"})
    elif domain == "lock":
        capabilities.add("lock_control")
    elif domain == "cover":
        capabilities.add("cover_control")
    elif domain == "media_player":
        capabilities.update({"media_target", "volume_control"})
    return capabilities


def _remember_device_referent(
    entity_id: str,
    domain: Optional[str] = None,
    *,
    source: str = "deterministic",
) -> None:
    eid = str(entity_id or "").strip()
    if "." not in eid:
        return
    actual_domain = eid.split(".", 1)[0].strip().lower()
    requested_domain = str(domain or actual_domain).strip().lower()
    if requested_domain != actual_domain:
        logging.warning(
            "DEVICE_REFERENT_DOMAIN_MISMATCH entity_id=%r requested=%r actual=%r",
            eid,
            requested_domain,
            actual_domain,
        )
    remember_referent(
        "device",
        eid,
        label=eid.split(".", 1)[-1].replace("_", " "),
        capabilities=_device_referent_capabilities(actual_domain),
        data={"entity_id": eid, "domain": actual_domain},
        source=source,
    )


def _remember_resolved_entity(
    entity_id: str,
    domain: str,
    *,
    source: str = "deterministic",
) -> None:
    eid = str(entity_id or "").strip()
    if "." not in eid:
        return
    actual_domain = eid.partition(".")[0].strip().lower()
    if actual_domain == "light":
        _remember_light(eid, source=source)
        return
    _remember_device_referent(eid, domain, source=source)


def _remember_light(eid: str, *, source: str = "deterministic"):
    global last_light_entity_id, last_light_updated_ts
    global _last_light_group, _last_light_group_cmd_id, _last_light_group_ts
    now = _now_ts()
    last_light_entity_id = eid
    last_light_updated_ts = now
    # Group lights by command ID — no timer, semantically correct
    if _last_light_group_cmd_id != _current_cmd_id:
        # First light set in this command — start a new group
        _last_light_group = [eid]
        _last_light_group_cmd_id = _current_cmd_id
    else:
        # Additional light in the same command — accumulate
        if eid not in _last_light_group:
            _last_light_group.append(eid)
    _last_light_group_ts = now

    remember_referent(
        "light",
        eid,
        label=eid.split(".", 1)[-1].replace("_", " "),
        capabilities={"light_target"},
        data={"entity_id": eid},
        ttl_seconds=CONTEXT_TTL_SECONDS,
        source=source,
    )
    remember_referent(
        "light_group",
        ",".join(_last_light_group),
        label="recent lights",
        capabilities={"light_group_target"},
        data={
            "entity_ids": list(_last_light_group),
            "command_id": _current_cmd_id,
        },
        ttl_seconds=CONTEXT_TTL_SECONDS,
        source=source,
    )
    _remember_device_referent(eid, "light", source=source)

def _get_recent_light() -> Optional[str]:
    ref = resolve_referent(
        kinds={"light"},
        capability="light_target",
        max_age_seconds=CONTEXT_TTL_SECONDS,
    )
    return str(ref.get("key")) if ref else None

def _get_recent_lights() -> list:
    """Return the light group from the most recently COMPLETED command.
    Returns empty list if called during the command that set the lights
    (i.e. cmd_id already incremented past the group's cmd_id)."""
    ref = resolve_referent(
        kinds={"light_group"},
        capability="light_group_target",
        max_age_seconds=CONTEXT_TTL_SECONDS,
    )
    if not ref:
        return []
    data = ref.get("data") or {}
    entity_ids = list(data.get("entity_ids") or [])
    # Only return the group if it came from a *previous* command —
    # at expansion time the current command hasn't set any lights yet.
    if data.get("command_id") == _current_cmd_id:
        return []
    return entity_ids

def _maybe_normalize_for_device_pipeline(text: str) -> str:
    if not _looks_like_device_command(text):
        return text
    # STT DEBUG: raw -> normalized
    if (os.getenv('PIPHONE_DEBUG_STT','0').strip() == '1'):
        logging.info('STT_NORM_IN: %r', text)
    out = _normalize_device_text(text)
    if (os.getenv('PIPHONE_DEBUG_STT','0').strip() == '1'):
        logging.info('STT_NORM_OUT: %r', out)
        globals()["_LAST_STT_NORM_OUT"] = out
    return out


def _resolve_exact_index_enabled() -> bool:
    try:
        raw = os.getenv("PIPHONE_RESOLVE_EXACT_INDEX")
        if raw is not None:
            return raw.strip().lower() not in ("0", "false", "no", "off")
    except Exception:
        pass

    try:
        from app_config import USE_RESOLVE_EXACT_INDEX
        return bool(USE_RESOLVE_EXACT_INDEX)
    except Exception:
        return True


def _resolve_surface_key(value: str) -> str:
    """Cheap canonical-name normalizer for HA friendly names and object IDs.

    User phrases still go through _sanitize_device_phrase(), including phonetic
    repairs. HA-provided names are already canonical, so applying the repair
    table to every entity surface just burns CPU during resolver scans/indexing.
    """
    s = (value or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"\b(please|thanks|thank you)\b", "", s).strip()
    s = re.sub(r"[^\w\s]", "", s).strip()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _resolve_norm_object_id(eid: str) -> str:
    try:
        if "." not in eid:
            return ""
        return _resolve_surface_key(eid.split(".", 1)[1].replace("_", " "))
    except Exception:
        return ""


def _resolve_candidate_score(norm_phrase: str, eid: str, domain: str, st: dict, matched_on: str) -> tuple:
    state = st.get("state")
    attrs = st.get("attributes") or {}
    fn = attrs.get("friendly_name") or ""
    norm_fn = _resolve_surface_key(fn) if fn else ""
    norm_oid = _resolve_norm_object_id(eid)

    unavailable_penalty = 1000 if state in ("unavailable", "unknown", None) else 0

    # Prefer exact match on whichever surface matched, then exact on the other surface.
    if matched_on == "fn":
        exact_penalty = 0 if norm_fn == norm_phrase else 50
    else:
        exact_penalty = 0 if norm_oid == norm_phrase else 50

    bad_tokens = (
        "flicker",
        "underwater",
        "scene trigger",
        "scene_trigger",
        "trigger",
        "effect",
        "preset",
    )
    hay = f"{eid} {fn}".lower()
    bad_penalty = 200 if any(bt in hay for bt in bad_tokens) else 0

    # Preserve the existing domain preference used by the resolver scan.
    domain_penalty = 0 if domain == "light" else (10 if domain == "switch" else 25)
    length_penalty = min(len(eid), 200) / 10.0

    return (unavailable_penalty + exact_penalty + bad_penalty + domain_penalty, length_penalty, eid)


def _resolve_exact_index_get(states_snapshot: Optional[list]) -> Optional[dict]:
    if not _resolve_exact_index_enabled():
        return None
    if not states_snapshot:
        return None

    try:
        import time as _time
        now = _time.monotonic()
        cached = _RESOLVE_EXACT_INDEX_CACHE.get("index")
        ts = float(_RESOLVE_EXACT_INDEX_CACHE.get("ts") or 0.0)
        if isinstance(cached, dict) and (now - ts) <= _RESOLVE_EXACT_INDEX_TTL_SEC:
            return cached

        index: Dict[str, list] = {}
        for st in states_snapshot:
            if not isinstance(st, dict):
                continue
            eid = st.get("entity_id", "")
            if not isinstance(eid, str) or "." not in eid:
                continue
            domain, _object_id = eid.split(".", 1)
            attrs = st.get("attributes") or {}
            fn = attrs.get("friendly_name") or ""

            norm_fn = _resolve_surface_key(fn) if fn else ""
            norm_oid = _resolve_norm_object_id(eid)

            if norm_fn:
                index.setdefault(norm_fn, []).append((eid, domain, st, "fn"))
            if norm_oid:
                index.setdefault(norm_oid, []).append((eid, domain, st, "oid"))

        _RESOLVE_EXACT_INDEX_CACHE["ts"] = now
        _RESOLVE_EXACT_INDEX_CACHE["index"] = index
        try:
            logging.info("RESOLVE_INDEX_REBUILT keys=%s states=%s", len(index), len(states_snapshot))
        except Exception:
            pass
        return index
    except Exception:
        logging.exception("RESOLVE_INDEX_BUILD_FAIL")
        return None


def _resolve_from_exact_index(norm_phrase: str, states_snapshot: Optional[list]) -> Optional[tuple]:
    try:
        index = _resolve_exact_index_get(states_snapshot)
        if not index:
            return None
        candidates = index.get(norm_phrase)
        if not candidates:
            return None

        best = min(
            candidates,
            key=lambda x: _resolve_candidate_score(norm_phrase, x[0], x[1], x[2], x[3]),
        )
        eid, domain, _st, matched_on = best
        via = f"index_{matched_on}"
        try:
            logging.info("RESOLVE_INDEX_HIT phrase=%r -> %s (domain=%s via=%s)", norm_phrase, eid, domain, via)
        except Exception:
            pass
        return (eid, domain, {"via": via, "phrase": norm_phrase})
    except Exception:
        logging.exception("RESOLVE_INDEX_LOOKUP_FAIL phrase=%r", norm_phrase)
        return None


def _resolve_device_entity(phrase: str, states_snapshot: Optional[list]) -> Optional[tuple]:
    """Returns (entity_id, domain) for the best match to the phrase."""
    if not phrase:
        return None
    norm_phrase = _sanitize_device_phrase(phrase)

    # -----------------------------
    # 0) Exact device alias mapping (app_config.HA_DEVICE_ALIASES)
    # -----------------------------
    # If user phrase matches any configured alias, return that entity immediately.
    # This lets all downstream modules (on/off, color, brightness, etc.) work with human-friendly names.
    try:
        from app_config import HA_DEVICE_ALIASES
    except Exception:
        HA_DEVICE_ALIASES = {}

    try:
        want = _sanitize_device_phrase(phrase, logger=logging)
        if want and isinstance(HA_DEVICE_ALIASES, dict):
            for aeid, aliases in (HA_DEVICE_ALIASES or {}).items():
                if not isinstance(aeid, str) or "." not in aeid:
                    continue
                adomain = aeid.split(".", 1)[0]
                cand_list = []
                if isinstance(aliases, (list, tuple, set)):
                    cand_list.extend(list(aliases))
                # Also allow the entity_id suffix itself (underscores -> spaces) as an implicit alias
                try:
                    cand_list.append(aeid.split(".", 1)[1].replace("_", " "))
                except Exception:
                    pass
                for a in cand_list:
                    if not isinstance(a, str):
                        continue
                    if _sanitize_device_phrase(a, logger=logging) == want:
                        logging.info(f"ALIAS_RESOLVE: {phrase!r} -> {aeid}")
                        return (aeid, adomain, {"via": "alias", "alias": want})
    except Exception as e:
        logging.error(f"Alias resolve error: {e}")

    if not states_snapshot:
        return None

    indexed = _resolve_from_exact_index(norm_phrase, states_snapshot)
    if indexed is not None:
        return indexed

    # -----------------------------
    # Helpers
    # -----------------------------
    def _norm_object_id(eid: str) -> str:
        return _resolve_norm_object_id(eid)

    def _token_set(s: str) -> set:
        s = _sanitize_device_phrase(s, logger=logging)
        if not s:
            return set()
        return {t for t in s.split() if t}

    want_tokens = _token_set(norm_phrase)

    BAD_TOKENS = (
        "flicker",
        "underwater",
        "scene trigger",
        "scene_trigger",
        "trigger",
        "effect",
        "preset",
    )

    # -----------------------------
    # 1) Candidate gather: friendly_name OR object_id
    # -----------------------------
    candidates = []
    for st in states_snapshot:
        eid = st.get("entity_id", "")
        if "." not in eid:
            continue
        domain, _object_id = eid.split(".", 1)
        attrs = st.get("attributes") or {}
        fn = attrs.get("friendly_name") or ""
        norm_fn = _resolve_surface_key(fn) if fn else ""
        norm_oid = _norm_object_id(eid)

        # Skip entities with no meaningful name surface
        if not norm_fn and not norm_oid:
            continue

        # Exact / substring against either surface
        if norm_fn and (norm_fn == norm_phrase or norm_phrase in norm_fn):
            candidates.append((eid, domain, st, "fn"))
        elif norm_oid and (norm_oid == norm_phrase or norm_phrase in norm_oid):
            candidates.append((eid, domain, st, "oid"))

    # -----------------------------
    # 2) Deterministic scoring (lower is better)
    # -----------------------------
    def _score(eid: str, domain: str, st: dict, matched_on: str) -> tuple:
        return _resolve_candidate_score(norm_phrase, eid, domain, st, matched_on)

    if candidates:
        best = min(candidates, key=lambda x: _score(x[0], x[1], x[2], x[3]))
        try:
            logging.info("RESOLVE_MATCH: phrase=%r -> %s (domain=%s via=%s)", phrase, best[0], best[1], best[3])
        except Exception:
            pass
        return (best[0], best[1], {"via": best[3], "phrase": norm_phrase})

    # -----------------------------
    # 3) Token-overlap fallback (conservative, extendable)
    # -----------------------------
    # Helps when STT is close but not substring-equal.
    try:
        best = None
        best_tuple = None

        for st in states_snapshot:
            eid = st.get("entity_id", "")
            if "." not in eid:
                continue
            domain, _ = eid.split(".", 1)

            attrs = st.get("attributes") or {}
            fn = attrs.get("friendly_name") or ""
            norm_fn2 = _resolve_surface_key(fn) if fn else ""
            norm_oid2 = _norm_object_id(eid)
            if not norm_fn2 and not norm_oid2:
                continue

            hay = f"{eid} {fn}".lower()
            if any(bt in hay for bt in BAD_TOKENS):
                continue

            toks = _token_set(norm_fn2) | _token_set(norm_oid2)
            if not toks or not want_tokens:
                continue

            overlap = len(want_tokens & toks)
            if overlap <= 0:
                continue

            # Higher overlap is better; break ties with a deterministic penalty set
            domain_bonus = 2 if domain == "light" else (1 if domain == "switch" else 0)
            score_tuple = (-(overlap), -domain_bonus, len(eid))

            if best is None or score_tuple < best_tuple:
                best = (eid, domain, overlap)
                best_tuple = score_tuple

        if best and best[2] >= 2:
            logging.info("TOKEN_RESOLVE: %r -> %s (overlap=%s)", phrase, best[0], best[2])
            return (best[0], best[1], {"via": "token_overlap", "overlap": best[2], "phrase": norm_phrase})
    except Exception:
        pass

    # -----------------------------
    # 4) Fuzzy fallback (SequenceMatcher) against friendly_name + object_id
    # -----------------------------
    try:
        BAD_TOKENS2 = ("flicker", "underwater", "scene trigger", "scene_trigger", "trigger", "effect", "preset")
        best_eid = None
        best_domain = None
        best_score = 0.0

        for st in states_snapshot:
            eid = st.get("entity_id", "")
            if "." not in eid:
                continue
            domain, _ = eid.split(".", 1)
            if domain not in ("light", "switch"):
                continue

            attrs = st.get("attributes") or {}
            fn = attrs.get("friendly_name") or ""
            norm_fn2 = _resolve_surface_key(fn) if fn else ""
            norm_oid2 = _norm_object_id(eid)
            if not norm_fn2 and not norm_oid2:
                continue

            hay = f"{eid} {fn}".lower()
            if any(bt in hay for bt in BAD_TOKENS2):
                continue

            cand_scores = []
            if norm_fn2:
                cand_scores.append(difflib.SequenceMatcher(a=norm_phrase, b=norm_fn2).ratio())
            if norm_oid2:
                cand_scores.append(difflib.SequenceMatcher(a=norm_phrase, b=norm_oid2).ratio())
            score = max(cand_scores) if cand_scores else 0.0

            if score > best_score:
                best_score = score
                best_eid = eid
                best_domain = domain

        if best_eid and best_domain and best_score >= 0.82:
            logging.info(f"FUZZY_RESOLVE: {phrase!r} -> {best_eid} (score={best_score:.3f})")
            return (best_eid, best_domain, {"via": "fuzzy", "score": round(best_score, 3), "phrase": norm_phrase})
    except Exception:
        pass

    return None

# Per-phrase resolver cache. Each utterance traverses 4+ handlers (lights,
# scenes, scripts, media, etc.) and each handler independently calls the
# resolver on the same phrase. Without caching, a failed match costs ~4 x
# 0.7s = ~3s of redundant fuzzy-match work against the HA entity list,
# making the failure path feel sluggish before the error tone plays.
# Cache by (phrase, snapshot_id) with a short TTL so stale HA state can't
# leak across utterances; snapshot identity changes between utterances
# because each process_audio() builds a fresh snapshot.
_RESOLVE_CACHE: dict = {}
_RESOLVE_CACHE_TTL_SEC = 2.0


def _resolve_cache_get(phrase: str, states_snapshot):
    # Cache key is phrase-only on purpose: different command handlers
    # build their own states_snapshot lambdas inside process_audio(), so
    # snapshot identity varies even within a single utterance. The 2s TTL
    # is short enough that any HA state change in flight will be picked up
    # on the next utterance, but long enough to cover the multi-handler
    # walk inside one utterance.
    try:
        import time as _time
        key = phrase
        hit = _RESOLVE_CACHE.get(key)
        if hit is None:
            return (False, None)
        ts, value = hit
        if (_time.monotonic() - ts) > _RESOLVE_CACHE_TTL_SEC:
            _RESOLVE_CACHE.pop(key, None)
            return (False, None)
        return (True, value)
    except Exception:
        return (False, None)


def _resolve_cache_put(phrase: str, states_snapshot, value):
    try:
        import time as _time
        key = phrase
        _RESOLVE_CACHE[key] = (_time.monotonic(), value)
        # Cheap bound: drop oldest if cache grows past 64 entries
        if len(_RESOLVE_CACHE) > 64:
            try:
                oldest = min(_RESOLVE_CACHE.items(), key=lambda kv: kv[1][0])[0]
                _RESOLVE_CACHE.pop(oldest, None)
            except Exception:
                pass
    except Exception:
        pass


def _resolve_device_entity_trace(phrase: str, states_snapshot):
    """Wrapper around _resolve_device_entity that logs phrase -> resolved entity."""
    try:
        import time as _time
        _t0 = _time.monotonic()
    except Exception:
        _t0 = None

    # Per-utterance cache: 4+ handlers in one utterance all call this for
    # the same phrase. Caching collapses the repeat cost from ~0.7s to ~0.
    cache_hit, cached_value = _resolve_cache_get(phrase, states_snapshot)
    if cache_hit:
        try:
            logging.info("RESOLVE_IN phrase=%r (cached)", phrase)
            if cached_value is None:
                logging.info("RESOLVE_OUT phrase=%r -> None (cached) dt=0.0000", phrase)
        except Exception:
            pass
        if cached_value is None:
            return None
        # Fall through to the rich logging path below with cached value
        r = cached_value
        try:
            logging.info("RESOLVE_OUT phrase=%r -> %s (cached)", phrase, cached_value[0] if isinstance(cached_value, (tuple, list)) and cached_value else cached_value)
        except Exception:
            pass
        return r

    try:
        logging.info("RESOLVE_IN phrase=%r", phrase)
    except Exception:
        pass

    r = _resolve_device_entity(phrase, states_snapshot)
    _resolve_cache_put(phrase, states_snapshot, r)

    try:
        dt = None
        if _t0 is not None:
            import time as _time
            dt = round(_time.monotonic() - _t0, 4)
    except Exception:
        dt = None

    try:
        if r is None:
            if dt is not None:
                logging.info("RESOLVE_OUT phrase=%r -> None dt=%.4f", phrase, dt)
            else:
                logging.info("RESOLVE_OUT phrase=%r -> None", phrase)
        else:
            entity_id = None
            domain = None
            via = None
            try:
                if isinstance(r, (tuple, list)):
                    if len(r) >= 1:
                        entity_id = r[0]
                    if len(r) >= 2:
                        domain = r[1]
                    if len(r) >= 3:
                        via = r[2]
                    if isinstance(via, dict):
                        # Keep rich meta internally, but emit a compact via string for RESOLVE_OUT logs.
                        m = dict(via)
                        m.setdefault('token', phrase)
                        try:
                            tok = m.get('token')
                            phr = m.get('phrase')
                            if tok and phr and tok != phr and m.get('via') == 'override':
                                m['via'] = 'phonetic'
                                m.setdefault('from', tok)
                                m.setdefault('to', phr)
                        except Exception:
                            pass

                        # Build short string (example: phonetic:dynamite→dining light)
                        try:
                            vkind = m.get('via') or 'meta'
                            v_from = m.get('from') or m.get('token')
                            v_to = m.get('to') or m.get('phrase')
                            if v_from and v_to and v_from != v_to:
                                via = f"{vkind}:{v_from}→{v_to}"
                            elif v_to:
                                via = f"{vkind}:{v_to}"
                            elif v_from:
                                via = f"{vkind}:{v_from}"
                            else:
                                via = str(m)
                        except Exception:
                            via = str(m)
            except Exception:
                pass
            if dt is not None:
                logging.info(
                    "RESOLVE_OUT phrase=%r -> entity=%r domain=%r via=%r dt=%.4f",
                    phrase, entity_id, domain, via, dt
                )
            else:
                logging.info(
                    "RESOLVE_OUT phrase=%r -> entity=%r domain=%r via=%r",
                    phrase, entity_id, domain, via
                )
    except Exception:
        pass
    # Keep handler compatibility: callers expect (entity_id, domain)
    try:
        if isinstance(r, (tuple, list)) and len(r) >= 2:
            return (r[0], r[1])
    except Exception:
        pass
    return r


_SINGULAR_DEVICE_PRONOUNS = {
    "it",
    "that",
    "this",
    "that one",
    "this one",
    "that device",
    "this device",
}


def _resolve_device_entity_with_context(
    phrase: str,
    states_snapshot,
    *,
    capability: str,
):
    """Resolve explicit text normally or a pronoun through typed dialogue state."""
    normalized = re.sub(r"[^a-z0-9\s]+", " ", str(phrase or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized not in _SINGULAR_DEVICE_PRONOUNS:
        return _resolve_device_entity_trace(phrase, states_snapshot)

    ref = resolve_referent(
        kinds={"device"},
        capability=capability,
    )
    if not ref:
        logging.info(
            "DEVICE_REFERENT_MISS pronoun=%r capability=%s",
            phrase,
            capability,
        )
        return None

    data = ref.get("data") or {}
    entity_id = str(data.get("entity_id") or ref.get("key") or "").strip()
    stored_domain = str(data.get("domain") or "").strip().lower()
    actual_domain = entity_id.partition(".")[0].strip().lower()
    state_exists = any(
        isinstance(state, dict) and state.get("entity_id") == entity_id
        for state in (states_snapshot or [])
    )
    capabilities = _device_referent_capabilities(actual_domain)
    if (
        "." not in entity_id
        or not state_exists
        or (stored_domain and stored_domain != actual_domain)
        or capability not in capabilities
    ):
        logging.warning(
            "DEVICE_REFERENT_INVALID key=%r stored_domain=%r actual_domain=%r "
            "capability=%r state_exists=%s",
            entity_id,
            stored_domain,
            actual_domain,
            capability,
            state_exists,
        )
        forget_referent("device", key=str(ref.get("key") or ""))
        return None

    logging.info(
        "DEVICE_REFERENT_RESOLVE pronoun=%r capability=%s entity_id=%s domain=%s",
        phrase,
        capability,
        entity_id,
        actual_domain,
    )
    return (entity_id, actual_domain)

# =========================
# TIME + WEATHER
# =========================

def _format_local_time(dt: Optional[datetime] = None) -> str:
    if dt is None:
        dt = datetime.now()
    try:
        return dt.strftime("It's %-I:%M %p.")
    except Exception:
        return ("It's " + dt.strftime("%I:%M %p").lstrip("0") + ".")

def _remember_location(location: str) -> None:
    """Record the most recently queried city/place for follow-up resolution."""
    global _last_location_query, _last_location_query_ts
    cleaned = (location or "").strip()
    if not cleaned:
        return
    _last_location_query = cleaned
    _last_location_query_ts = _now_ts()
    remember_referent(
        "location",
        cleaned,
        label=cleaned,
        capabilities={"location"},
        data={"location": cleaned},
        ttl_seconds=CONTEXT_TTL_SECONDS,
    )


def _recall_location() -> Optional[str]:
    """Return the last queried location if still within TTL, else None."""
    ref = resolve_referent(
        kinds={"location"},
        capability="location",
        max_age_seconds=CONTEXT_TTL_SECONDS,
    )
    return str(ref.get("key")) if ref else None


_LOCATION_PRONOUN_RE = re.compile(r"\b(?:there|over there|in there)\b", re.IGNORECASE)
_LOCATION_DISTANCE_ORIGIN_TTL_SECONDS = 2 * 60


def _resolve_location_pronoun(text: str) -> Optional[str]:
    """If text references a place pronoun ('there'), substitute the last
    remembered location. Returns the resolved location or None if nothing
    to substitute."""
    if not text or not _LOCATION_PRONOUN_RE.search(text):
        return None
    return _recall_location()


def _remember_pending_location_origin(destination: str) -> None:
    """Keep a source-scoped destination while asking for its origin."""
    cleaned = str(destination or "").strip()
    if not cleaned:
        return
    remember_referent(
        "location_distance_origin",
        cleaned,
        label=cleaned,
        capabilities={"supply_location_origin"},
        data={"destination": cleaned},
        ttl_seconds=_LOCATION_DISTANCE_ORIGIN_TTL_SECONDS,
    )


def _recall_pending_location_origin() -> Optional[str]:
    ref = resolve_referent(
        kinds={"location_distance_origin"},
        capability="supply_location_origin",
        max_age_seconds=_LOCATION_DISTANCE_ORIGIN_TTL_SECONDS,
    )
    if not ref:
        return None
    return str((ref.get("data") or {}).get("destination") or ref.get("key") or "").strip() or None


def _request_source_is_fixed() -> Optional[bool]:
    """Return True/False only when source mobility is explicitly configured."""
    source = get_source(get_current_source_id())
    if not isinstance(source, dict) or "mobile" not in source:
        return None
    return not bool(source.get("mobile"))


def handle_time_query(location: Optional[str]) -> Optional[str]:
    """
    - "what time is it" -> local
    - "what time is it in tokyo" -> location time (and dedup display)
    """
    if not location:
        return _format_local_time()

    geo = geocode_location(location)
    if not geo:
        return f"I couldn't find {location}."

    _remember_location(location)

    tz = geo.get("timezone")
    if tz:
        try:
            # zoneinfo exists on Python 3.9+
            from zoneinfo import ZoneInfo
            dt = datetime.now(ZoneInfo(tz))
            disp = _strip_for_tts(geo["display"])
            return f"{disp}. {_format_local_time(dt)}"
        except Exception:
            pass

    disp = _strip_for_tts(geo["display"])
    return f"{disp}. {_format_local_time()}"


def handle_date_query(location: Optional[str]) -> str:
    """Answer today's date locally or in a geocoded timezone."""
    if not location:
        return format_date_response()

    geo = geocode_location(location)
    if not geo:
        return f"I couldn't find {location}."

    _remember_location(location)

    tz = geo.get("timezone")
    if tz:
        try:
            from zoneinfo import ZoneInfo

            dt = datetime.now(ZoneInfo(tz))
            disp = _strip_for_tts(geo["display"])
            return f"{disp}. {format_date_response(dt)}"
        except Exception:
            pass

    disp = _strip_for_tts(geo["display"])
    return f"{disp}. {format_date_response()}"

def _home_weather_coordinates() -> Optional[Tuple[float, float]]:
    try:
        return (
            float((HOME_LOCATION or {}).get("latitude")),
            float((HOME_LOCATION or {}).get("longitude")),
        )
    except (TypeError, ValueError):
        return None


def handle_weather_query(
    location: Optional[str],
    *,
    query: Optional[WeatherQuery] = None,
    states_snapshot: Optional[list] = None,
) -> Optional[str]:
    """
    Current local conditions prefer HA; future local forecasts prefer HA's
    response-producing weather action. Open-Meteo handles named places and is
    the fallback when HA lacks the requested local forecast horizon.
    """
    query = query or WeatherQuery(location=location)
    location = (location or query.location or "").strip() or None

    if query.mode == "current" and not location:
        haw = _ha_local_weather(states_snapshot)
        if haw:
            return haw
        coordinates = _home_weather_coordinates()
        if not coordinates:
            return "Local weather isn't configured yet."
        return _open_meteo_weather(*coordinates) or "I couldn't get the weather right now."

    if query.mode == "current":
        geo = geocode_location(location)
        if not geo:
            return f"I couldn't find {location}."
        response = _open_meteo_weather(geo["lat"], geo["lon"])
        if not response:
            return f"I couldn't get the weather for {location}."
        _remember_location(location)
        return f"{_strip_for_tts(geo['display'])}. {response}"

    if query.mode == "hourly":
        if location:
            geo = geocode_location(location)
            if not geo:
                return f"I couldn't find {location}."
            timezone_name = geo.get("timezone")
            report = _open_meteo_report(
                geo["lat"],
                geo["lon"],
                forecast_days=forecast_days_needed(query, timezone_name=timezone_name),
            )
            response = (
                format_hourly_response(
                    query,
                    report.hourly,
                    timezone_name=(report.timezone or timezone_name),
                )
                if report
                else None
            )
            if not response and report and query.period == "tonight":
                response = format_forecast_response(
                    query,
                    report.daily,
                    timezone_name=(report.timezone or timezone_name),
                )
            if not response:
                return f"I couldn't get the hourly forecast for {location}."
            _remember_location(location)
            return f"{_strip_for_tts(geo['display'])}. {response}"

        hourly = _ha_hourly_forecasts(states_snapshot) or []
        response = format_hourly_response(query, hourly)
        if response:
            return response

        coordinates = _home_weather_coordinates()
        report = None
        if coordinates:
            report = _open_meteo_report(
                *coordinates,
                forecast_days=forecast_days_needed(query),
            )
            response = (
                format_hourly_response(
                    query,
                    report.hourly,
                    timezone_name=report.timezone,
                )
                if report
                else None
            )
            if response:
                return response

        if query.period == "tonight":
            daily = _ha_daily_forecasts(states_snapshot) or []
            response = format_forecast_response(query, daily)
            if not response and report:
                response = format_forecast_response(
                    query,
                    report.daily,
                    timezone_name=report.timezone,
                )
            if response:
                return response
        return "I couldn't get the hourly forecast right now."

    if location:
        geo = geocode_location(location)
        if not geo:
            return f"I couldn't find {location}."
        timezone_name = geo.get("timezone")
        report = _open_meteo_report(
            geo["lat"],
            geo["lon"],
            forecast_days=forecast_days_needed(query, timezone_name=timezone_name),
        )
        response = (
            format_forecast_response(
                query,
                report.daily,
                timezone_name=(report.timezone or timezone_name),
            )
            if report
            else None
        )
        if not response:
            return f"I couldn't get the forecast for {location}."
        _remember_location(location)
        return f"{_strip_for_tts(geo['display'])}. {response}"

    # Preserve a partial HA range as a final fallback, but first try to fulfill
    # the entire request through configured coordinates.
    ha_forecasts = _ha_daily_forecasts(states_snapshot) or []
    response = format_forecast_response(query, ha_forecasts)
    if response:
        return response
    partial_ha_response = format_forecast_response(
        query,
        ha_forecasts,
        allow_partial=True,
    )

    coordinates = _home_weather_coordinates()
    if coordinates:
        report = _open_meteo_report(
            *coordinates,
            forecast_days=forecast_days_needed(query),
        )
        response = (
            format_forecast_response(
                query,
                report.daily,
                timezone_name=report.timezone,
            )
            if report
            else None
        )
        if response:
            return response
    if partial_ha_response:
        return partial_ha_response
    return "I couldn't get the forecast right now."

# =========================
# COMMAND PARSING
# =========================

def _maybe_say(text_if_enabled: str) -> Optional[str]:
    return text_if_enabled if SPEAK_ACTION_CONFIRMATIONS and text_if_enabled else ""


def _maybe_say_media(text_if_enabled: str) -> Optional[str]:
    """Confirmation for now-playing media (Plex/YouTube/music), gated by
    SPEAK_MEDIA_CONFIRMATIONS (separate from device-action confirmations)."""
    return text_if_enabled if SPEAK_MEDIA_CONFIRMATIONS and text_if_enabled else ""


def _now_playing_with_pause(
    *,
    tl: str,
    states_snapshot: Optional[list],
    sonos_players: Dict[str, str],
    default_sonos_room: str,
    apple_tv_entity: str,
) -> Optional[str]:
    """
    Minimal, low-risk now-playing wrapper:
    - Only triggers for now-playing queries.
    - Uses focus if available.
    - Reports paused content (previous regression: paused returned nothing).
    - Does NOT perform HA actions; reads states_snapshot only.
    """
    t = (tl or "").strip().lower()
    if not re.search(r"\b(what's playing|what is playing|what song is this|what track is this|now playing)\b", t):
        return None

    if not states_snapshot:
        return "I can't reach Home Assistant right now."

    def _state(eid: str):
        return next((s for s in states_snapshot if s.get("entity_id") == eid), None)

    def _norm_room(s: str) -> str:
        s = (s or "").strip().lower()
        s = re.sub(r"\s+", " ", s)
        return s

    def _find_room_in_text(tl2: str):
        # Prefer longest match first ("living room" before "room")
        for room in sorted(sonos_players.keys(), key=len, reverse=True):
            if re.search(rf"\b{re.escape(room)}\b", tl2):
                return room
        return None

    def _sonos_phrase(eid: str) -> Optional[str]:
        st = _state(eid)
        if not st:
            return None
        state = (st.get("state") or "").strip().lower()
        attrs = st.get("attributes") or {}
        title = (attrs.get("media_title") or "").strip()
        artist = (attrs.get("media_artist") or "").strip()

        # Sonos soundbar often reports TV/line-in as a placeholder title when TV audio is active.
        # In that case, Sonos is not the authoritative metadata source; defer to the TV/Plex path.
        placeholder_titles = {
            "tv", "television",
            "line in", "line-in", "linein",
            "aux", "hdmi", "hdmi arc", "arc",
        }
        if title and title.strip().lower() in placeholder_titles and not artist:
            return None

        # If we truly have no metadata, say nothing (lets TV fallback / or "Nothing..." win).
        if not title and not artist:
            return None

        # Normal music phrasing; do NOT announce paused state here.
        if title and artist:
            return f"It's {title} by {artist}."
        if title:
            return f"It's {title}."
        return f"It's {artist}."

    def _clean_episode_title(raw: str) -> str:
        s = (raw or "").strip()
        if not s:
            return s
        # Common formats:
        #  - "S2 · E3: The Knights"
        #  - "S2 E3: The Knights"
        #  - "Season 2 Episode 3: The Knights"
        # Keep only the human episode title portion when possible.
        import re
        m = re.search(r":\s*(.+)$", s)
        if m:
            return m.group(1).strip()
        # If no colon, sometimes it’s "S2 · E3 The Knights" (rare) — strip leading S/E tokens.
        s = re.sub(r"^\s*(S\d+\s*[·\-\s]*\s*E\d+)\s*", "", s, flags=re.IGNORECASE).strip()
        s = re.sub(r"^\s*season\s*\d+\s*episode\s*\d+\s*", "", s, flags=re.IGNORECASE).strip()
        return s

    def _plex_tv_phrase(client_hint: str = "Apple TV") -> Optional[str]:
        """
        Fallback for when HA's Apple TV integration drops/empties metadata while paused.
        Uses Plex /status/sessions and matches the Player title against client_hint.
        Returns: "<Show>. <Episode>." (or "<Title>." for movies) or None.
        """
        try:
            plex_url = globals().get("PLEX_URL")
            plex_token = globals().get("PLEX_TOKEN")
            if not plex_url or not plex_token:
                return None

            import requests
            import xml.etree.ElementTree as ET

            url = str(plex_url).rstrip("/") + "/status/sessions"
            r = requests.get(url, params={"X-Plex-Token": plex_token}, timeout=4)
            if r.status_code != 200 or not (r.text or "").strip():
                return None

            root = ET.fromstring(r.text)

            def _matches_player(player_el) -> bool:
                if player_el is None:
                    return False
                title = (player_el.get("title") or "").strip().lower()
                if not title:
                    return False
                hint = (client_hint or "").strip().lower()
                return (hint in title) if hint else True

            # Plex XML can have Video elements for movies/episodes.
            # For episodes: grandparentTitle = show, title = episode.
            for video in root.findall(".//Video"):
                player = video.find("./Player")
                if not _matches_player(player):
                    continue

                show = (video.get("grandparentTitle") or "").strip()
                ep = (video.get("title") or "").strip()
                if show and ep:
                    ep = _clean_episode_title(ep)
                    return f"{show}. {ep}."
                if show:
                    return f"{show}."
                title = (video.get("title") or "").strip()
                if title:
                    return f"{title}."
            return None
        except Exception:
            return None

    def _tv_phrase(eid: str) -> Optional[str]:
        st = _state(eid)
        if not st:
            return None

        attrs = st.get("attributes") or {}

        # HA Apple TV (your example):
        #   media_artist = show title
        #   media_title  = "S2 · E3: The Knights"
        show = (attrs.get("media_artist") or "").strip()
        # Some integrations use media_series_title
        if not show:
            show = (attrs.get("media_series_title") or "").strip()

        raw = (attrs.get("media_title") or "").strip()
        ep = _clean_episode_title(raw)

        # Preferred UX: show title + episode title (no "paused", no S/E prefix).
        if show and ep:
            return f"{show}. {ep}."
        if show:
            return f"{show}."
        if ep:
            return f"{ep}."

        # If HA gives us nothing (often happens while paused), fall back to Plex sessions.
        plex_phrase = _plex_tv_phrase(client_hint="Apple TV")
        if plex_phrase:
            return plex_phrase

        return None

    # 1) Explicit room wins (Sonos)
    room = _find_room_in_text(t)
    if room:
        eid = sonos_players.get(_norm_room(room))
        if eid:
            resp = _sonos_phrase(eid)
            return resp or "Nothing is playing."
        return "Nothing is playing."

    # 2) Focus wins (if available)
    focus_kind = None
    focus_eid = None
    try:
        getter = globals().get("_get_transport_focus")
        if callable(getter):
            focus_kind, focus_eid = getter()
    except Exception:
        focus_kind = None
        focus_eid = None

    if focus_kind == "tv":
        resp = _tv_phrase(apple_tv_entity)
        if resp:
            return resp

    if focus_kind == "sonos" and focus_eid:
        resp = _sonos_phrase(focus_eid)
        if resp:
            return resp

    # 3) Default room Sonos, then TV fallback
    default_eid = sonos_players.get(_norm_room(default_sonos_room))
    if default_eid:
        resp = _sonos_phrase(default_eid)
        if resp:
            return resp

    resp = _tv_phrase(apple_tv_entity)
    if resp:
        return resp

    return "Nothing is playing."



def _is_np_query(tl: str) -> bool:
    """Top-level now-playing detector (shared with now_playing_controls).
    Must be available inside process_device_commands().
    """
    try:
        from now_playing_controls import _is_now_playing_query as _npq_fn
        return bool(_npq_fn(tl))
    except Exception:
        import re as _re
        _tl = (tl or '').strip().lower()
        _tl = _tl.replace('’', "'").replace('‘', "'")
        if _re.fullmatch(r"\s*playing\s*[.?!]*\s*", _tl):
            return True
        return bool(_re.search(r"\b(what's playing|what is playing|now playing|what song is this|what track is this)\b", _tl))


def _execute_local_transport_toggle(
    *,
    states_snapshot,
    maybe_say,
    tv_entity: str = "",
    tv_on_scene: Optional[str] = None,
    sonos_entity: str = "",
    sonos_room: str = "",
    set_transport_focus=None,
    get_transport_focus=None,
):
    """
    Toggle local-room playback between pause and play/resume.

    This helper is now request-aware:
      - tv_entity is the active room's configured TV, if any
      - sonos_entity is the active room's speaker, if any
      - rooms without TV config are speaker-only
      - TV resume preflight uses the active room's TV-on scene and TV entity

    This preserves living-room behavior while allowing future rooms with TVs
    to participate without hardcoded living-room Apple TV assumptions.
    """

    def _st(eid: str):
        if not eid:
            return None
        for x in (states_snapshot or []):
            if x.get("entity_id") == eid:
                return x
        return None

    def _state(eid: str) -> str:
        return str((_st(eid) or {}).get("state") or "").strip().lower()

    def _attrs(eid: str) -> dict:
        return (_st(eid) or {}).get("attributes") or {}

    def _is_sonos_tv_passthrough(eid: str) -> bool:
        a = _attrs(eid)
        source = str(a.get("source") or "").strip().lower()
        cid = str(a.get("media_content_id") or "").strip()
        title = str(a.get("media_title") or "").strip().lower()
        artist = str(a.get("media_artist") or "").strip()
        station = str(a.get("media_station") or "").strip()
        if source == "tv":
            return True
        if cid.startswith("x-sonos-htastream:"):
            return True
        if title == "tv" and not artist and not station:
            return True
        return False

    def _call(svc: str, eid: str):
        return call_ha_service(svc, {"entity_id": eid})

    tv_eid = (tv_entity or "").strip()
    sonos_eid = (sonos_entity or "").strip()
    room_label = (sonos_room or DEFAULT_SONOS_ROOM or "").strip().lower()

    # Defensive fallback only. Normal callers should pass sonos_entity.
    if not sonos_eid and room_label:
        try:
            sonos_eid = (SONOS_PLAYERS or {}).get(room_label) or ""
        except Exception:
            sonos_eid = ""

    tv_state = _state(tv_eid) if tv_eid else ""
    sonos_state = _state(sonos_eid) if sonos_eid else ""
    sonos_is_music = bool(sonos_eid) and (not _is_sonos_tv_passthrough(sonos_eid))

    focus = None
    try:
        if callable(get_transport_focus):
            focus = get_transport_focus()
    except Exception:
        focus = None
    if not focus:
        focus = getattr(process_device_commands, "_last_transport_focus", None)

    last = getattr(process_device_commands, "_last_paused_transport", None)

    try:
        logging.info(
            "TOGGLE_PLAY_PAUSE_DEBUG focus=%r last=%r room=%r tv_eid=%r tv_state=%r sonos_eid=%r sonos_state=%r sonos_is_music=%r",
            focus,
            last,
            room_label,
            tv_eid,
            tv_state,
            sonos_eid,
            sonos_state,
            sonos_is_music,
        )
    except Exception:
        pass

    decision = None

    # Pause-first: if something local is actively playing, toggle should pause it.
    if focus == "tv" and tv_eid and tv_state == "playing":
        decision = ("pause", "tv", tv_eid)
    elif focus == "sonos" and sonos_eid and sonos_is_music and sonos_state == "playing":
        decision = ("pause", "sonos", sonos_eid)
    elif sonos_eid and sonos_is_music and sonos_state == "playing":
        decision = ("pause", "sonos", sonos_eid)
    elif tv_eid and tv_state == "playing":
        decision = ("pause", "tv", tv_eid)

    # Resume path: mirror current bare play/resume behavior.
    elif last == "sonos" and sonos_eid and sonos_is_music and sonos_state in {"paused", "idle"}:
        decision = ("play", "sonos", sonos_eid)
    elif last == "tv" and tv_eid and tv_state == "paused":
        decision = ("play", "tv", tv_eid)
    elif sonos_eid and sonos_is_music and sonos_state == "paused":
        decision = ("play", "sonos", sonos_eid)
    elif tv_eid and tv_state == "paused":
        decision = ("play", "tv", tv_eid)
    elif last == "sonos" and sonos_eid and sonos_is_music and sonos_state == "idle":
        decision = ("play", "sonos", sonos_eid)

    try:
        logging.info("TOGGLE_PLAY_PAUSE_DEBUG decision=%r", decision)
    except Exception:
        pass

    if not decision:
        return None

    action, target_kind, target_eid = decision
    svc = "media_player/media_pause" if action == "pause" else "media_player/media_play"

    # If the smart toggle is resuming local TV playback, reuse the existing
    # watch-video preflight helpers so the room TV can be powered on via scene
    # and the room Apple TV can be awakened before we send the final play command.
    if action == "play" and target_kind == "tv" and tv_eid:
        try:
            from app_config import TV_ON_COOLDOWN_SECONDS as _TV_ON_COOLDOWN_SECONDS
        except Exception:
            _TV_ON_COOLDOWN_SECONDS = 10 * 60

        if tv_on_scene:
            try:
                _maybe_turn_on_tv_scene(
                    call_ha_service=call_ha_service,
                    tv_on_scene=tv_on_scene,
                    cooldown_s=int(_TV_ON_COOLDOWN_SECONDS),
                )
            except Exception:
                logging.exception("TOGGLE_TV_PREFLIGHT_SCENE_FAIL")

        try:
            _ensure_apple_tv_awake(
                states_snapshot=states_snapshot,
                call_ha_service=call_ha_service,
                apple_tv_entity=tv_eid,
                tv_on_scene=None,
                force=False,
            )
        except Exception:
            logging.exception("TOGGLE_TV_PREFLIGHT_WAKE_FAIL")

    if _call(svc, target_eid):
        if action == "pause":
            process_device_commands._last_paused_transport = target_kind
            process_device_commands._last_transport_focus = target_kind
        else:
            process_device_commands._last_transport_focus = target_kind

        try:
            if callable(set_transport_focus):
                set_transport_focus(target_kind, target_eid)
        except Exception:
            pass

        logging.info(
            "CLAIM: transport_toggle_play_pause room=%s target=%s action=%s entity=%s",
            room_label,
            target_kind,
            action,
            target_eid,
        )
        return maybe_say("Okay.")

    return None



# --- Portable room focus: "I'm in the bedroom" ------------------------------
#
# Lets portable sources (menubar, raycast, telegram) set a sticky room focus so
# bare commands route there. Stationary sources (handset, buttons, wakeword) are
# fixed and refuse the change. See source_room_state.py and home_registry SOURCES.

# Explicit set-intents — the word "room" (or an unambiguous "move/put me")
# makes the intent clear, so an unresolved room name yields a helpful hint.
_ROOM_FOCUS_SET_EXPLICIT = [
    # set/change/switch/update/make [my|the|current ...] room [focus] to|= <room>
    re.compile(
        r"^(?:set|change|switch|update|make)\s+(?:my\s+|the\s+|current\s+)*"
        r"room(?:\s+focus)?\s+(?:to|=)\s+(?:the\s+)?(.+?)$",
        re.IGNORECASE,
    ),
    # [set] [my] room focus [to|is] <room>   (the "focus" keyword, connector optional)
    re.compile(
        r"^(?:set\s+)?(?:my\s+)?room\s+focus\s+(?:to\s+|is\s+|=\s*)?(?:the\s+)?(.+?)$",
        re.IGNORECASE,
    ),
    # set/make <room> to|as [my|the|current ...] room [focus]   (reversed)
    re.compile(
        r"^(?:set|make)\s+(?:the\s+)?(.+?)\s+(?:to|as)\s+"
        r"(?:my\s+|the\s+|current\s+)*room(?:\s+focus)?$",
        re.IGNORECASE,
    ),
    # make <room> my|the [current] room
    re.compile(
        r"^make\s+(?:the\s+)?(.+?)\s+(?:my|the)\s+(?:current\s+)?room$",
        re.IGNORECASE,
    ),
    # move/put me to|in|into <room>
    re.compile(
        r"^(?:move|put)\s+me\s+(?:to|in|into)\s+(?:the\s+)?(.+?)$",
        re.IGNORECASE,
    ),
]
# Ambiguous natural phrasing ("I'm in the mood for music" is NOT a room set):
# only treated as a room set when the phrase resolves to a known room.
_ROOM_FOCUS_SET_NATURAL = [
    # I'm [now|currently] in|at [the] <room> [now]
    re.compile(
        r"^(?:i'?m|i\s+am)\s+(?:now\s+|currently\s+)?(?:in|at)\s+"
        r"(?:the\s+)?(.+?)(?:\s+now)?$",
        re.IGNORECASE,
    ),
    # I [just] moved/went/headed/am going to|into [the] <room> [now]
    re.compile(
        r"^i\s+(?:just\s+)?(?:moved|went|headed|am\s+going|am\s+heading)\s+"
        r"(?:to|in|into)\s+(?:the\s+)?(.+?)(?:\s+now)?$",
        re.IGNORECASE,
    ),
]
_ROOM_FOCUS_QUERY = re.compile(
    r"^(?:where\s+am\s+i"
    r"|what\s+room\s+am\s+i(?:\s+in|\s+set\s+to)?"
    r"|which\s+room\s+am\s+i\s+in"
    r"|what(?:'?s|\s+is)\s+my(?:\s+current)?\s+room"
    r"|what(?:'?s|\s+is)\s+my\s+room\s+focus)$",
    re.IGNORECASE,
)
_ROOM_FOCUS_CLEAR = re.compile(
    r"^(?:(?:forget|clear|reset)\s+(?:my\s+)?room(?:\s+focus)?"
    r"|i'?m\s+not\s+in\s+any\s+room)$",
    re.IGNORECASE,
)


def _clean_room_phrase(phrase: str) -> str:
    """Trim filler around a captured room name without touching 'room' itself
    (so 'living room' survives). Strips a leading article and trailing
    politeness/now words."""
    p = (phrase or "").strip()
    p = re.sub(r"^(?:the|a|my)\s+", "", p, flags=re.IGNORECASE)
    p = re.sub(r"\s+(?:now|please|thanks|thank\s+you)$", "", p, flags=re.IGNORECASE)
    return p.strip()


def _handle_room_focus_intent(t: str, tl: str) -> Optional[str]:
    """Handle 'I'm in the <room>' / 'where am I' / 'forget my room'.

    Returns a user-facing response string when the utterance is a room-focus
    command, or None to let normal command processing continue.
    """
    bare = re.sub(r"[?!.]+$", "", (tl or "").strip()).strip()
    if not bare:
        return None

    source_id = get_current_source_id()
    mobile = is_source_mobile(source_id)

    # --- Query: where am I? ---
    if _ROOM_FOCUS_QUERY.match(bare):
        if mobile:
            current = _get_focus_room(get_source_room_key(source_id))
        else:
            current = get_source_room(source_id)
        if current:
            return f"You're in the {get_room_label(current)}."
        return "I don't have a room set for this device yet."

    # --- Clear focus ---
    if _ROOM_FOCUS_CLEAR.match(bare):
        if not mobile:
            return "This device has a fixed room, so there's nothing to clear."
        if _clear_focus_room(get_source_room_key(source_id)):
            return "Okay — I've cleared your room focus."
        return "You didn't have a room focus set."

    # --- Set focus (explicit vs natural phrasing) ---
    room_phrase = None
    explicit = False
    for _pat in _ROOM_FOCUS_SET_EXPLICIT:
        _m = _pat.match(bare)
        if _m:
            room_phrase = _m.group(1)
            explicit = True
            break
    if room_phrase is None:
        for _pat in _ROOM_FOCUS_SET_NATURAL:
            _m = _pat.match(bare)
            if _m:
                room_phrase = _m.group(1)
                break

    if not room_phrase:
        return None

    room_phrase = _clean_room_phrase(room_phrase)
    room_id = _registry_room_id_from_any(room_phrase)
    if not room_id:
        # Explicit intent → tell the user; ambiguous natural phrasing → fall
        # through so things like "I'm in the mood for music" still reach the
        # normal pipeline / ChatGPT.
        if explicit:
            return f"I don't know a room called {room_phrase.strip()}."
        return None

    if not mobile:
        fixed = get_source_room(source_id)
        if fixed:
            return (
                f"This device is fixed to the {get_room_label(fixed)} "
                f"and can't change rooms."
            )
        return "This device has a fixed room and can't change rooms."

    if _set_focus_room(get_source_room_key(source_id), room_id):
        logging.info(
            "ROOM_FOCUS_SET source=%s room=%s", source_id, room_id
        )
        return (
            f"Okay — you're in the {get_room_label(room_id)} now. "
            f"I'll send your commands there."
        )
    return "Sorry, I couldn't update your room right now."


def _process_device_commands_impl(text: str, *, _repair_pass: int = 1) -> Optional[str]:
    global _current_cmd_id
    _current_cmd_id += 1
    _raw_text = text

    # --- "Say that again" / repeat last spoken response ---
    _repeat_phrases = re.compile(
        r"^(say\s+that\s+again|say\s+it\s+again|repeat\s+that|repeat\s+it|"
        r"what\s+did\s+you\s+say|what\s+was\s+that|pardon|come\s+again)[\s?!.]*$",
        re.IGNORECASE,
    )
    if _repeat_phrases.match((text or "").strip()):
        if last_spoken_text:
            logging.info("CLAIM: repeat_last_spoken")
            return last_spoken_text
        return "I haven't said anything yet."

    # --- PHONETIC PASS1: routing/command repairs (safe) ---
    text = _apply_routing_repairs(text, _repair_pass)
    _text_after_routing = text
    _dispatch_timing_mark("routing_repair")

    # Ensure ACTION_DECISION 'norm=' always reflects THIS utterance even when
    # we skip device normalization (e.g., trigger aliases like 'living room dim').
    # globals() writes target this module (command_dispatch); readers in
    # main.py access it via command_dispatch._LAST_STT_NORM_OUT.
    try:
        globals()['_LAST_STT_NORM_OUT'] = text
    except Exception:
        pass

    LOCAL_SAY_RESP = handle_local_say_controls(text)
    if LOCAL_SAY_RESP is not None:
        repaired_local_say = _apply_routing_repairs(f"announce {LOCAL_SAY_RESP}", _repair_pass)
        repaired_local_say = re.sub(r"^announce\b", "", repaired_local_say, flags=re.IGNORECASE).strip()
        if repaired_local_say and repaired_local_say != LOCAL_SAY_RESP:
            logging.info("LOCAL_SAY_REPAIR: %r -> %r", LOCAL_SAY_RESP, repaired_local_say)
            LOCAL_SAY_RESP = repaired_local_say
        logging.info("CLAIM: local_say_controls")
        return LOCAL_SAY_RESP

    try:
        text = _maybe_normalize_for_device_pipeline(text)
    except Exception:
        # Never let normalization break command handling
        text = _raw_text
    if text != _raw_text:
        try:
            logging.info("NORMALIZED_TEXT: %r -> %r", _raw_text, text)
        except Exception:
            pass
    _dispatch_timing_mark("normalize")

    t = (text or "").strip()
    tl = t.lower().strip()

    try:
        _media_rewrite = rewrite_media_pronoun_command(t)
    except Exception:
        logging.exception("MEDIA_REFERENT_REWRITE_FAIL text=%r", t)
        _media_rewrite = None
    if _media_rewrite and _media_rewrite != t:
        logging.info("MEDIA_REFERENT_COMMAND_REWRITE: %r -> %r", t, _media_rewrite)
        text = _media_rewrite
        t = (text or "").strip()
        tl = t.lower().strip()
        try:
            globals()["_LAST_STT_NORM_OUT"] = text
        except Exception:
            pass

    # --- Portable room focus: "I'm in the bedroom" (high precedence) ---
    # Runs early so portable sources can set/clear/query their sticky room
    # before any device handler claims the phrase. Stationary sources refuse.
    try:
        _room_focus_reply = _handle_room_focus_intent(t, tl)
    except Exception:
        _room_focus_reply = None
    if _room_focus_reply:
        logging.info("CLAIM: room_focus_intent")
        return _room_focus_reply

    # --- Plural pronoun expansion for multi-light follow-ups ---
    # "set them to blue" / "turn those off" → "set side lamp and stair light to blue"
    # Only fires when the last command targeted multiple lights.
    _PLURAL_LIGHT_PAT = re.compile(r"\b(them|those lights|these lights|those|these)\b", re.IGNORECASE)
    if _PLURAL_LIGHT_PAT.search(tl):
        _recent_group = _get_recent_lights()
        if len(_recent_group) > 1:
            _names = " and ".join(eid.split(".", 1)[-1].replace("_", " ") for eid in _recent_group)
            _expanded = _PLURAL_LIGHT_PAT.sub(_names, t, count=1)
            if _expanded != t:
                logging.info("PLURAL_EXPAND: %r → %r", t, _expanded)
                t = _expanded
                tl = t.lower().strip()

    if (os.getenv('PIPHONE_DEBUG_STT','0').strip() == '1'):
        pass

    # Resolve per-request context: explicit room (with swap-intent override
    # dropped), and request-aware TV/Plex defaults. The unpack preserves the
    # legacy local-variable names used throughout the rest of the function.
    _request_ctx = _resolve_request_context(tl)
    _explicit_room_id_for_request = _request_ctx.explicit_room_id
    _request_tv_room_id = _request_ctx.request_tv_room_id
    _request_tv_entity = _request_ctx.request_tv_entity
    _request_tv_remote = _request_ctx.request_tv_remote
    _request_tv_on_scene = _request_ctx.request_tv_on_scene
    _request_plex_client_name = _request_ctx.request_plex_client_name
    _request_plex_launch_script = _request_ctx.request_plex_launch_script
    _dispatch_timing_mark("request_context")

    # --- Applet lifecycle (start/stop/toggle a named applet) ---
    # Runs early so phrases like "start note lights" can't be misclaimed
    # by handlers further down. Returns None for non-applet text so other
    # handlers still get their turn.
    try:
        _applet_response = handle_applet_controls(tl)
    except Exception:
        logging.exception("APPLET_CMD_FAIL text=%r", tl)
        _applet_response = None
    if _applet_response is not None:
        try:
            globals()['_ACTION_OCCURRED'] = True
        except Exception:
            pass
        return _applet_response

    # ------------------------------------------------------------------
    # HARD OVERRIDE: BEGINNING -> APPLE TV
    # Prevent Sonos/MySonos/Spotify handlers from stealing phrases like:
    #  - "start over" (MySonos matches "over")
    #  - "start from beginning" (Spotify/Sonos handlers treat as "start ..." play command)
    # Always seek Apple TV to 0 for these phrases.
    # ------------------------------------------------------------------
    if re.search(
        r"\b("
        r"start\s+over|restart|"
        r"start\s+from\s+(?:the\s+)?beginning|"
        r"(?:skip|go)\s+to\s+beginning|"
        r"go\s+back\s+to\s+the\s+beginning|"
        r"back\s+to\s+the\s+beginning"
        r")\b",
        tl,
    ):
        if not _request_tv_entity:
            logging.info("TV_COMMAND_NO_ROOM_TV command=%r room_id=%r", tl, _request_tv_room_id)
            return None

        ok = call_ha_service(
            "media_player/media_seek",
            {"entity_id": _request_tv_entity, "seek_position": 0},
        )
        if ok:
            try:
                process_device_commands._last_transport_focus = "tv"
            except Exception:
                pass
            try:
                _set_transport_focus_for_request("tv", _request_tv_entity)
            except Exception:
                pass
            # Only speak if _maybe_say is available at this point in the function
            if "_maybe_say" in locals():
                return _maybe_say("Starting over.")
            return ""
        return None


    # Strip trailing punctuation (helps short commands like 'pause.' / 'resume.')
    tl = re.sub(r"[.!,?]+$", "", tl).strip()

    # Request-aware Sonos/audio default for this utterance.
    # This is the Phase 1 media-room migration seam: explicit room phrases still
    # win downstream, but bare media/audio commands now treat the request room as
    # "here" when it maps to a Sonos player.
    _legacy_sonos_room = _norm_sonos_room_key(DEFAULT_SONOS_ROOM)
    _request_sonos_room = _request_default_sonos_room(room_override=_explicit_room_id_for_request)
    _request_sonos_eid = (SONOS_PLAYERS or {}).get(_request_sonos_room)

    _request_tv_ctx = _request_default_tv_context(room_override=_explicit_room_id_for_request)
    _request_tv_room_id = _request_tv_ctx.get("room_id")
    _request_tv_entity = _request_tv_ctx.get("tv_entity")
    _request_tv_remote = _request_tv_ctx.get("tv_remote")
    _request_tv_on_scene = _request_tv_ctx.get("tv_on_scene")
    _request_plex_client_name = _request_tv_ctx.get("plex_client_name")
    _request_plex_launch_script = _request_tv_ctx.get("plex_launch_script")
    _dispatch_timing_mark("media_context")

    try:
        logging.info(
            "REQUEST_TV_CONTEXT room_id=%r tv=%r remote=%r tv_on_scene=%r plex_client=%r plex_launch=%r",
            _request_tv_room_id,
            _request_tv_entity,
            _request_tv_remote,
            _request_tv_on_scene,
            _request_plex_client_name,
            _request_plex_launch_script,
        )
    except Exception:
        pass

    try:
        if _request_sonos_room != _legacy_sonos_room:
            logging.info(
                "REQUEST_MEDIA_ROOM legacy=%r request_sonos_room=%r",
                _legacy_sonos_room,
                _request_sonos_room,
            )
    except Exception:
        pass

    def _transport_focus_store_for_request() -> dict:
        """
        Per-room transport focus memory for request-aware media.

        This is intentionally local to process_device_commands for now so Phase 2
        stays surgical. It prevents a satellite/source-room interaction from
        leaking focus into another room.
        """
        try:
            store = getattr(process_device_commands, "_transport_focus_by_room", None)
            if not isinstance(store, dict):
                store = {}
                setattr(process_device_commands, "_transport_focus_by_room", store)
            return store
        except Exception:
            return {}

    def _set_transport_focus_for_request(kind: str, entity_id: Optional[str] = None) -> None:
        """
        Store media transport focus for the active request room.

        Room-context rule:
        * Sonos focus is valid only when it matches this room's Sonos entity.
        * TV focus is valid only when this room has a configured TV entity and
          the focused entity matches that room TV.
        * Rooms without TV config never store TV focus.
        * Legacy global focus is still updated for the legacy living-room scope
          so existing living-room behavior remains compatible.
        """
        kind = (kind or "").strip().lower()
        if not kind:
            return

        eid = (entity_id or "").strip() if entity_id is not None else ""

        focus_room_key = (_request_sonos_room or _request_tv_room_id or "").strip()
        if not focus_room_key:
            return

        if kind == "sonos":
            if not eid:
                eid = _request_sonos_eid or ""
            if not eid or not _request_sonos_eid or eid != _request_sonos_eid:
                try:
                    logging.info(
                        "REQUEST_FOCUS_SET_SKIP kind=sonos reason=entity_mismatch room=%r eid=%r request_sonos_eid=%r",
                        focus_room_key,
                        eid,
                        _request_sonos_eid,
                    )
                except Exception:
                    pass
                return

        elif kind == "tv":
            if not eid:
                eid = _request_tv_entity or ""
            if not eid or not _request_tv_entity or eid != _request_tv_entity:
                try:
                    logging.info(
                        "REQUEST_FOCUS_SET_SKIP kind=tv reason=no_room_tv_or_entity_mismatch room=%r eid=%r request_tv_entity=%r",
                        focus_room_key,
                        eid,
                        _request_tv_entity,
                    )
                except Exception:
                    pass
                return

        else:
            return

        try:
            import time as _time
            now = float(_time.time())
        except Exception:
            now = 0.0

        try:
            store = _transport_focus_store_for_request()
            store[focus_room_key] = {
                "kind": kind,
                "entity_id": eid,
                "ts": now,
            }
            logging.info(
                "REQUEST_FOCUS_SET room=%r kind=%r entity_id=%r",
                focus_room_key,
                kind,
                eid,
            )
        except Exception:
            pass

        # Preserve legacy global focus behavior for the legacy room only.
        # This keeps the current living-room phone behavior stable while the
        # new per-room store becomes the canonical path for request-aware media.
        if _request_sonos_room == _legacy_sonos_room:
            try:
                if "_set_transport_focus" in globals() and callable(globals().get("_set_transport_focus")):
                    globals()["_set_transport_focus"](kind, eid, now)
            except Exception:
                pass

    def _get_transport_focus_for_request():
        """
        Return media focus for the active request room only.

        This is capability-based, not living-room-based:
        * Sonos focus must match this room's Sonos entity.
        * TV focus must match this room's configured TV entity.
        * If this room has no TV, TV focus is not valid.
        """
        focus_room_key = (_request_sonos_room or _request_tv_room_id or "").strip()

        try:
            store = _transport_focus_store_for_request()
            row = store.get(focus_room_key)
            if isinstance(row, dict):
                kind = str(row.get("kind") or "").strip().lower() or None
                eid = str(row.get("entity_id") or "").strip() or None

                if kind == "sonos":
                    if eid and _request_sonos_eid and eid == _request_sonos_eid:
                        return (kind, eid)
                    return (None, None)

                if kind == "tv":
                    if eid and _request_tv_entity and eid == _request_tv_entity:
                        return (kind, eid)
                    return (None, None)
        except Exception:
            pass

        # Legacy fallback: preserve old global focus behavior for the legacy
        # living-room scope only, but still filter it through the active room's
        # configured entities so it cannot leak into other rooms.
        if _request_sonos_room == _legacy_sonos_room:
            try:
                if "_get_transport_focus" in globals() and callable(globals().get("_get_transport_focus")):
                    kind, eid = globals()["_get_transport_focus"]()
                    kind = str(kind or "").strip().lower() or None
                    eid = str(eid or "").strip() or None

                    if kind == "sonos":
                        if eid and _request_sonos_eid and eid == _request_sonos_eid:
                            return (kind, eid)
                        return (None, None)

                    if kind == "tv":
                        if eid and _request_tv_entity and eid == _request_tv_entity:
                            return (kind, eid)
                        return (None, None)

                    return (None, None)
            except Exception:
                pass

        return (None, None)


    # ------------------------------------------------------------------
    # Alarm / timer controls


    def _get_request_transport_focus_kind_for_local_context():
        """
        Adapter for transport_helpers.get_local_transport_context().

        That helper currently expects a "kind" string ("tv" / "sonos" / None).
        Feed it request-scoped focus instead of legacy function-global focus so
        future TV rooms do not inherit another room's transport focus.
        """
        try:
            kind, _eid = _get_transport_focus_for_request()
            return kind
        except Exception:
            return None

    def _request_tv_label() -> str:
        """
        Human/text label for the active room's TV.

        Used by text confirmation context only; voice confirmations are still
        controlled by SPEAK_ACTION_CONFIRMATIONS.
        """
        try:
            room = (_request_sonos_room or _request_tv_room_id or "").strip()
            if room:
                return f"{room} TV"
        except Exception:
            pass
        return "TV"


    #
    # Must run before generic schedule_controls because phrases like
    # "set a timer for 5 minutes" are alarm/timer intents, not generic
    # scheduled command intents.
    # ------------------------------------------------------------------
    try:
        alarm_resp = handle_alarm_controls(
            tl=tl,
            maybe_say=_maybe_say,
            sonos_players=globals().get("SONOS_PLAYERS", {}),
            default_sonos_room=globals().get("DEFAULT_SONOS_ROOM"),
        )
    except Exception:
        logging.exception("alarm_controls failed")
        alarm_resp = None

    if alarm_resp is not None:
        logging.info("CLAIM: alarm_controls")
        return alarm_resp

    # ------------------------------------------------------------------
    # Scheduling controls
    #
    # Must run before any immediate-action handlers. Example:
    #   "turn off holiday in 20 minutes"
    # should schedule "turn off holiday", not execute it immediately.
    #
    # Validation note:
    # schedule_controls.py can validate via subprocess, but that costs several
    # seconds on the Pi because it cold-imports main.py. Since we are
    # already inside the live gpio_ptt process here, use the same command brain
    # in-process with HA writes temporarily blocked. We also capture the
    # resolved service/entity preview so scheduler safety policy can block by
    # resolved action, not just by phrase.
    # ------------------------------------------------------------------
    def _validate_scheduled_command_in_process(command: str):
        command = (command or "").strip()
        if not command:
            return False, "empty command", {}

        global call_ha_service, _ACTION_OCCURRED

        old_call_ha_service = call_ha_service
        old_action = globals().get("_ACTION_OCCURRED", False)

        old_last_light_entity_id = globals().get("last_light_entity_id", None)
        old_last_light_updated_ts = globals().get("last_light_updated_ts", 0)
        old_last_norm = globals().get("_LAST_STT_NORM_OUT", None)
        old_dialogue_scope = snapshot_dialogue_scope()

        old_transport_focus = None
        try:
            old_transport_focus = dict(globals().get("_transport_focus") or {})
        except Exception:
            old_transport_focus = None

        old_last_paused_transport = getattr(process_device_commands, "_last_paused_transport", None)
        old_last_transport_focus = getattr(process_device_commands, "_last_transport_focus", None)

        old_env_test = os.environ.get("PIPHONE_TEST_MODE")
        old_env_live = os.environ.get("PIPHONE_LIVE")

        writes = []

        def _blocked_call_ha_service(service, data=None, *args, **kwargs):
            try:
                logging.info("SCHED_VALIDATE_BLOCKED_WRITE svc=%s data=%r", service, data)
            except Exception:
                pass
            try:
                writes.append({
                    "service": str(service),
                    "data": dict(data or {}) if isinstance(data, dict) else {},
                })
            except Exception:
                writes.append({
                    "service": str(service),
                    "data": {},
                })
            globals()["_ACTION_OCCURRED"] = True
            return True

        try:
            os.environ["PIPHONE_TEST_MODE"] = "1"
            os.environ.pop("PIPHONE_LIVE", None)

            globals()["_ACTION_OCCURRED"] = False
            call_ha_service = _blocked_call_ha_service

            rv = process_device_commands(command)

            handled = bool(globals().get("_ACTION_OCCURRED", False))
            metadata = {
                "validator": "in_process",
                "writes": writes,
                "return_value": rv,
            }

            if handled:
                return True, "validated in-process", metadata

            try:
                logging.info("SCHED_VALIDATE_INPROC_UNHANDLED command=%r rv=%r", command, rv)
            except Exception:
                pass
            return False, "command was not claimed by in-process validation", metadata

        finally:
            call_ha_service = old_call_ha_service
            globals()["_ACTION_OCCURRED"] = old_action

            globals()["last_light_entity_id"] = old_last_light_entity_id
            globals()["last_light_updated_ts"] = old_last_light_updated_ts
            restore_dialogue_scope(old_dialogue_scope)

            if old_last_norm is None:
                try:
                    globals().pop("_LAST_STT_NORM_OUT", None)
                except Exception:
                    pass
            else:
                globals()["_LAST_STT_NORM_OUT"] = old_last_norm

            if old_transport_focus is not None:
                try:
                    globals()["_transport_focus"].clear()
                    globals()["_transport_focus"].update(old_transport_focus)
                except Exception:
                    pass

            try:
                if old_last_paused_transport is None and hasattr(process_device_commands, "_last_paused_transport"):
                    delattr(process_device_commands, "_last_paused_transport")
                else:
                    process_device_commands._last_paused_transport = old_last_paused_transport
            except Exception:
                pass

            try:
                if old_last_transport_focus is None and hasattr(process_device_commands, "_last_transport_focus"):
                    delattr(process_device_commands, "_last_transport_focus")
                else:
                    process_device_commands._last_transport_focus = old_last_transport_focus
            except Exception:
                pass

            if old_env_test is None:
                os.environ.pop("PIPHONE_TEST_MODE", None)
            else:
                os.environ["PIPHONE_TEST_MODE"] = old_env_test

            if old_env_live is None:
                os.environ.pop("PIPHONE_LIVE", None)
            else:
                os.environ["PIPHONE_LIVE"] = old_env_live

    try:
        schedule_resp = handle_schedule_controls(
            tl=tl,
            maybe_say=_maybe_say,
            validate_command=_validate_scheduled_command_in_process,
            solar_resolver=lambda event, day_hint, now: resolve_solar_event(
                event,
                day_hint,
                now=now,
                states_provider=(ha_get_states if callable(ha_get_states) else None),
                home_location=HOME_LOCATION,
            ),
        )
    except Exception:
        logging.exception("schedule_controls failed")
        schedule_resp = None

    if schedule_resp is not None:
        logging.info("CLAIM: schedule_controls")
        return schedule_resp

    # Stock quotes and market-clock questions are read-only. Claim them before
    # device handlers so language such as "market open" cannot become an HA
    # open/close action.
    stock_response = handle_stock_quote_query(
        tl,
        home_location=HOME_LOCATION,
    )
    if stock_response is not None:
        logging.info("CLAIM: stock_quote_controls")
        return stock_response

    # Named-place distance and direction are read-only and need no Home
    # Assistant snapshot. Road distance, traffic, and travel time deliberately
    # remain unclaimed so the conversational web-search path can answer them.
    pending_location_destination = _recall_pending_location_origin()
    location_query = parse_location_query(
        tl,
        pending_destination=pending_location_destination,
    )
    if location_query is not None:
        profile_units = str((ASSISTANT_PROFILE or {}).get("units") or "imperial")
        location_answer = answer_location_query(
            location_query,
            home_location=HOME_LOCATION,
            units=profile_units,
            source_is_fixed=_request_source_is_fixed(),
            recalled_location=_recall_location(),
        )
        if location_answer.needs_origin and location_answer.destination:
            _remember_pending_location_origin(location_answer.destination)
            _remember_location(location_answer.destination)
        elif location_answer.destination:
            forget_referent("location_distance_origin")
            _remember_location(location_answer.destination)
        logging.info(
            "CLAIM: location_controls intent=%s needs_origin=%s",
            location_query.intent,
            location_answer.needs_origin,
        )
        return location_answer.text

    # Read-only lunar-date and planetary questions can contain action words such
    # as "next", "set", and "up". Claim them before device/media handlers so
    # an astronomy question can never mutate Home Assistant state.
    _early_astronomy_query = parse_astronomy_query(tl)
    if (
        _early_astronomy_query is not None
        and _early_astronomy_query.intent
        in {
            "phase_event",
            "planet_event",
            "planet_position",
            "planet_up",
            "planet_visible",
            "planet_best",
            "visible_planets",
        }
    ):
        astronomy_response = handle_astronomy_query(
            tl,
            home_location=HOME_LOCATION,
        )
        if astronomy_response is not None:
            logging.info("CLAIM: astronomy_controls")
            return astronomy_response

    # ------------------------------------------------------------------
    # VERBATIM trigger aliases (scenes/scripts) — BEFORE on/off/device resolution
    #
    # Rationale:
    # - HA_TRIGGER_ALIASES currently only affect try_run_runnable_from_text(),
    #   but "turn on/off ..." is claimed earlier by handle_on_off_controls().
    # - That means phrases like "turn off tv" can be mis-resolved by the
    #   general device resolver and accidentally match another scene.
    #
    # Behavior:
    # - Exact (normalized) phrase match -> run the specified scene/script
    # - Scenes always use scene/turn_on (even for "turn off tv" -> scene.tv_off)
    # ------------------------------------------------------------------
    try:
        from app_config import HA_TRIGGER_ALIASES as _HA_TRIGGER_ALIASES
    except Exception:
        _HA_TRIGGER_ALIASES = {}

    def _alias_key(s: str) -> str:
        s = (s or "").strip().lower()
        s = re.sub(r"[.!,?]+$", "", s).strip()
        s = re.sub(r"\s+", " ", s).strip()
        return s

    try:
        _verbatim_map = {}
        for _ent, _phrases in (_HA_TRIGGER_ALIASES or {}).items():
            if not isinstance(_ent, str) or not (_ent.startswith("scene.") or _ent.startswith("script.")):
                continue
            if not isinstance(_phrases, (list, tuple, set)):
                continue
            for _p in _phrases:
                if not isinstance(_p, str):
                    continue
                k = _alias_key(_p)
                if k:
                    _verbatim_map[k] = _ent

        _target = _verbatim_map.get(_alias_key(tl))
        if isinstance(_target, str) and (_target.startswith("scene.") or _target.startswith("script.")):
            logging.info(f"CLAIM: trigger_alias_verbatim target={_target!r}")
            if _target.startswith("scene."):
                ok = call_ha_service("scene/turn_on", {"entity_id": _target})
            else:
                ok = call_ha_service("script/turn_on", {"entity_id": _target})
            if ok:
                return _maybe_say("Okay.")
    except Exception as e:
        logging.debug(f"Trigger alias verbatim dispatch error: {e}")

    # Spotify library actions before generic scene/script matching.
    # Phrases like "save this song" can otherwise be stolen by broad HA
    # runnables. Keep this narrow so normal play/device phrases still flow
    # through the regular routing order below.
    if re.search(r"\b(like this|save this|favorite this|add this to library|save this song|like this song|add (?:this|this song|current song|current track) to (?:playlist )?.+)\b", tl):
        spotify_library_resp = handle_spotify_controls(tl, maybe_say=_maybe_say)
        if spotify_library_resp is not None:
            logging.info("CLAIM: spotify_library_controls")
            return spotify_library_resp

    # Warm-cache scene/script fast path. This avoids converting the whole HA
    # cache to a states list for static runnable phrases like button scenes.
    # If the runnable cache is cold or expired, this returns None and the normal
    # later scene/script block refreshes from HA exactly as before.
    if re.match(r"^(pause|play|resume|stop|next|previous|prev|skip|group|ungroup)\b", tl) or re.match(r"^(turn on|turn off|toggle|lock|unlock)\b", tl):
        runnable_resp = None
        _dispatch_timing_mark("scene_script_cached", skip=1)
    else:
        runnable_resp = try_run_runnable_from_text(
            t,
            ha_get_states=ha_get_states,
            normalize_scene_phrase=lambda s: _normalize_scene_phrase(s, logger=logging),
            call_ha_service=call_ha_service,
            speak_action_confirmations=SPEAK_ACTION_CONFIRMATIONS,
            ttl_seconds=10 * 60,
            refresh_cache=False,
        )
        if runnable_resp is not None:
            logging.info("CLAIM: scene_script_cached")
            _dispatch_timing_mark("scene_script_cached", hit=1)
            return runnable_resp  # may be "" when confirmations disabled
        _dispatch_timing_mark("scene_script_cached", hit=0)


    # Pre-fetch states once for this utterance (used by _entity_exists)
    _states_snapshot = ha_get_states()
    _dispatch_timing_mark(
        "ha_get_states",
        states=(len(_states_snapshot) if isinstance(_states_snapshot, list) else None),
    )

    # ------------------------------------------------------------------
    # No-TV request-room bare next/previous.
    #
    # Rooms without a configured TV are audio-only for bare next/previous.
    # Rooms with a configured TV should fall through to the normal local
    # TV-vs-Sonos arbitration below.
    # ------------------------------------------------------------------
    if not _request_tv_entity and re.fullmatch(
        r"(next|next track|next song|previous|previous track|previous song|prev)",
        tl,
    ):
        target_eid = _request_sonos_eid
        if not target_eid:
            return None

        svc = (
            "media_player/media_next_track"
            if re.search(r"\bnext\b", tl)
            else "media_player/media_previous_track"
        )
        if call_ha_service(svc, {"entity_id": target_eid}):
            _set_transport_focus_for_request("sonos", target_eid)
            try:
                process_device_commands._last_transport_focus = "sonos"
            except Exception:
                pass
            logging.info("CLAIM: transport_bare_nextprev_request_room room=%s", _request_sonos_room)
            return _maybe_say("Okay.")
        return None

    # ------------------------------------------------------------------
    # NEXT/PREVIOUS transport router (focus-aware)
    # - Explicit: "next on tv", "previous tv", "next episode", "next on music"
    # - Bare: "next"/"previous"/"prev" routes to focused transport (tv vs sonos),
    #   with a simple state-based fallback when focus is unknown.
    # ------------------------------------------------------------------
    if re.fullmatch(
        r"(next(\s+episode)?|previous(\s+episode)?|prev)\b"
        r"(\s+(?:on\s+)?(?:the\s+)?)?"
        r"(tv|apple\s*tv|music|sonos)?",
        tl,
    ) or re.search(r"\b(next|previous|prev)\s+(?:on|in)\s+(tv|apple\s*tv|music|sonos)\b", tl):

        def _st_local(eid: str):
            if not eid:
                return None
            for x in (_states_snapshot or []):
                if x.get("entity_id") == eid:
                    return x
            return None

        def _state_local(eid: str) -> str:
            return str((_st_local(eid) or {}).get("state") or "").strip().lower()

        def _call_np(eid: str, verb_np: str) -> bool:
            svc = "media_player/media_next_track" if verb_np == "next" else "media_player/media_previous_track"
            return _ha_ok(call_ha_service(svc, {"entity_id": eid}))

        # Normalize verb
        verb_np = "next" if re.search(r"\bnext\b", tl) else "previous"

        # Detect explicit target
        explicit_kind = None  # "tv" | "sonos" | None
        if re.search(r"\b(tv|apple\s*tv)\b", tl):
            explicit_kind = "tv"
        elif re.search(r"\b(music|sonos)\b", tl):
            explicit_kind = "sonos"

        tv_eid = (_request_tv_entity or "")
        sonos_eid = SONOS_PLAYERS.get(_request_sonos_room)

        # Helper to set focus consistently for the active request room.
        def _bump_focus(kind: str, eid: str):
            try:
                _set_transport_focus_for_request(kind, eid)
            except Exception:
                pass
            try:
                process_device_commands._last_transport_focus = "tv" if kind == "tv" else "sonos"
            except Exception:
                pass

        # 1) Explicit target always wins (and steals focus)
        if explicit_kind == "tv":
            logging.info("CLAIM: transport_nextprev_explicit_tv")
            if not tv_eid:
                logging.info("TV_EXPLICIT_NO_ROOM_TV command=%r room_id=%r", tl, _request_tv_room_id)
                return None
            if _call_np(tv_eid, verb_np):
                _bump_focus("tv", tv_eid)
                return _maybe_say("Okay.")
            return None

        if explicit_kind == "sonos":
            logging.info("CLAIM: transport_nextprev_explicit_music")
            if sonos_eid and _call_np(sonos_eid, verb_np):
                _bump_focus("sonos", sonos_eid)
                return _maybe_say("Okay.")
            return None

        # 2) Request-room focus-based routing
        try:
            focus_kind, focus_eid = _get_transport_focus_for_request()
        except Exception:
            focus_kind, focus_eid = None, None

        if focus_kind == "tv" and tv_eid and focus_eid == tv_eid:
            logging.info("CLAIM: transport_nextprev_focus_tv")
            if _call_np(tv_eid, verb_np):
                _bump_focus("tv", tv_eid)
                return _maybe_say("Okay.")
            return None

        if focus_kind == "sonos" and sonos_eid and focus_eid == sonos_eid:
            logging.info("CLAIM: transport_nextprev_focus_music")
            if _call_np(sonos_eid, verb_np):
                _bump_focus("sonos", sonos_eid)
                return _maybe_say("Okay.")
            return None

        # 3) Fallback: if Apple TV is playing/paused, treat as TV; else Sonos default
        tv_state = _state_local(tv_eid)
        if tv_eid and tv_state in ("playing", "paused"):
            logging.info("CLAIM: transport_nextprev_fallback_tv")
            if _call_np(tv_eid, verb_np):
                _bump_focus("tv", tv_eid)
                return _maybe_say("Okay.")
            return None

        if sonos_eid and _state_local(sonos_eid) in ("playing", "paused"):
            logging.info("CLAIM: transport_nextprev_fallback_music")
            if _call_np(sonos_eid, verb_np):
                _bump_focus("sonos", sonos_eid)
                return _maybe_say("Okay.")
            return None

        # Nothing we can confidently route to
        return None


    # EARLY_ONOFF_LOCK: ensure turn on/off + lock/unlock always claim (incl pptest)
    # This avoids later branching/guardrails preventing on/off from being reached.
    if re.match(r"^(turn on|turn off)\b", tl):
        onoff_resp = handle_on_off_controls(
            tl=tl,
            call_ha_service=call_ha_service,
            maybe_say=_maybe_say,
            resolve_device_entity=lambda phrase: _resolve_device_entity_with_context(
                phrase,
                _states_snapshot,
                capability="binary_control",
            ),
            states_snapshot=_states_snapshot,
            remember_entity=lambda eid, domain: _remember_resolved_entity(
                eid,
                domain,
                source="device_action",
            ),
        )
        if onoff_resp is not None:
            logging.info("CLAIM: on_off_controls")
            return onoff_resp

    if re.match(r"^toggle\b", tl):
        toggle_resp = handle_toggle_controls(
            tl=tl,
            call_ha_service=call_ha_service,
            maybe_say=_maybe_say,
            resolve_device_entity=lambda phrase: _resolve_device_entity_with_context(
                phrase,
                _states_snapshot,
                capability="toggle_control",
            ),
            remember_entity=lambda eid, domain: _remember_resolved_entity(
                eid,
                domain,
                source="device_action",
            ),
        )
        if toggle_resp is not None:
            logging.info("CLAIM: toggle_controls")
            return toggle_resp

    if re.match(r"^(lock|unlock)\b", tl):
        lock_resp = handle_lock_controls(
            tl=tl,
            call_ha_service=call_ha_service,
            maybe_say=_maybe_say,
            resolve_device_entity=lambda phrase: _resolve_device_entity_with_context(
                phrase,
                _states_snapshot,
                capability="lock_control",
            ),
            remember_entity=lambda eid, domain: _remember_resolved_entity(
                eid,
                domain,
                source="device_action",
            ),
        )
        if lock_resp is not None:
            logging.info("CLAIM: lock_controls")
            return lock_resp

    capability_resp = handle_ha_capability_controls(
        tl=tl,
        states_snapshot=_states_snapshot,
        resolve_device_entity=lambda phrase: _resolve_device_entity_trace(
            phrase,
            _states_snapshot,
        ),
        call_ha_service=call_ha_service,
        maybe_say=_maybe_say,
    )
    if capability_resp is not None:
        logging.info("CLAIM: ha_capability_controls")
        return capability_resp


    # --- Volume (module) ---
    clear_text_confirm_context()
    _ppchat_volume_ctx = None
    _m_vol_set = re.search(r"\b(?:set\s+)?volume\s+(?:to\s+)?(\d{1,3})\s*%?\b", tl)
    if _m_vol_set:
        try:
            _ppchat_volume_ctx = int(_m_vol_set.group(1))
        except Exception:
            _ppchat_volume_ctx = None

    VOLUME_MODULE_RESP = handle_volume_controls(
        tl=tl,
        states_snapshot=_states_snapshot,
        call_ha_service=call_ha_service,
        maybe_say=_maybe_say,
        sonos_players=SONOS_PLAYERS,
        default_sonos_room=_request_sonos_room,
        default_volume_room=(
            _explicit_room_id_for_request
            or get_active_room_for_request_defaults()
            or get_default_room_id()
        ),
    )
    if VOLUME_MODULE_RESP is not None and _ppchat_volume_ctx is not None:
        set_text_confirm_context(
            kind="volume",
            value=_ppchat_volume_ctx,
            label=f"{_request_sonos_room} volume",
        )
    if VOLUME_MODULE_RESP is not None:
        logging.info("CLAIM: volume_controls")
        return VOLUME_MODULE_RESP
    # --- Announcements (module) ---
    ANNOUNCEMENT_MODULE_RESP = handle_announcement_controls(
        tl=tl,
        maybe_say=_maybe_say,
        players_map=SONOS_PLAYERS,
        default_sonos_room=_request_sonos_room,
        tts_generate_audio=tts_generate_audio,
        sonos_play_media=sonos_play_media,
        mark_action_occurred=mark_action_occurred,
    )
    if ANNOUNCEMENT_MODULE_RESP is not None:
        logging.info("CLAIM: announcement_controls")
        return ANNOUNCEMENT_MODULE_RESP

    # --- Now Playing (query) ---

    # DEBUG: now-playing routing snapshot (logs only for now-playing queries)
    _npq = _is_np_query(tl)
    if _npq:
        try:
            fk = fe = None
            try:
                fk, fe = _get_transport_focus_for_request()
            except Exception:
                fk = fe = None
            logging.info(f"NP_DEBUG tl={tl!r} focus_kind={fk!r} focus_eid={fe!r} default_room={_request_sonos_room!r} apple_tv={_request_tv_entity!r}")
            print(f"[NP_DEBUG] tl={tl!r} focus_kind={fk!r} focus_eid={fe!r} default_room={_request_sonos_room!r} apple_tv={_request_tv_entity!r}")
        except Exception as e:
            logging.info(f"NP_DEBUG logging failed: {e}")

    # Bare "what's playing" should be scoped to DEFAULT_SONOS_ROOM unless a room is explicitly mentioned.
    _np_players_map = SONOS_PLAYERS
    try:
        _tl_np = (tl or "").lower()
        _room_mentioned = False
        for _room in (SONOS_PLAYERS or {}).keys():
            if re.search(rf"\b{re.escape(_room)}\b", _tl_np):
                _room_mentioned = True
                break

        if not _room_mentioned:
            _default_key = (_request_sonos_room or "").strip().lower()
            _default_eid = (SONOS_PLAYERS or {}).get(_default_key)
            if _default_eid:
                _np_players_map = {_default_key: _default_eid}
    except Exception:
        _np_players_map = SONOS_PLAYERS

    _np_is_query = _is_np_query(tl)
    if _np_is_query:
        _dbg_focus(f"NP query tl={tl!r} default_room={_request_sonos_room!r} apple_tv={_request_tv_entity!r}")
        try:
            fk, fe = _get_transport_focus_for_request()
            _dbg_focus(f"request_transport_focus kind={fk!r} eid={fe!r}")
        except Exception as e:
            _dbg_focus(f"request_transport_focus getter error: {e}")
    now_playing_resp = handle_now_playing_controls(
        tl=tl,
        states_snapshot=_states_snapshot,
        sonos_players=_np_players_map,
        default_sonos_room=_request_sonos_room,
        apple_tv_entity=(_request_tv_entity or ''),
        get_transport_focus=_get_transport_focus_for_request,
    )
    # UX guard: never return silence for an explicit now-playing query.
    # During source switches, HA can briefly expose no/partial metadata.
    if _np_is_query and (now_playing_resp is None or not str(now_playing_resp).strip()):
        now_playing_resp = "Hang on - I can't see what's playing yet."

    if _np_is_query:
        _dbg_focus(f"handle_now_playing_controls returned: {now_playing_resp!r}")
    if now_playing_resp is not None:
        logging.info("CLAIM: now_playing")
        return now_playing_resp

    # --- Sonos source switching (e.g., 'tv audio', 'switch to tv audio', optional room) ---
    source_resp = handle_sonos_source_controls(
        tl=tl,
        call_ha_service=call_ha_service,
        maybe_say=_maybe_say,
        players_map=SONOS_PLAYERS,
        default_room=_request_sonos_room,
        apple_tv_entity=(_request_tv_entity or ''),
        set_transport_focus=_set_transport_focus_for_request,
    )
    if source_resp is not None:
        logging.info("CLAIM: sonos_source_controls")
        return source_resp

    
# --- YouTube on the Apple TV (Lounge API) — claims before the Plex watch-preflight ---
    # Claims known channels, explicit "youtube"/"on youtube", or reel/roundup/digest/
    # playlist verbs (play or watch — those words can't be songs). A bare "watch
    # <title>" for an unknown channel returns None and falls through to the Plex
    # preflight + handler below; "play <artist>" returns None so music routing is
    # untouched. When it DOES claim, it runs its own full TV preflight (TV-on scene +
    # wake ATV + launch the YouTube app), reusing the same helpers as Plex.
    def _youtube_preflight():
        try:
            from app_config import TV_ON_COOLDOWN_SECONDS as _cd
        except Exception:
            _cd = 10 * 60
        # 1) TV power (rate-limited; shares the cooldown with Plex).
        try:
            if _request_tv_on_scene:
                _maybe_turn_on_tv_scene(call_ha_service=call_ha_service,
                                        tv_on_scene=_request_tv_on_scene,
                                        cooldown_s=int(_cd))
        except Exception:
            pass
        # 2) Wake the Apple TV (force) so it can accept the app-launch — mirrors
        #    Plex's preflight.
        try:
            if _request_tv_entity:
                _ensure_apple_tv_awake(states_snapshot=_states_snapshot,
                                       call_ha_service=call_ha_service,
                                       apple_tv_entity=_request_tv_entity,
                                       tv_on_scene=None, force=True,
                                       allow_play_fallback=False)
        except Exception:
            pass
        # 3) Foreground YouTube. ALWAYS done for a playback command — NOT gated on
        #    app_name, which is sticky/stale on the ATV (it keeps reporting
        #    "YouTube" long after you've left it, so a gate would wrongly skip the
        #    launch). pyatv's play_media with the app bundle id wakes + switches
        #    reliably even mid-playback (this integration doesn't expose
        #    select_source). Falls back to the HA launch script only if rejected.
        launched = False
        try:
            ok = False
            if _request_tv_entity:
                try:
                    ok = bool(call_ha_service("media_player/play_media", {
                        "entity_id": _request_tv_entity,
                        "media_content_type": "app",
                        "media_content_id": "com.google.ios.youtube",
                    }))
                except Exception:
                    ok = False
            if not ok:
                call_ha_service("script/turn_on",
                                {"entity_id": "script.apple_tv_launch_youtube"})
            launched = True
        except Exception:
            pass
        # 4) Give the app a moment to foreground before the Lounge setPlaylist; the
        #    Lounge command also retries, covering the rest of the readiness gap.
        if launched:
            try:
                from app_config import YOUTUBE_APP_LAUNCH_SETTLE_S as _settle
            except Exception:
                _settle = 1.5
            try:
                import time as _t
                _t.sleep(float(_settle))
            except Exception:
                pass

    youtube_resp = handle_youtube_controls(
        tl=tl,
        call_ha_service=call_ha_service,
        maybe_say=_maybe_say_media,
        preflight=_youtube_preflight,
        mark_action=mark_action_occurred,
    )
    if youtube_resp is not None:
        logging.info("CLAIM: youtube_controls")
        return youtube_resp

    # --- Video preflight (watch ...) ---
    # For future extensibility: this wake step is generic for ANY watch-style video request.
    if re.match(r"^watch\b", tl):
        if not _request_tv_entity:
            logging.info("WATCH_NO_ROOM_TV room_id=%r text=%r", _request_tv_room_id, tl)
            return "I don't have a TV configured for this room."

        # TV-on policy: trigger the room's TV-on scene for watch, rate-limited via cooldown.
        try:
            from app_config import TV_ON_COOLDOWN_SECONDS as _TV_ON_COOLDOWN_SECONDS
        except Exception:
            _TV_ON_COOLDOWN_SECONDS = 10 * 60

        try:
            _maybe_turn_on_tv_scene(
                call_ha_service=call_ha_service,
                tv_on_scene=_request_tv_on_scene,
                cooldown_s=int(_TV_ON_COOLDOWN_SECONDS),
            )
        except Exception:
            pass

        # Wake ATV if HA reports it asleep.
        try:
            did_wake = _ensure_apple_tv_awake(
                states_snapshot=_states_snapshot,
                call_ha_service=call_ha_service,
                apple_tv_entity=_request_tv_entity,
                tv_on_scene=None,
                # Force wake: HA state can be stale ('idle'/'on') while ATV is effectively asleep,
                # which breaks Plex /clients discovery.
                force=True,
                allow_play_fallback=False,
            )
        except Exception:
            did_wake = False

        # Ensure Plex is frontmost on ATV (HA script).
        try:
            did_launch = False
            if _request_plex_launch_script:
                did_launch = _ensure_apple_tv_app(
                    states_snapshot=_states_snapshot,
                    call_ha_service=call_ha_service,
                    apple_tv_entity=_request_tv_entity,
                    desired_app="Plex",
                    launch_script=_request_plex_launch_script,
                )
        except Exception:
            did_launch = False

        # If we woke ATV or launched Plex, bias focus toward TV so bare transport routes correctly.
        if did_wake or did_launch:
            try:
                _set_transport_focus_for_request("tv", _request_tv_entity)
            except Exception:
                pass
            try:
                setattr(process_device_commands, "_last_transport_focus", "tv")
            except Exception:
                pass

# --- Plex (scaffold: detects 'watch <title>' but does not act yet) ---
    PLEX_MODULE_RESP = None

    # Only allow Plex participation when the active request room has TV/Plex context.
    # This preserves living-room/default behavior while preventing no-TV rooms from
    # implicitly falling back to "Apple TV".
    _plex_description_resolver = (
        (lambda desc, **kw: resolve_plex_description(desc, OPENAI_CLIENT, **kw))
        if OPENAI_CLIENT is not None else None
    )

    if _request_tv_entity or _request_plex_client_name:
        if re.match(r"^watch\b", tl):
            import time as _time
            _attempts = 6
            _delay = 0.40
            for _i in range(_attempts):
                PLEX_MODULE_RESP = handle_plex_controls(
                    tl=tl,
                    maybe_say=_maybe_say_media,
                    plex_url=PLEX_URL,
                    plex_token=PLEX_TOKEN,
                    prefer_client_name=(_request_plex_client_name or "Apple TV"),
                    mark_action_occurred=mark_action_occurred,
                    resolve_description=_plex_description_resolver,
                )
                if PLEX_MODULE_RESP is not None:
                    break
                _time.sleep(_delay)
                _delay = min(_delay * 1.5, 1.5)
        else:
            PLEX_MODULE_RESP = handle_plex_controls(
                tl=tl,
                maybe_say=_maybe_say_media,
                plex_url=PLEX_URL,
                plex_token=PLEX_TOKEN,
                prefer_client_name=(_request_plex_client_name or "Apple TV"),
                mark_action_occurred=mark_action_occurred,
                resolve_description=_plex_description_resolver,
            )
    else:
        try:
            if re.search(r"\bplex\b", tl):
                logging.info("PLEX_SKIP_NO_ROOM_TV room_id=%r text=%r", _request_tv_room_id, tl)
        except Exception:
            pass

    if PLEX_MODULE_RESP is not None:
        return PLEX_MODULE_RESP

    # --- Sonos Spotify (browse_media -> play_media) ---
    # --- Sonos Spotify (browse_media -> play_media) ---
    # Allow optional room targeting: "play X in kitchen".
    #
    # Keep this dynamic from SONOS_PLAYERS so rooms like bathroom/future rooms
    # are stripped before pinned-radio / My Sonos / Spotify browse matching.
    # Example:
    #   "play kclu in bathroom" -> sonos_tl="play kclu", sonos_room="bathroom"
    sonos_tl = tl
    sonos_room = _request_sonos_room

    try:
        for _room_key in sorted((SONOS_PLAYERS or {}).keys(), key=len, reverse=True):
            _room_norm = _norm_sonos_room_key(str(_room_key))
            if not _room_norm:
                continue
            _room_pat = re.escape(_room_norm)
            _m_sonos_room = re.search(
                rf"\b(?:in|on)\s+(?:the\s+)?{_room_pat}\s*$",
                sonos_tl,
            )
            if not _m_sonos_room:
                continue

            sonos_room = _room_norm
            sonos_tl = sonos_tl[: _m_sonos_room.start()].strip()
            try:
                logging.info(
                    "SONOS_ROOM_SUFFIX_STRIP room=%r sonos_tl=%r original=%r",
                    sonos_room,
                    sonos_tl,
                    tl,
                )
            except Exception:
                pass
            break
    except Exception:
        pass

    sonos_entity_id = SONOS_PLAYERS.get(sonos_room) or SONOS_PLAYERS.get(_request_sonos_room)

    
    

    # --- Exact transport toggle command (local-room only) ---
    # Must be claimed before music-start handlers so phrases like
    # "toggle play pause" are not misinterpreted as music requests.
    #
    # Room-aware cleanup:
    #   "toggle play pause"
    #   "play pause"
    #   "toggle play pause in kitchen"
    #   "play pause in bathroom"
    # all use the active request room / explicit room override.
    try:
        _toggle_rooms_alt = "|".join(
            re.escape(_norm_sonos_room_key(str(_r)))
            for _r in sorted((SONOS_PLAYERS or {}).keys(), key=len, reverse=True)
            if _norm_sonos_room_key(str(_r))
        )
    except Exception:
        _toggle_rooms_alt = ""

    _toggle_pat = r"(?:toggle\s+)?play\s+pause"
    if _toggle_rooms_alt:
        _toggle_pat += rf"(?:\s+(?:in|on)\s+(?:the\s+)?(?:{_toggle_rooms_alt}))?"
    if re.fullmatch(_toggle_pat, tl.strip().lower()):
        toggle_resp = _execute_local_transport_toggle(
            states_snapshot=_states_snapshot,
            maybe_say=_maybe_say,
            tv_entity=(_request_tv_entity or ""),
            tv_on_scene=_request_tv_on_scene,
            sonos_entity=(_request_sonos_eid or ""),
            sonos_room=_request_sonos_room,
            set_transport_focus=_set_transport_focus_for_request,
            get_transport_focus=_get_request_transport_focus_kind_for_local_context,
        )
        if toggle_resp is not None:
            return toggle_resp

    # --- Sonos My Sonos / Favorites (browse_media -> play_media) ---
    # --- Sonos My Sonos / Favorites (browse_media -> play_media) ---
    # Prefer items saved in Sonos (e.g., radio stations, line-in favorites like "Turntable") before
    # falling back to pinned_radio + Spotify-specific logic.
    mysonos_resp = handle_sonos_my_sonos_controls(
        tl=sonos_tl,
        ha_session=_ha_session_safe(),
        ha_url=HA_URL,
        ha_headers=_ha_headers_safe(),
        sonos_entity_id=sonos_entity_id,
        call_ha_service=call_ha_service,
        maybe_say=_maybe_say,
    )
    if mysonos_resp is not None:
        logging.info("CLAIM: sonos_my_sonos")
        # If PiPhone started playback on the DEFAULT room Sonos, bump transport focus to Sonos.
        try:
            if (sonos_room or "").strip().lower() == (_request_sonos_room or "").strip().lower():
                setattr(process_device_commands, "_last_transport_focus", "sonos")
                try:
                    import time as _time
                    default_key = (_request_sonos_room or "").strip().lower()
                    default_eid = (SONOS_PLAYERS or {}).get(default_key) or sonos_entity_id
                    if default_eid:
                        _set_transport_focus_for_request("sonos", default_eid)
                except Exception:
                    pass
        except Exception:
            pass
        return mysonos_resp


    # --- Pinned radio stations (e.g., "play kclu", optional "in kitchen") ---
    try:
        from app_config import PINNED_RADIO_STATIONS
    except Exception:
        PINNED_RADIO_STATIONS = {}

    radio_resp = handle_pinned_radio_controls(
        tl=tl,
        sonos_tl=sonos_tl,
        pinned_radio_stations=(PINNED_RADIO_STATIONS or {}),
        sonos_entity_id=sonos_entity_id,
        call_ha_service=call_ha_service,
        maybe_say=_maybe_say,
    )
    if radio_resp is not None:
        logging.info("CLAIM: pinned_radio")
        # If PiPhone started playback on the DEFAULT room Sonos, bump transport focus to Sonos.
        # This keeps bare pause/resume aligned with "music was started more recently".
        try:
            if (sonos_room or "").strip().lower() == (_request_sonos_room or "").strip().lower():
                # Local focus memory (used by your bare transport logic)
                setattr(process_device_commands, "_last_transport_focus", "sonos")
                # Shared transport focus (used elsewhere; keep in lockstep)
                try:
                    import time as _time
                    default_key = (_request_sonos_room or "").strip().lower()
                    default_eid = (SONOS_PLAYERS or {}).get(default_key) or sonos_entity_id
                    if default_eid:
                        _set_transport_focus_for_request("sonos", default_eid)
                except Exception:
                    pass
        except Exception:
            pass
        return radio_resp

    sonos_resp = handle_sonos_spotify_browse_play(
        tl=sonos_tl,
        ha_session=_ha_session_safe(),
        ha_url=HA_URL,
        ha_headers=_ha_headers_safe(),
        sonos_entity_id=sonos_entity_id,
        call_ha_service=call_ha_service,
        maybe_say=_maybe_say,
    )
    if sonos_resp is not None:
        logging.info("CLAIM: sonos_spotify_browse_play")
        # If PiPhone started playback on the DEFAULT room Sonos, bump transport focus to Sonos.
        try:
            if (sonos_room or "").strip().lower() == (_request_sonos_room or "").strip().lower():
                setattr(process_device_commands, "_last_transport_focus", "sonos")
                try:
                    import time as _time
                    default_key = (_request_sonos_room or "").strip().lower()
                    default_eid = (SONOS_PLAYERS or {}).get(default_key) or sonos_entity_id
                    if default_eid:
                        _set_transport_focus_for_request("sonos", default_eid)
                except Exception:
                    pass
        except Exception:
            pass
        return sonos_resp

    # --- Local astronomy ---
    astronomy_response = handle_astronomy_query(
        tl,
        home_location=HOME_LOCATION,
        states_snapshot=_states_snapshot,
    )
    if astronomy_response is not None:
        logging.info("CLAIM: astronomy_controls")
        return astronomy_response

    # --- Time and date ---
    m_time = re.search(r"\bwhat(?:'s| is) the time(?: in (.+))?\b", tl) or re.search(r"\bwhat time is it(?: in (.+))?\b", tl) or re.search(r"\btell me the time(?: in (.+))?\b", tl)
    if m_time:
        loc = m_time.group(1).strip() if m_time.group(1) else None
        if not loc:
            loc = _resolve_location_pronoun(tl)
        logging.info("CLAIM: time_controls")
        return handle_time_query(loc)

    date_query = parse_date_query(tl)
    if date_query:
        loc = date_query.location
        if not loc:
            loc = _resolve_location_pronoun(tl)
        logging.info("CLAIM: date_controls")
        return handle_date_query(loc)

    # --- Weather ---
    weather_query = parse_weather_query(tl)
    if weather_query:
        loc = weather_query.location
        if not loc:
            loc = _resolve_location_pronoun(tl)
        return handle_weather_query(
            loc,
            query=weather_query,
            states_snapshot=_states_snapshot,
        )

    # 6.5) State queries (device state readback)
    # IMPORTANT: this block must be at top-level indentation inside process_device_commands.
    # If it's nested under Weather/Scenes, it becomes unreachable and causes error-tone fallthrough.
    try:
        logging.info('STATE_Q_IN: %r', tl)
    except Exception:
        pass

    _looks_state_query = looks_like_state_query(tl)

    try:
        if tl.startswith("__alarm_fire__ "):
            gate = 1
        elif re.match(r"^in\s+\d+\s+(seconds?|minutes?|hours?)\b", tl):
            gate = 1
        elif re.match(r"^at\s+\d{1,2}(:\d{2})?\s*(am|pm)?\b", tl):
            gate = 1
        logging.info("STATE_Q_GATE=%s tl=%r", 1 if _looks_state_query else 0, tl)
    except Exception:
        pass

    if _looks_state_query:
        try:
            logging.info("STATE_Q_CALL default_room=%r", (DEFAULT_SONOS_ROOM or None))
        except Exception:
            pass

        state_q = handle_state_query_controls(
            tl,
            states_snapshot=_states_snapshot,
            resolve_device_entity=lambda phrase: _resolve_device_entity_trace(phrase, _states_snapshot),
            maybe_say=_maybe_say,
            sonos_players=SONOS_PLAYERS,
            default_sonos_room=_request_sonos_room,
            remember_entity=lambda eid, domain: _remember_resolved_entity(
                eid,
                domain,
                source="state_query",
            ),
        )

        try:
            logging.info("STATE_Q_RET=%r", state_q)
        except Exception:
            pass

        # If it looks like a state query but module returns None, return a safe fallback
        # AND mark action occurred so we don't play the error tone.
        if state_q is None:
            try:
                logging.info("STATE_Q_FALLBACK")
            except Exception:
                pass
            state_q = "I couldn't tell."

        try:
            globals()['_ACTION_OCCURRED'] = True
        except Exception:
            pass

        try:
            logging.info('CLAIM: state_query_controls')
        except Exception:
            pass
        return state_q

# --- Scenes/Scripts (short phrases like "living room bright") ---
    homelab_resp = handle_homelab_controls(
        tl=tl,
        states_snapshot=_states_snapshot,
    )
    if homelab_resp is not None:
        try:
            logging.info("CLAIM: homelab_controls")
        except Exception:
            pass
        return homelab_resp

    # --- Scenes/Scripts (short phrases like "living room bright") ---
    # Avoid stealing transport words (pause/play/resume/etc.)
    if re.match(r"^(pause|play|resume|stop|next|previous|prev|skip|group|ungroup)\b", tl) or re.match(r"^(turn on|turn off|toggle|lock|unlock)\b", tl):
        runnable_resp = None
    else:
        runnable_resp = try_run_runnable_from_text(
            t,
            ha_get_states=ha_get_states,
            normalize_scene_phrase=lambda s: _normalize_scene_phrase(s, logger=logging),
            call_ha_service=call_ha_service,
            speak_action_confirmations=SPEAK_ACTION_CONFIRMATIONS,
            ttl_seconds=10 * 60,
        )
        if runnable_resp is not None:
            return runnable_resp  # may be "" when confirmations disabled

    # TV command guard for rooms without configured TVs.
    # Prevent explicit TV phrases from falling through to living-room Apple TV.
    if not _request_tv_entity and re.search(r"\b(tv|apple\s*tv)\b", tl):
        if re.search(r"\b(pause|stop|resume|play|next|previous|prev|skip|rewind|forward|back|start over|restart)\b", tl):
            logging.info("TV_EXPLICIT_NO_ROOM_TV command=%r room_id=%r", tl, _request_tv_room_id)
            return None

    # --- Media transport disambiguation (TV vs music) ---
    # Bare pause/resume must stay in lockstep with now-playing arbitration.
    # IMPORTANT: bare commands only consider DEFAULT room Sonos + the Apple TV entity.

    # Persist tiny focus memory on the function (no globals)
    if not hasattr(process_device_commands, "_last_paused_transport"):
        process_device_commands._last_paused_transport = None  # "tv" | "sonos" | None
    if not hasattr(process_device_commands, "_last_transport_focus"):
        process_device_commands._last_transport_focus = None  # "tv" | "sonos" | None


    def _st(eid: str):
        if not eid:
            return None
        for x in (_states_snapshot or []):
            if x.get("entity_id") == eid:
                return x
        return None

    def _state(eid: str) -> str:
        return str((_st(eid) or {}).get("state") or "").strip().lower()

    def _attrs(eid: str) -> dict:
        return (_st(eid) or {}).get("attributes") or {}

    def _is_sonos_tv_passthrough(eid: str) -> bool:
        a = _attrs(eid)
        source = str(a.get("source") or "").strip().lower()
        cid = str(a.get("media_content_id") or "").strip()
        title = str(a.get("media_title") or "").strip().lower()
        artist = str(a.get("media_artist") or "").strip()
        station = str(a.get("media_station") or "").strip()
        if source == "tv":
            return True
        if cid.startswith("x-sonos-htastream:"):
            return True
        if title == "tv" and not artist and not station:
            return True
        return False

    def _call(svc: str, eid: str):
        return call_ha_service(svc, {"entity_id": eid})

    # Explicit targets should always work
    if re.search(r"\b(pause|stop)\s+tv\b", tl):
        logging.info("CLAIM: transport_explicit_tv")
        tv_eid = (_request_tv_entity or "")
        if not tv_eid:
            logging.info("TV_EXPLICIT_NO_ROOM_TV command=%r room_id=%r", tl, _request_tv_room_id)
            return None
        if _call("media_player/media_pause", tv_eid):
            process_device_commands._last_paused_transport = "tv"
            process_device_commands._last_transport_focus = "tv"
            _set_transport_focus_for_request("tv", tv_eid)
            return _maybe_say("Okay.")
        return None

    if re.search(r"\b(resume|play)\s+tv\b", tl):
        logging.info("CLAIM: transport_explicit_tv")
        tv_eid = (_request_tv_entity or "")
        if not tv_eid:
            logging.info("TV_EXPLICIT_NO_ROOM_TV command=%r room_id=%r", tl, _request_tv_room_id)
            return None
        if _call("media_player/media_play", tv_eid):
            process_device_commands._last_transport_focus = "tv"
            _set_transport_focus_for_request("tv", tv_eid)
            return _maybe_say("Okay.")
        return None

    if re.search(r"\b(pause|stop)\s+music\b", tl):
        logging.info("CLAIM: transport_explicit_music")
        sonos_eid = SONOS_PLAYERS.get(_request_sonos_room)
        if sonos_eid and _call("media_player/media_pause", sonos_eid):
            process_device_commands._last_paused_transport = "sonos"
            process_device_commands._last_transport_focus = "sonos"
            try:
                _set_transport_focus_for_request("sonos", sonos_eid)
            except Exception:
                pass
            return _maybe_say("Okay.")
        return None

    if re.search(r"\b(resume|play)\s+music\b", tl):
        logging.info("CLAIM: transport_explicit_music")
        sonos_eid = SONOS_PLAYERS.get(_request_sonos_room)
        if sonos_eid and _call("media_player/media_play", sonos_eid):
            process_device_commands._last_transport_focus = "sonos"
            try:
                _set_transport_focus_for_request("sonos", sonos_eid)
            except Exception:
                pass
            return _maybe_say("Okay.")
        return None

    # -------------------------------------------------
    # NEXT / PREVIOUS transport (focus-aware like pause/play)
    # -------------------------------------------------

    # Explicit targets should always work (and should set focus)
    if re.search(r"\b(next)\s+(?:on\s+)?tv\b", tl):
        logging.info("CLAIM: transport_explicit_tv_nextprev")
        tv_eid = (_request_tv_entity or "")
        if not tv_eid:
            logging.info("TV_EXPLICIT_NO_ROOM_TV command=%r room_id=%r", tl, _request_tv_room_id)
            return None
        if _call("media_player/media_next_track", tv_eid):
            process_device_commands._last_transport_focus = "tv"
            _set_transport_focus_for_request("tv", tv_eid)
            return _maybe_say("Okay.")
        return None

    if re.search(r"\b(previous|prev)\s+(?:on\s+)?tv\b", tl):
        logging.info("CLAIM: transport_explicit_tv_nextprev")
        # TV previous can be "restart unless near start" — we implement smart double-tap:
        tv_eid = (_request_tv_entity or "")
        if not tv_eid:
            logging.info("TV_EXPLICIT_NO_ROOM_TV command=%r room_id=%r", tl, _request_tv_room_id)
            return None

        attrs = _attrs(tv_eid)
        pos = attrs.get("media_position")
        app = str(attrs.get("app_name") or attrs.get("source") or attrs.get("app_id") or "").strip().lower()

        # Tunable per-app thresholds (seconds). Edit freely later.
        _thr_map = {
            "plex": 5,
            "youtube": 5,
            "_default": 5,
        }
        thr = _thr_map.get("_default", 10)
        for k, v in _thr_map.items():
            if k != "_default" and k and (k in app):
                thr = int(v)
                break

        def _prev_once():
            return _call("media_player/media_previous_track", tv_eid)

        ok = False
        try:
            posf = float(pos) if pos is not None else None
        except Exception:
            posf = None

        if posf is not None and posf > thr:
            ok1 = _prev_once()
            if ok1:
                import time as _time
                _time.sleep(0.25)
                ok2 = _prev_once()
                ok = bool(ok2)
            else:
                ok = False
        else:
            ok = bool(_prev_once())

        if ok:
            process_device_commands._last_transport_focus = "tv"
            _set_transport_focus_for_request("tv", tv_eid)
            return _maybe_say("Okay.")
        return None

    if re.search(r"\b(next)\s+(?:on\s+)?music\b", tl):
        logging.info("CLAIM: transport_explicit_music_nextprev")
        sonos_eid = SONOS_PLAYERS.get(_request_sonos_room)
        if sonos_eid and _call("media_player/media_next_track", sonos_eid):
            process_device_commands._last_transport_focus = "sonos"
            try:
                _set_transport_focus_for_request("sonos", sonos_eid)
            except Exception:
                pass
            return _maybe_say("Okay.")
        return None

    if re.search(r"\b(previous|prev)\s+(?:on\s+)?music\b", tl):
        logging.info("CLAIM: transport_explicit_music_nextprev")
        sonos_eid = SONOS_PLAYERS.get(_request_sonos_room)
        if sonos_eid and _call("media_player/media_previous_track", sonos_eid):
            process_device_commands._last_transport_focus = "sonos"
            try:
                _set_transport_focus_for_request("sonos", sonos_eid)
            except Exception:
                pass
            return _maybe_say("Okay.")
        return None

    # Room-targeted transport stays as-is (can affect non-default rooms intentionally).
    # Dynamic from SONOS_PLAYERS so future rooms do not require regex edits.
    m_room_transport = None
    try:
        for _room_key in sorted((SONOS_PLAYERS or {}).keys(), key=len, reverse=True):
            _room_norm = _norm_sonos_room_key(str(_room_key))
            if not _room_norm:
                continue
            _room_pat = re.escape(_room_norm)
            _m_rt = re.fullmatch(
                rf"(pause|stop|resume|play)\s+(?:in\s+)?(?:the\s+)?({_room_pat})",
                tl,
            )
            if _m_rt:
                m_room_transport = (_m_rt.group(1), _room_norm)
                break
    except Exception:
        m_room_transport = None

    if m_room_transport:
        logging.info("CLAIM: transport_room_targeted_music")
        verb = m_room_transport[0]
        room = m_room_transport[1]
        target_eid = SONOS_PLAYERS.get(room)
        if not target_eid:
            return None
        svc = "media_player/media_pause" if verb in {"pause", "stop"} else "media_player/media_play"
        if _call(svc, target_eid):
            label = f"{room} speaker"
            verb_out = "paused" if verb in {"pause", "stop"} else "resumed"
            if verb == "stop":
                verb_out = "stopped"
            set_text_confirm_context(
                kind="transport",
                verb=verb_out,
                entity_id=target_eid,
                label=label,
            )
            try:
                if room == (_request_sonos_room or "").strip().lower():
                    process_device_commands._last_transport_focus = "sonos"
                    if verb in {"pause", "stop"}:
                        process_device_commands._last_paused_transport = "sonos"
                    _set_transport_focus_for_request("sonos", target_eid)
            except Exception:
                pass
            return _maybe_say("Okay.")
        return None

    # Bare next/previous: DEFAULT ROOM ONLY (routes by focus like pause/play)
    # Bare next/previous: DEFAULT ROOM ONLY (routes by focus like pause/play)
    if tl in {"next", "previous", "prev"}:
        logging.info("CLAIM: transport_bare_nextprev")
        verb = tl

        _ctx = _get_local_transport_context(
            states_snapshot=_states_snapshot,
            apple_tv_entity=(_request_tv_entity or ''),
            sonos_players=SONOS_PLAYERS,
            default_sonos_room=_request_sonos_room,
            get_recent_transport_focus=_get_request_transport_focus_kind_for_local_context,
            get_last_paused_transport=lambda: getattr(process_device_commands, "_last_paused_transport", None),
            is_sonos_tv_passthrough=_is_sonos_tv_passthrough,
        )
        tv_eid = _ctx["tv_eid"]
        sonos_eid = _ctx["sonos_eid"]
        tv_state = _ctx["tv_state"]
        sonos_state = _ctx["sonos_state"]
        sonos_is_music = _ctx["sonos_is_music"]
        focus = _ctx["focus"]

        def _tv_next() -> bool:
            return bool(_call("media_player/media_next_track", tv_eid))

        def _tv_prev_smart() -> bool:
            attrs = _attrs(tv_eid)
            pos = attrs.get("media_position")
            app = str(attrs.get("app_name") or attrs.get("source") or attrs.get("app_id") or "").strip().lower()

            # Tunable per-app thresholds (seconds). Edit freely later.
            _thr_map = {
                "plex": 5,
                "youtube": 5,
                "_default": 5,
            }
            thr = _thr_map.get("_default", 10)
            for k, v in _thr_map.items():
                if k != "_default" and k and (k in app):
                    thr = int(v)
                    break

            def _prev_once() -> bool:
                return bool(_call("media_player/media_previous_track", tv_eid))

            try:
                posf = float(pos) if pos is not None else None
            except Exception:
                posf = None

            # If we're past the threshold, first prev = restart, second prev = previous episode/item.
            if posf is not None and posf > thr:
                ok1 = _prev_once()
                if not ok1:
                    return False
                import time as _time
                _time.sleep(0.25)
                return _prev_once()

            # Near start (or unknown position): single previous is safest.
            return _prev_once()

        def _sonos_nextprev() -> bool:
            if not sonos_eid or not sonos_is_music:
                return False
            svc = "media_player/media_next_track" if verb == "next" else "media_player/media_previous_track"
            return bool(_call(svc, sonos_eid))

        # Preferred routing: focus first (if that target is plausibly active)
        if focus == "tv" and tv_eid and tv_state in {"playing", "paused"}:
            ok = _tv_next() if verb == "next" else _tv_prev_smart()
            if ok:
                process_device_commands._last_transport_focus = "tv"
                try:
                    _set_transport_focus_for_request("tv", tv_eid)
                except Exception:
                    pass
                return _maybe_say("Okay.")
            return ""

        if focus == "sonos" and sonos_eid and sonos_is_music and sonos_state in {"playing", "paused"}:
            ok = _sonos_nextprev()
            if ok:
                process_device_commands._last_transport_focus = "sonos"
                try:
                    _set_transport_focus_for_request("sonos", sonos_eid)
                except Exception:
                    pass
                return _maybe_say("Okay.")
            return ""

        # Fallback routing if focus isn't set / isn't valid right now:
        # If TV is playing/paused and Sonos isn't real music, use TV.
        if tv_eid and tv_state in {"playing", "paused"} and (not sonos_is_music):
            ok = _tv_next() if verb == "next" else _tv_prev_smart()
            if ok:
                process_device_commands._last_transport_focus = "tv"
                try:
                    _set_transport_focus_for_request("tv", tv_eid)
                except Exception:
                    pass
                return _maybe_say("Okay.")
            return ""

        # Otherwise prefer Sonos music if active
        if sonos_is_music and sonos_state in {"playing", "paused"}:
            ok = _sonos_nextprev()
            if ok:
                process_device_commands._last_transport_focus = "sonos"
                try:
                    _set_transport_focus_for_request("sonos", sonos_eid)
                except Exception:
                    pass
                return _maybe_say("Okay.")
            return ""

        # Last resort: try TV anyway
        if tv_eid:
            ok = _tv_next() if verb == "next" else _tv_prev_smart()
            if ok:
                process_device_commands._last_transport_focus = "tv"
                try:
                    _set_transport_focus_for_request("tv", tv_eid)
                except Exception:
                    pass
                return _maybe_say("Okay.")
            return ""

        return None



    # Smart media toggle: local-room only
    if tl.strip().lower() == "play pause":
        try:
            logging.info(
                "PLAY_PAUSE_DEBUG before focus=%r last=%r tv_eid=%r tv_state=%r sonos_eid=%r sonos_state=%r sonos_is_music=%r",
                getattr(process_device_commands, "_last_transport_focus", None),
                getattr(process_device_commands, "_last_paused_transport", None),
                tv_eid,
                tv_state,
                sonos_eid,
                sonos_state,
                sonos_is_music,
            )
        except Exception:
            pass

        decision = _decide_local_play_pause_toggle(_get_local_transport_context(
            states_snapshot=_states_snapshot,
            apple_tv_entity=(_request_tv_entity or ''),
            sonos_players=SONOS_PLAYERS,
            default_sonos_room=_request_sonos_room,
            get_recent_transport_focus=_get_request_transport_focus_kind_for_local_context,
            get_last_paused_transport=lambda: getattr(process_device_commands, "_last_paused_transport", None),
            is_sonos_tv_passthrough=_is_sonos_tv_passthrough,
        ))

        try:
            logging.info("PLAY_PAUSE_DEBUG decision=%r", decision)
        except Exception:
            pass

        if not decision:
            return None

        action, target_kind, target_eid = decision
        svc = "media_player/media_pause" if action == "pause" else "media_player/media_play"

        if _call(svc, target_eid):
            if action == "pause":
                process_device_commands._last_paused_transport = target_kind
                process_device_commands._last_transport_focus = target_kind
            else:
                process_device_commands._last_transport_focus = target_kind
            try:
                _set_transport_focus_for_request(target_kind, target_eid)
            except Exception:
                pass
            logging.info("CLAIM: transport_play_pause target=%s action=%s", target_kind, action)
            return _maybe_say("Okay.")
        return None

    # Bare pause/resume/play/stop.
    #
    # Room-capability media migration:
    # - rooms with a configured TV use local TV+Sonos arbitration
    # - rooms without a configured TV are Sonos/audio-only
    # - no room should fall through to the hardcoded living-room Apple TV
    if tl in {"pause", "resume", "play", "stop"}:
        logging.info("CLAIM: transport_bare")
        verb = tl
        clear_text_confirm_context()

        if not _request_tv_entity:
            target_eid = (SONOS_PLAYERS or {}).get(_request_sonos_room)
            if not target_eid:
                return None

            if verb in {"pause", "stop"}:
                svc = "media_player/media_pause" if verb == "pause" else "media_player/media_stop"
                ok = _call(svc, target_eid)
                if ok:
                    process_device_commands._last_paused_transport = "sonos"
                    process_device_commands._last_transport_focus = "sonos"
                    _set_transport_focus_for_request("sonos", target_eid)
                    set_text_confirm_context(
                        kind="transport",
                        verb=("paused" if verb == "pause" else "stopped"),
                        entity_id=target_eid,
                        label=f"{_request_sonos_room} speaker",
                    )
                    return _maybe_say("Okay.")
                return None

            # For request-room play/resume, prefer the request room's Sonos
            # directly. HA/Sonos may return OK even if queue state is sparse,
            # but this is still the correct room target and avoids living-room TV.
            ok = _call("media_player/media_play", target_eid)
            if ok:
                process_device_commands._last_transport_focus = "sonos"
                _set_transport_focus_for_request("sonos", target_eid)
                set_text_confirm_context(
                    kind="transport",
                    verb="resumed",
                    entity_id=target_eid,
                    label=f"{_request_sonos_room} speaker",
                )
                return _maybe_say("Okay.")
            return None

        _ctx = _get_local_transport_context(
            states_snapshot=_states_snapshot,
            apple_tv_entity=(_request_tv_entity or ''),
            sonos_players=SONOS_PLAYERS,
            default_sonos_room=_request_sonos_room,
            get_recent_transport_focus=_get_request_transport_focus_kind_for_local_context,
            get_last_paused_transport=lambda: getattr(process_device_commands, "_last_paused_transport", None),
            is_sonos_tv_passthrough=_is_sonos_tv_passthrough,
        )
        tv_eid = _ctx["tv_eid"]
        sonos_eid = _ctx["sonos_eid"]
        tv_state = _ctx["tv_state"]
        sonos_state = _ctx["sonos_state"]
        sonos_is_music = _ctx["sonos_is_music"]

        # Resume/play: prefer whatever we last paused (if still paused)
        if verb in {"resume","play"}:
            last = _ctx["last_paused"]
            if last == "sonos" and sonos_eid and sonos_state in {"paused", "idle"}:
                if _call("media_player/media_play", sonos_eid):
                    process_device_commands._last_transport_focus = "sonos"
                    try:
                        _set_transport_focus_for_request("sonos", sonos_eid)
                    except Exception:
                        pass
                    set_text_confirm_context(kind="transport", verb="resumed", entity_id=sonos_eid, label=f"{_request_sonos_room} speaker")
                    return _maybe_say("Okay.")
                return None
            if last == "tv" and tv_eid and tv_state == "paused":
                if _call("media_player/media_play", tv_eid):
                    process_device_commands._last_transport_focus = "tv"
                    try:
                        _set_transport_focus_for_request("tv", tv_eid)
                    except Exception:
                        pass
                    set_text_confirm_context(kind="transport", verb="resumed", entity_id=tv_eid, label=f"{_request_sonos_room} TV")
                    return _maybe_say("Okay.")
                return None

            # fallback: if music is paused, resume it; else if tv is paused, resume tv
            if sonos_eid and sonos_is_music and sonos_state == "paused":
                if _call("media_player/media_play", sonos_eid):
                    process_device_commands._last_transport_focus = "sonos"
                    try:
                        _set_transport_focus_for_request("sonos", sonos_eid)
                    except Exception:
                        pass
                    set_text_confirm_context(kind="transport", verb="resumed", entity_id=sonos_eid, label=f"{_request_sonos_room} speaker")
                    return _maybe_say("Okay.")
                return None
            if tv_eid and tv_state == "paused":
                if _call("media_player/media_play", tv_eid):
                    process_device_commands._last_transport_focus = "tv"
                    try:
                        _set_transport_focus_for_request("tv", tv_eid)
                    except Exception:
                        pass
                    set_text_confirm_context(kind="transport", verb="resumed", entity_id=tv_eid, label=f"{_request_sonos_room} TV")
                    return _maybe_say("Okay.")
                return None
            return None

        # Pause/stop: respect last interaction (focus) when both are contenders.
        focus = _ctx["focus"]

        # If focus is TV and TV is currently playing, pause TV first.
        if focus == "tv" and tv_eid and tv_state == "playing":
            if _call("media_player/media_pause", tv_eid):
                process_device_commands._last_paused_transport = "tv"
                process_device_commands._last_transport_focus = "tv"
                try:
                    _set_transport_focus_for_request("tv", tv_eid)
                except Exception:
                    pass
                set_text_confirm_context(kind="transport", verb="paused", entity_id=tv_eid, label=f"{_request_sonos_room} TV")
                return _maybe_say("Okay.")
            return None

        # If focus is Sonos and REAL music is currently playing, pause music first.
        if focus == "sonos" and sonos_eid and sonos_is_music and sonos_state == "playing":
            if _call("media_player/media_pause", sonos_eid):
                process_device_commands._last_paused_transport = "sonos"
                process_device_commands._last_transport_focus = "sonos"
                try:
                    _set_transport_focus_for_request("sonos", sonos_eid)
                except Exception:
                    pass
                set_text_confirm_context(kind="transport", verb="paused", entity_id=sonos_eid, label=f"{_request_sonos_room} speaker")
                return _maybe_say("Okay.")
            return None

        # Fallback (original behavior): pause real music if playing, else pause TV if playing
        if sonos_eid and sonos_is_music and sonos_state == "playing":
            if _call("media_player/media_pause", sonos_eid):
                process_device_commands._last_paused_transport = "sonos"
                process_device_commands._last_transport_focus = "sonos"
                try:
                    _set_transport_focus_for_request("sonos", sonos_eid)
                except Exception:
                    pass
                set_text_confirm_context(kind="transport", verb="paused", entity_id=sonos_eid, label=f"{_request_sonos_room} speaker")
                return _maybe_say("Okay.")
            return None

        if tv_eid and tv_state == "playing":
            if _call("media_player/media_pause", tv_eid):
                process_device_commands._last_paused_transport = "tv"
                process_device_commands._last_transport_focus = "tv"
                try:
                    _set_transport_focus_for_request("tv", tv_eid)
                except Exception:
                    pass
                set_text_confirm_context(kind="transport", verb="paused", entity_id=tv_eid, label=f"{_request_sonos_room} TV")
                return _maybe_say("Okay.")
            return None

        return None

    # --- Apple TV controls ---
    apple_tv_resp = None
    if _request_tv_entity or _request_tv_remote:
        apple_tv_resp = handle_apple_tv_controls(
            tl=tl,
            states_snapshot=_states_snapshot,
            call_ha_service=call_ha_service,
            maybe_say=_maybe_say,
            entity_id=(_request_tv_entity or ""),
            remote_entity_id=(_request_tv_remote or ""),
            default_skip_seconds=APPLE_TV_DEFAULT_SKIP_SECONDS,
            get_fresh_state=ha_get_state,
        )
    if apple_tv_resp is not None:
        return apple_tv_resp

    # --- Sonos controls (transport/grouping/now-playing) ---
    sonos_resp = handle_sonos_controls(
        tl=tl,
        states_snapshot=_states_snapshot,
        call_ha_service=call_ha_service,
        maybe_say=_maybe_say,
        players_map=SONOS_PLAYERS,
        default_room=_request_sonos_room,
        get_last_master_room=_get_last_sonos_master_room,
        set_last_master_room=_set_last_sonos_master_room,
    )
    if sonos_resp is not None:
        logging.info("CLAIM: sonos_controls")
        return sonos_resp

    # --- Spotify controls (now playing / like / add to playlist) ---
    spotify_resp = handle_spotify_controls(tl, maybe_say=_maybe_say)
    if spotify_resp is not None:
        return spotify_resp

    # --- Spotify play-by-name (playlist/artist/album/track) via Sonos ---
    try:
        from app_config import PINNED_SPOTIFY_PLAYLISTS
    except Exception:
        PINNED_SPOTIFY_PLAYLISTS = {}

    _spotify_description_resolver = (
        (lambda desc: resolve_spotify_description(desc, OPENAI_CLIENT))
        if OPENAI_CLIENT is not None else None
    )
    pbn_resp = handle_play_by_name_controls(
        tl=tl,
        default_sonos_room=_request_sonos_room,
        sonos_players=SONOS_PLAYERS,
        pinned_spotify_playlists=(PINNED_SPOTIFY_PLAYLISTS or {}),
        resolve_play_request=resolve_play_request,
        resolve_typed_play_request=resolve_typed_play_request,
        call_ha_service=call_ha_service,
        maybe_say=_maybe_say_media,
        resolve_description=_spotify_description_resolver,
    )
    if pbn_resp is not None:
        return pbn_resp

# --- Spotify (Spotcast) ---
    spotcast_resp = handle_spotcast_play_controls(
        tl=tl,
        call_ha_service=call_ha_service,
        maybe_say=_maybe_say,
        default_device_name=(get_room_spotcast_device_name(_request_sonos_room) or ""),
        device_aliases=get_spotcast_device_aliases(),
    )
    if spotcast_resp is not None:
        logging.info("CLAIM: spotcast_play")
        return spotcast_resp

    # =====================================================================
    # MODULE LIGHT CONTROLS (preferred; legacy inline remains as fallback)
    # =====================================================================
    if USE_MODULE_LIGHT_CONTROLS:
        if any(k in tl for k in (
            "light", "lights", "lamp", "brightness", "bright",
            "dim", "dark", "color", "colour",
            "kelvin", "warm", "cool"
        )):
            logging.info(f"DEBUG: entering module-light controls tl={tl!r}")
        # Resolve light targets with HA_DEVICE_ALIASES support (via _resolve_device_entity), then fall back.
        # This makes aliases like 'starlight' work consistently for brightness/color/kelvin/rgb.
        def _resolve_light_target_aliases(phrase: str):
            # Pronouns must always resolve via recent-light context — never via entity
            # token matching, because short words like "it" substring-match entity
            # names ("kitchen" contains "it") and pick the wrong device.
            _PRONOUNS = {"it", "that", "this", "them", "those", "these"}
            if (phrase or "").strip().lower() not in _PRONOUNS:
                try:
                    r = _resolve_device_entity_trace(phrase, _states_snapshot)
                    if r and isinstance(r, tuple) and len(r) >= 2:
                        eid, domain = r[0], r[1]
                        if domain == "light" and isinstance(eid, str):
                            return (eid, False)
                except Exception:
                    pass
            return _resolve_light_target(
                phrase,
                light_phrase_overrides=get_brightness_light_phrase_overrides(),
                get_recent_light=_get_recent_light,
                entity_exists=lambda eid: _entity_exists(eid, _states_snapshot),
                logger=logging,
            )

        def _resolve_kelvin_target(phrase: str):
            # Color temperature can also target the per-room virtual "color"
            # light (the same entity "set color to X" drives), not just named
            # lights. Map "color"/"<room> color" to configured room entities; everything
            # else falls back to normal light-target resolution.
            color_lights = get_room_color_light_map()
            p = re.sub(r"^(?:the|my)\s+", "", (phrase or "").strip().lower()).strip()
            if p in ("color", "colour"):
                eid = (
                    get_room_default_for_request("color_light", fallback=None)
                    or color_lights.get((get_default_room_id() or "").replace("_", " "))
                )
                return (eid, True) if eid else (None, False)
            m = re.fullmatch(r"(.+?)\s+colou?rs?", p)
            if m:
                rk = m.group(1).strip()
                eid = color_lights.get(rk)
                if not eid:
                    rid = find_room_by_alias(rk) or ""
                    eid = color_lights.get(rid.replace("_", " ").strip())
                if eid:
                    return (eid, False)
            return _resolve_light_target_aliases(phrase)

        # Generic room "lights <level/color/temp>" → real area devices (not the
        # virtual brightness/color helpers). Runs before kelvin/brightness/color
        # so it claims "set the lights to blue/50%/warm white" first and targets
        # the room area; named lights ("stair light blue"), keyword
        # "brightness/color X" commands, and on/off all fall through unclaimed.
        room_lights_resp = handle_room_lights_controls(
            tl=tl,
            call_ha_service=call_ha_service,
            maybe_say=_maybe_say,
            remember_light=_remember_light,
        )
        if room_lights_resp is not None:
            logging.info("CLAIM: room_lights_controls")
            return room_lights_resp

        # Kelvin first, so "set X to 3000k" doesn't get stolen by brightness/color.
        # Uses _resolve_kelvin_target so "color"/"<room> color" also accept kelvin.
        kelvin_resp = handle_kelvin_controls(
            tl=tl,
            call_ha_service=call_ha_service,
            maybe_say=_maybe_say,
            resolve_light_target=_resolve_kelvin_target,
            remember_light=_remember_light,
            try_light_turn_on=lambda entity_id, payloads: _try_light_turn_on(entity_id, payloads, call_ha_service=call_ha_service),
        )
        if kelvin_resp is not None:
            logging.info("CLAIM: kelvin_controls")
            return kelvin_resp

        # RGB/HEX before named colors (explicit formats)
        rgbhex_resp = handle_rgb_hex_controls(
            tl=tl,
            call_ha_service=call_ha_service,
            maybe_say=_maybe_say,
            resolve_light_target=_resolve_light_target_aliases,
            remember_light=_remember_light,
            try_light_turn_on=lambda entity_id, payloads: _try_light_turn_on(entity_id, payloads, call_ha_service=call_ha_service),
        )
        if rgbhex_resp is not None:
            logging.info("CLAIM: rgb_hex_controls")
            return rgbhex_resp

        # Brightness (ordered, with overlap guards inside module)
        _ppchat_brightness_ctx = None
        _m_bri_global = (
            re.search(r"\bset\s+brightness(?:es)?\s+(?:to\s+)?(\d{1,3})\s*%?\b", tl)
            or re.search(r"\bbrightness(?:es)?\s+(\d{1,3})\s*%?\b", tl)
        )
        if _m_bri_global:
            try:
                _ppchat_brightness_ctx = int(_m_bri_global.group(1))
            except Exception:
                _ppchat_brightness_ctx = None

        bri_resp = handle_brightness_controls(
            tl=tl,
            states_snapshot=_states_snapshot,
            call_ha_service=call_ha_service,
            maybe_say=_maybe_say,
            resolve_light_target=_resolve_light_target_aliases,
            remember_light=_remember_light,
            get_recent_light=_get_recent_light,
        )
        if bri_resp is not None and _ppchat_brightness_ctx is not None:
            brightness_room = (
                _explicit_room_id_for_request
                or get_active_room_for_request_defaults()
                or get_default_room_id()
            )
            set_text_confirm_context(
                kind="brightness",
                value=_ppchat_brightness_ctx,
                label=f"{get_room_label(brightness_room) or 'Room'} brightness",
            )
        if bri_resp is not None:
            logging.info(f"DEBUG: bri_resp={bri_resp!r}")
            logging.info("CLAIM: brightness_controls")
            return bri_resp

        # Named colors (with optional AI slot-fill for evocative descriptions)
        _color_resolver = (
            (lambda desc: resolve_color_description(desc, OPENAI_CLIENT))
            if OPENAI_CLIENT is not None else None
        )
        color_resp = handle_color_controls(
            tl=tl,
            call_ha_service=call_ha_service,
            maybe_say=_maybe_say,
            resolve_light_target=_resolve_light_target_aliases,
            remember_light=_remember_light,
            color_lights=get_room_color_light_map(),
            default_color_room=(get_default_room_id() or "").replace("_", " "),
            resolve_color=_color_resolver,
        )
        if color_resp is not None:
            logging.info("CLAIM: color_controls")
            return color_resp

        # On/Off (generic device turn_on/turn_off via HA states)
        onoff_resp = handle_on_off_controls(
            tl=tl,
            call_ha_service=call_ha_service,
            maybe_say=_maybe_say,
            resolve_device_entity=lambda phrase: _resolve_device_entity_with_context(
                phrase,
                _states_snapshot,
                capability="binary_control",
            ),
            states_snapshot=_states_snapshot,
            remember_entity=lambda eid, domain: _remember_resolved_entity(
                eid,
                domain,
                source="device_action",
            ),
        )
        if onoff_resp is not None:
            logging.info("CLAIM: on_off_controls")
            return onoff_resp

        # Toggle (atomic via HA <domain>/toggle service)
        toggle_resp = handle_toggle_controls(
            tl=tl,
            call_ha_service=call_ha_service,
            maybe_say=_maybe_say,
            resolve_device_entity=lambda phrase: _resolve_device_entity_with_context(
                phrase,
                _states_snapshot,
                capability="toggle_control",
            ),
            remember_entity=lambda eid, domain: _remember_resolved_entity(
                eid,
                domain,
                source="device_action",
            ),
        )
        if toggle_resp is not None:
            logging.info("CLAIM: toggle_controls")
            return toggle_resp

        # Locks (lock/unlock via HA lock domain)
        lock_resp = handle_lock_controls(
            tl=tl,
            call_ha_service=call_ha_service,
            maybe_say=_maybe_say,
            resolve_device_entity=lambda phrase: _resolve_device_entity_with_context(
                phrase,
                _states_snapshot,
                capability="lock_control",
            ),
            remember_entity=lambda eid, domain: _remember_resolved_entity(
                eid,
                domain,
                source="device_action",
            ),
        )
        if lock_resp is not None:
            logging.info("CLAIM: lock_controls")
            return lock_resp
    # =====================================================================

    # --- PHONETIC PASS2: device-noun repairs (risky) ---
    # Only try if pass1 did not claim anything AND the utterance looks device-likely.
    if _repair_pass == 1:
        try:
            if not globals().get("_ACTION_OCCURRED", False):
                should_try, reason = _should_try_device_repairs_pass2(_text_after_routing, sonos_players=SONOS_PLAYERS)
                if should_try:
                    _r2 = _apply_phonetic_device_repairs(_text_after_routing, sonos_players=SONOS_PLAYERS)
                    if _r2 != _text_after_routing:
                        logging.info("UTTERANCE_REPAIR_DEVICE: %r -> %r (reason=%s)", _text_after_routing, _r2, reason)
                        logging.info("REPAIR_PASS2_DECISION: try=True reason=%s", reason)
                        return process_device_commands(_r2, _repair_pass=2)
                    else:
                        logging.info("REPAIR_PASS2_DECISION: try=False reason=%s (no_change)", reason)
        except Exception as e:
            logging.debug("REPAIR_PASS2_EXCEPTION: %s", e)

    return None


def process_device_commands(text: str, *, _repair_pass: int = 1) -> Optional[str]:
    """Run the ordered device pipeline, including at most one repair retry."""
    _dispatch_timing_begin(text, _repair_pass)
    result = None
    error = None
    try:
        result = _process_device_commands_impl(text, _repair_pass=_repair_pass)
        return result
    except Exception as exc:
        error = exc
        raise
    finally:
        _dispatch_timing_end(result, error)


# =========================
# STATE RESET
# =========================

def reset_dispatch_state():
    """Reset all command-dispatch module state. Called by gpio_ptt.reset_session()."""
    global last_light_entity_id, last_light_updated_ts, last_spoken_text
    global _last_light_group, _last_light_group_cmd_id, _last_light_group_ts
    global _last_location_query, _last_location_query_ts
    global _ACTION_OCCURRED
    last_light_entity_id = None
    last_light_updated_ts = 0
    last_spoken_text = None
    _last_light_group = []
    _last_light_group_cmd_id = -1
    _last_light_group_ts = 0.0
    _last_location_query = None
    _last_location_query_ts = 0.0
    _ACTION_OCCURRED = False
    reset_dialogue_state()
