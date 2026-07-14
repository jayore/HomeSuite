"""Fail closed when action-oriented timing language reaches immediate routing."""

from __future__ import annotations

import re
from typing import Optional


TEMPORAL_SAFETY_RESPONSE = (
    "I noticed timing language I couldn't safely interpret, so I left everything unchanged."
)

_ACTION_RE = re.compile(
    r"^(?:please\s+)?(?:turn|switch|set|make|change|dim|brighten|toggle|"
    r"open|close|lock|unlock|run)\b"
)
_NUMBER_RE = (
    r"(?:\d{1,6}|a|an|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty(?:[\s-]+(?:one|two|three|four|five|six|seven|eight|nine))?|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety)"
)
_WEEKDAY_RE = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_MONTH_RE = (
    r"(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)"
)
_TIME_FIRST_RE = re.compile(
    rf"^(?:in\s+(?:the\s+next\s+)?{_NUMBER_RE}\s+[a-z]+|"
    rf"tomorrow|next\s+(?:week|month|year|{_WEEKDAY_RE})|"
    rf"on\s+(?:{_WEEKDAY_RE}|{_MONTH_RE}\s+\d{{1,2}}))\b.*\b"
    r"(?:turn|switch|set|make|change|dim|brighten|toggle|open|close|lock|unlock|run)\b"
)
_DATE_MARKER_RE = re.compile(
    rf"\b(?:tomorrow|next\s+(?:week|month|year|{_WEEKDAY_RE})|"
    rf"on\s+(?:{_WEEKDAY_RE}|{_MONTH_RE}\s+\d{{1,2}})|"
    r"at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b"
)


def guard_unconsumed_temporal_action(text: str) -> Optional[str]:
    """Return a safety response only for unclaimed, timed device actions."""
    normalized = str(text or "").strip().lower().replace("’", "'")
    normalized = re.sub(r"[?!.]+$", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return None

    if _TIME_FIRST_RE.search(normalized):
        return TEMPORAL_SAFETY_RESPONSE
    if not _ACTION_RE.search(normalized):
        return None
    if re.search(
        rf"\b(?:for|in)\s+(?:the\s+next\s+)?{_NUMBER_RE}\s+[a-z]+\b",
        normalized,
    ):
        return TEMPORAL_SAFETY_RESPONSE
    if _DATE_MARKER_RE.search(normalized):
        return TEMPORAL_SAFETY_RESPONSE
    return None
