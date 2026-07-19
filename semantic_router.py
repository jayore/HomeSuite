"""Classify unclaimed text as deterministic work, conversation, or noise.

Recognizable local-control language always stays with the deterministic
pipeline. Meaningful language that remains unclaimed can use ChatGPT, while a
small voice-specific debris guard avoids turning stray capture fragments into
conversation. This module never resolves entities or executes actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import re
import time

from app_config import CHATGPT_CONTINUATION_WINDOW_SECONDS, ROOMS
from astronomy_controls import looks_like_astronomy_query
from date_controls import looks_like_date_query
from homelab_controls import looks_like_homelab_query
from location_controls import looks_like_location_query
from stock_quote_controls import looks_like_stock_query
from weather_utils import looks_like_weather_query
from calendar_controls import looks_like_calendar_request
from pending_controls import looks_like_pending_query
from temporary_actions import looks_like_temporary_action_request
from conversational_nl import normalize_conversational_command


class RouteOutcome(str, Enum):
    DEVICE = "device"
    CHATGPT = "chatgpt"
    ERROR = "error"


@dataclass(frozen=True)
class RouteResult:
    outcome: RouteOutcome


_GREETING_PAT = re.compile(r"^(hi|hello|hey|greetings|yo|sup|what's up|whats up)$")
_FAREWELL_PAT = re.compile(r"^(bye|goodbye|see you|see ya|later|good night|night)$")
_ACK_PAT = re.compile(r"^(thanks|thank you|ok|okay|alright|cool|nice|great|awesome|sweet|perfect|interesting|got it|sounds good)$")
_CONVO_START_PAT = re.compile(
    r"\b(what|why|how|when|where|who)\b|"
    r"\b(tell me|teach me|explain|give me|share|can you|could you|would you|do you|is it|are you)\b"
)

_DEVICEISH_PATTERNS = [
    re.compile(r"\b(turn|switch)\s+(on|off)\b"),
    re.compile(r"^(?:turn|switch)\s+.+\s+(?:back\s+)?(?:on|off)$"),
    re.compile(r"\bset\s+.+\b"),
    re.compile(r"\b(brightness|volume|color|kelvin)\b"),
    re.compile(r"\b(rgb|#?[0-9a-f]{6}|\d{4,5}\s*k)\b"),
    re.compile(r"\b(skip|rewind|fast forward|forward)\b"),
    re.compile(r"^(?:please\s+)?(?:pause|resume|ungroup)$"),
    re.compile(r"^(?:please\s+)?(?:next|previous)\s+(?:track|video|episode)$"),
    re.compile(r"^(?:please\s+)?(?:play|watch)\b"),
    re.compile(r"^(?:please\s+)?(?:say|announce|run)\b"),
    re.compile(r"^(?:please\s+)?(?:lock|unlock)\b"),
    re.compile(r"^(?:please\s+)?(?:open|close)\b.*\b(?:door|doors|blinds|shades|cover|covers|curtains|garage|gate)\b"),
    re.compile(r"^(?:please\s+)?(?:start|pause|stop|send|dock|locate)\b.*\bvacuum\b"),
    re.compile(r"^(?:please\s+)?(?:increase|decrease|raise|lower)\b.*\b(?:fan|thermostat|temperature|speed)\b"),
    re.compile(r"^(?:please\s+)?(?:dim|brighten)\b"),
    re.compile(r"^(?:please\s+)?make\b.*\b(?:light|lights|lamp|lamps|it)\b"),
    re.compile(r"^(?:please\s+)?(?:mute|unmute|group|switch)\b.*\b(?:tv|sonos|speaker|room|kitchen|bedroom|office|living)\b"),
    re.compile(r"^(?:please\s+)?save\s+this\s+(?:song|track)$"),
    re.compile(r"^(?:i(?:'m| am)\s+in|where am i|clear my room focus)\b"),
    re.compile(r"\b(?:alarm|alarms|timer|timers|reminder|reminders|snooze)\b"),
    re.compile(r"\b(?:scheduled(?: actions?)?|schedules?)\b"),
    re.compile(
        r"^(?:(?:ok|okay|alright|sure|right|got it)\s*,?\s*)?"
        r"(?:(?:add|put|subtract|remove|take)\b.*\b(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)\b.*"
        r"\b(?:it|that|this)(?: one)?|"
        r"(?:set|change|reset)\s+(?:it|that|this)(?: one)?\s+(?:to|for)\s+.*"
        r"\b(?:seconds?|secs?|minutes?|mins?|hours?|hrs?))$"
    ),
    re.compile(
        r"^(?:(?:ok|okay|alright|sure|right|got it)\s*,?\s*)?"
        r"(?:how much time(?:\s+is)?\s+(?:left|remaining)(?:\s+on\s+(?:it|that|this)(?: one)?)?|"
        r"how long(?:\s+does\s+(?:it|that|this)(?: one)?\s+have)?\s+left|"
        r"(?:cancel|delete)\s+(?:it|that|this)(?: one)?)$"
    ),
    re.compile(r"^(?:please\s+)?(?:wake me up|remind me)\b"),
    re.compile(r"\b(?:sunrise|sunset)\b"),
    re.compile(r"^(?:say that again|repeat that|what did you say)$"),
    re.compile(r"\b(?:youtube|daily reel|digest)\b"),
    re.compile(r"\bwhat(?:'s| is)\s+playing\b"),
    re.compile(r"\bwhat\s+is\s+it\s+about\b"),
    re.compile(r"\bwhat\s+lights?\s+are\s+on\b"),
    re.compile(r"\bhow\s+open\s+are\b.*\b(?:blinds|shades|cover|covers|curtains)\b"),
    re.compile(r"\b(?:battery|fan speed|vacuum doing)\b"),
    re.compile(r"\bwhat(?:'s| is)\s+the\s+temperature\b"),
    re.compile(r"\bwhats\s+the\s+temperature\b"),
    re.compile(r"\bhow\s+(hot|cold)\b"),
    re.compile(r"\btemperature\b"),
]

# Imperative verbs remain deterministic even when entity resolution fails.
# This prevents an open-ended model from claiming that an unknown action ran.
_ACTION_LEAD_PAT = re.compile(
    r"^(?:please\s+)?(?:"
    r"activate|deactivate|enable|disable|turn|switch|set|dim|brighten|"
    r"lock|unlock|open|close|start|stop|pause|resume|play|watch|"
    r"mute|unmute|increase|decrease|raise|lower|run|announce|say"
    r")\b"
)


_DEVICE_STATE_PATTERNS = [
    # Lights / switches
    re.compile(r"^(is|are)\s+.+\s+(on|off)\b"),
    # Locks
    re.compile(r"^(is|are)\s+.+\s+(locked|unlocked)\b"),
    # Doors/windows/garage open/closed (keep conservative so we don't steal 'is X open?' for businesses)
    re.compile(r"^(is|are)\s+.*\b(door|doors|window|windows|garage|gate)\b.*\b(open|closed)\b"),
]

# Sensor queries we want to route to DEVICE only when the user is clearly asking "at home / inside".
_SENSOR_WORDS = ("temperature", "humidity", "temp")
_INSIDE_MARKERS = (
    "inside",
    "indoor",
    "in here",
    "in the house",
    "at home",
)


def _configured_room_phrases() -> set[str]:
    phrases = set()
    for room_id, room in (ROOMS or {}).items():
        phrases.add(str(room_id).strip().lower().replace("_", " "))
        if isinstance(room, dict):
            phrases.update(
                str(alias).strip().lower()
                for alias in (room.get("aliases") or [])
                if str(alias).strip()
            )
    return phrases


def _looks_device_state_question(t: str) -> bool:
    if not t:
        return False
    for p in _DEVICE_STATE_PATTERNS:
        if p.search(t):
            return True

    # "what's the temperature inside", "temperature in the living room", etc.
    if any(w in t for w in _SENSOR_WORDS):
        if any(m in t for m in _INSIDE_MARKERS) or any(
            room in t for room in _configured_room_phrases()
        ):
            return True

    return False

_LOCAL_UTILITY_PATTERNS = [
    re.compile(r"\bwhat(?:'s| is)\s+the\s+time\b"),
    re.compile(r"\bwhat\s+time\s+is\s+it\b"),
    re.compile(r"\btell\s+me\s+the\s+time\b"),
    re.compile(r"\bwhat(?:'s| is)\s+the\s+weather\b"),
    re.compile(r"\bweather\b"),
    re.compile(r"\bforecast\b"),
]

_VOICE_SOURCE_TYPES = {
    "audio",
    "microphone",
    "piphone",
    "ptt",
    "satellite",
    "voice",
    "wakeword",
}
_DEBRIS_TOKENS = {
    "a", "ah", "an", "and", "but", "eh", "er", "err", "hal",
    "hm", "hmm", "huh", "mm", "mmm", "oh", "or", "please", "so",
    "the", "uh", "um", "umm", "well",
}
_WORD_PAT = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[.!,?]+$", "", s).strip()
    return s


def _looks_local_utility(t: str) -> bool:
    return (
        looks_like_astronomy_query(t)
        or looks_like_date_query(t)
        or looks_like_location_query(t)
        or looks_like_stock_query(t)
        or looks_like_weather_query(t)
        or looks_like_calendar_request(t)
        or looks_like_pending_query(t)
        or looks_like_temporary_action_request(t)
        or any(p.search(t) for p in _LOCAL_UTILITY_PATTERNS)
    )


def _looks_deviceish(t: str) -> bool:
    if not t:
        return False
    if looks_like_homelab_query(t):
        return True
    if _ACTION_LEAD_PAT.search(t):
        return True
    for p in _DEVICEISH_PATTERNS:
        if p.search(t):
            return True
    if _looks_device_state_question(t):
        return True
    # Single room/scene-ish words should bias DEVICE (and error if no action exists)
    if t in _configured_room_phrases() or t == "movie":
        return True
    return False


def _looks_conversational(t: str) -> bool:
    if "?" in t:
        return True
    if _GREETING_PAT.fullmatch(t):
        return True
    if _FAREWELL_PAT.fullmatch(t):
        return True
    if _ACK_PAT.fullmatch(t):
        return True
    if _CONVO_START_PAT.search(t):
        return True
    return False


def _word_tokens(t: str) -> list[str]:
    return _WORD_PAT.findall(t)


def _meaningful_tokens(t: str) -> list[str]:
    return [token for token in _word_tokens(t) if token not in _DEBRIS_TOKENS]


def _looks_like_capture_debris(t: str) -> bool:
    tokens = _word_tokens(t)
    return not tokens or not _meaningful_tokens(t)


def _is_voice_source(source_type: Optional[str]) -> bool:
    source = str(source_type or "").strip().lower().replace("-", "_")
    if not source:
        return False
    return source in _VOICE_SOURCE_TYPES or any(
        marker in source for marker in ("wakeword", "satellite", "microphone")
    )


def _is_recent_chatgpt_turn(now_ts: float, last_chatgpt_ts: Optional[float]) -> bool:
    if last_chatgpt_ts is None:
        return False
    age = now_ts - float(last_chatgpt_ts)
    try:
        window = max(0.0, float(CHATGPT_CONTINUATION_WINDOW_SECONDS))
    except (TypeError, ValueError):
        window = 120.0
    return 0.0 <= age <= window


def route_utterance(
    *,
    text: str,
    now_ts: Optional[float] = None,
    last_chatgpt_ts: Optional[float] = None,
    source_type: Optional[str] = None,
) -> RouteResult:
    """Classify one utterance after deterministic handlers have declined it."""
    if now_ts is None:
        now_ts = time.time()

    t = _norm(normalize_conversational_command(text))
    if not t:
        return RouteResult(RouteOutcome.ERROR)

    if _looks_local_utility(t):
        return RouteResult(RouteOutcome.DEVICE)

    if _looks_deviceish(t):
        return RouteResult(RouteOutcome.DEVICE)

    if _looks_like_capture_debris(t):
        return RouteResult(RouteOutcome.ERROR)

    if _looks_conversational(t):
        return RouteResult(RouteOutcome.CHATGPT)

    # Within an AI exchange, even a one-word fragment can be meaningful. The
    # timestamp is supplied by the caller's source-scoped conversation state.
    if _is_recent_chatgpt_turn(now_ts, last_chatgpt_ts):
        return RouteResult(RouteOutcome.CHATGPT)

    # Typed surfaces can safely be permissive because they do not contain VAD
    # tails or wake-word capture debris. For voice, require at least a short
    # phrase unless the utterance is explicitly conversational or continues a
    # recent AI exchange.
    if not _is_voice_source(source_type):
        return RouteResult(RouteOutcome.CHATGPT)
    if len(_word_tokens(t)) >= 2:
        return RouteResult(RouteOutcome.CHATGPT)

    return RouteResult(RouteOutcome.ERROR)
