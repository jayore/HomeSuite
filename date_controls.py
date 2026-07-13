"""Parse and format deterministic current-date questions.

The parser deliberately recognizes only requests for today's calendar date.
Questions about holidays, historical dates, or date arithmetic remain available
to the conversational path instead of being claimed by an incomplete handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Optional


_LOCATION_SUFFIX = (
    r"(?:\s+in\s+(?P<location>.+?)|\s+(?P<there>there|over\s+there))?"
)
_DATE_PATTERNS = (
    re.compile(
        rf"^what(?:'s|s|\s+is)\s+(?:the\s+date|today's\s+date|todays\s+date)"
        rf"{_LOCATION_SUFFIX}$"
    ),
    re.compile(
        rf"^what\s+(?:date|day)\s+is\s+it{_LOCATION_SUFFIX}$"
    ),
    re.compile(
        rf"^what\s+day\s+of\s+the\s+week\s+is\s+it{_LOCATION_SUFFIX}$"
    ),
    re.compile(
        rf"^tell\s+me\s+(?:the\s+date|today's\s+date|todays\s+date)"
        rf"{_LOCATION_SUFFIX}$"
    ),
)


@dataclass(frozen=True)
class DateQuery:
    """A request for the current date, optionally at a named location."""

    location: Optional[str] = None


def _normalize(text: str) -> str:
    value = str(text or "").lower().replace("’", "'").replace("‘", "'")
    value = re.sub(r"\s+", " ", value).strip(" .,!?")
    return value


def parse_date_query(text: str) -> Optional[DateQuery]:
    """Return a current-date query when the whole utterance is recognized."""

    normalized = _normalize(text)
    for pattern in _DATE_PATTERNS:
        match = pattern.fullmatch(normalized)
        if not match:
            continue
        location = (match.groupdict().get("location") or "").strip() or None
        return DateQuery(location=location)
    return None


def looks_like_date_query(text: str) -> bool:
    """Report whether text belongs to the deterministic date handler."""

    return parse_date_query(text) is not None


def format_date_response(value: Optional[datetime] = None) -> str:
    """Format a natural spoken response containing weekday and full date."""

    current = value or datetime.now()
    return (
        f"Today is {current.strftime('%A, %B')} "
        f"{current.day}, {current.year}."
    )
