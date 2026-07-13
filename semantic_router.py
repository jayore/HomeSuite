"""Classify text as deterministic device work or general conversation.

The router is intentionally conservative: recognizable local-control language
goes to the device pipeline, clearly conversational language goes to ChatGPT,
and ambiguous cases retain enough context for the caller to choose a fallback.
It does not resolve entities or execute actions; those guarantees belong to
``command_dispatch``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import re
import time

from app_config import ROOMS
from astronomy_controls import looks_like_astronomy_query
from date_controls import looks_like_date_query
from stock_quote_controls import looks_like_stock_query


class RouteOutcome(str, Enum):
    DEVICE = "device"
    CHATGPT = "chatgpt"
    ERROR = "error"


@dataclass(frozen=True)
class RouteResult:
    outcome: RouteOutcome


CHATGPT_CONTINUATION_WINDOW_SECONDS = 60.0

_GREETING_PAT = re.compile(r"^(hi|hello|hey|greetings|yo|sup|what's up|whats up)$")
_FAREWELL_PAT = re.compile(r"^(bye|goodbye|see you|see ya|later|good night|night)$")
_ACK_PAT = re.compile(r"^(thanks|thank you|ok|okay|alright|cool|nice|great|awesome|sweet|perfect|interesting|got it|sounds good)$")
_CONVO_START_PAT = re.compile(
    r"\b(what|why|how|when|where|who)\b|"
    r"\b(tell me|teach me|explain|give me|share|can you|could you|would you|do you|is it|are you)\b"
)

_DEVICEISH_PATTERNS = [
    re.compile(r"\b(turn|switch)\s+(on|off)\b"),
    re.compile(r"\bset\s+.+\b"),
    re.compile(r"\b(brightness|volume|color|kelvin)\b"),
    re.compile(r"\b(rgb|#?[0-9a-f]{6}|\d{4,5}\s*k)\b"),
    re.compile(r"\b(skip|rewind|fast forward|forward)\b"),
    re.compile(r"\bwhat(?:'s| is)\s+the\s+temperature\b"),
    re.compile(r"\bwhats\s+the\s+temperature\b"),
    re.compile(r"\bhow\s+(hot|cold)\b"),
    re.compile(r"\btemperature\b"),
]

_DEVICEISH_PATTERNS = [
    re.compile(r"\b(turn|switch)\s+(on|off)\b"),
    re.compile(r"\bset\s+.+\b"),
    re.compile(r"\b(brightness|volume|color|kelvin)\b"),
    re.compile(r"\b(rgb|#?[0-9a-f]{6}|\d{4,5}\s*k)\b"),
    re.compile(r"\b(skip|rewind|fast forward|forward)\b"),
    re.compile(r"\bwhat(?:'s| is)\s+the\s+temperature\b"),
    re.compile(r"\bwhats\s+the\s+temperature\b"),
    re.compile(r"\bhow\s+(hot|cold)\b"),
    re.compile(r"\btemperature\b"),
]


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

_CONTINUATION_PAT = re.compile(r"^(another one|another|one more|more|again|why|really|go on)$")


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[.!,]+$", "", s).strip()
    return s


def _looks_local_utility(t: str) -> bool:
    return (
        looks_like_astronomy_query(t)
        or looks_like_date_query(t)
        or looks_like_stock_query(t)
        or any(p.search(t) for p in _LOCAL_UTILITY_PATTERNS)
    )


def _looks_deviceish(t: str) -> bool:
    if not t:
        return False
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


def _looks_chatgpt_continuation(t: str) -> bool:
    return bool(_CONTINUATION_PAT.fullmatch(t))


def route_utterance(
    *,
    text: str,
    now_ts: Optional[float] = None,
    last_chatgpt_ts: Optional[float] = None,
) -> RouteResult:
    """Classify one utterance using lexical intent and recent conversation."""
    if now_ts is None:
        now_ts = time.time()

    t = _norm(text)
    if not t:
        return RouteResult(RouteOutcome.ERROR)

    if _looks_local_utility(t):
        return RouteResult(RouteOutcome.DEVICE)

    if _looks_deviceish(t):
        return RouteResult(RouteOutcome.DEVICE)

    if _looks_conversational(t):
        return RouteResult(RouteOutcome.CHATGPT)

    if last_chatgpt_ts and (now_ts - last_chatgpt_ts) <= CHATGPT_CONTINUATION_WINDOW_SECONDS:
        if _looks_chatgpt_continuation(t):
            return RouteResult(RouteOutcome.CHATGPT)

    return RouteResult(RouteOutcome.ERROR)
