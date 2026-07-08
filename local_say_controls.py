from __future__ import annotations

import re
from typing import Optional


_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"say(?:\s+(?:back|out\s+loud|aloud))?"
    r"|speak"
    r"|repeat(?:\s+(?:back|after\s+me|the\s+(?:phrase|sentence|words?)))"
    r")\s+(.+?)\s*[.?!]*\s*$",
    re.IGNORECASE,
)

_BLOCKED_MESSAGES = {
    "that",
    "it",
    "that again",
    "it again",
    "again",
}


def handle_local_say_controls(text: str) -> Optional[str]:
    """
    Return text to be spoken locally for simple TTS diagnostics.

    This intentionally only extracts the requested phrase. The existing local
    speak_text path handles actual playback and TTS normalization.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    match = _PREFIX_RE.match(raw)
    if not match:
        return None

    phrase = re.sub(r"\s+", " ", match.group(1)).strip(" \"'")
    phrase = re.sub(r"\s*[.?!]\s*$", "", phrase).strip()
    if not phrase:
        return None

    if phrase.lower() in _BLOCKED_MESSAGES:
        return None

    return phrase
