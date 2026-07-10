"""Normalize readable responses at the final text-to-speech boundary.

Command handlers should return ordinary human-readable text. Immediately before
speech, this module expands known pronunciations and units, removes unsupported
markup, regularizes pauses, and splits text into gTTS-sized chunks. Text sent to
Telegram, HTTP clients, logs, or other displays should bypass these transforms.
"""

from __future__ import annotations

import re
from typing import List, Mapping, Optional


_BUILTIN_PRONUNCIATIONS = {
    "qBittorrent": "cue bit torrent",
    "qbittorrent": "cue bit torrent",
    "Seerr": "seer",
    "seerr": "seer",
    "YoreNAS": "Yore N A S",
    "NAS": "N A S",
    "DSM": "D S M",
    "CPU": "C P U",
    "GPU": "G P U",
    "RAM": "ram",
    "USB": "U S B",
    "HA": "Home Assistant",
    "STT": "S T T",
    "TTS": "T T S",
    "API": "A P I",
    "HTTP": "H T T P",
    "HTTPS": "H T T P S",
    "DNS": "D N S",
    "TCP": "T C P",
    "UDP": "U D P",
    "IP": "I P",
    "LAN": "lan",
    "WAN": "wan",
    "WiFi": "Wi Fi",
    "wifi": "Wi Fi",
    "M.2": "M dot 2",
}


_UNIT_REPLACEMENTS = [
    (re.compile(r"(\d+(?:,\d{3})+)\s*light[-\s]?years?\b", re.I), r"\1 light years"),
    (re.compile(r"(\d+(?:\.\d+)?)\s+(million|billion|trillion)\s*light[-\s]?years?\b", re.I), r"\1 \2 light years"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*light[-\s]?years?\b", re.I), r"\1 light years"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*°\s*F\b", re.I), r"\1 degrees Fahrenheit"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*°\s*C\b", re.I), r"\1 degrees Celsius"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*%"), r"\1 percent"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*kB/s\b", re.I), r"\1 kilobytes per second"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*KiB/s\b", re.I), r"\1 kibibytes per second"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*MB/s\b", re.I), r"\1 megabytes per second"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*MiB/s\b", re.I), r"\1 mebibytes per second"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*GB/s\b", re.I), r"\1 gigabytes per second"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*Gbps\b", re.I), r"\1 gigabits per second"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*Mbps\b", re.I), r"\1 megabits per second"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*Kbps\b", re.I), r"\1 kilobits per second"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*ms\b", re.I), r"\1 milliseconds"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*km\b", re.I), r"\1 kilometers"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*mi\b", re.I), r"\1 miles"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*mph\b", re.I), r"\1 miles per hour"),
]


def _apply_word_replacements(text: str, replacements: Mapping[str, str]) -> str:
    for source, target in replacements.items():
        if not isinstance(source, str) or not source:
            continue
        if not isinstance(target, str):
            continue
        text = re.sub(rf"(?<!\w){re.escape(source)}(?!\w)", target, text)
    return text


def _expand_dotted_acronyms(text: str) -> str:
    def repl(match: re.Match) -> str:
        return " ".join(re.findall(r"[A-Za-z]", match.group(0)))

    return re.sub(r"\b(?:[A-Za-z]\.){2,}", repl, text)


def _smooth_known_phrases(text: str) -> str:
    text = re.sub(r"\bcollide,\s+with\b", "collide with", text, flags=re.I)
    return text


def _strip_markdown_for_tts(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.M)
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    text = re.sub(r"(?<!\w)(\*|_)([^*_]+)\1(?!\w)", r"\2", text)
    text = text.replace("*", " ")
    return text


def _remove_non_pause_commas(text: str) -> str:
    """
    Remove modifier commas that are useful for reading but awkward in speech.
    Numeric comma handling and broad comma stripping happen separately.
    """
    non_pause_modifiers = (
        "single",
        "larger",
        "smaller",
        "nearby",
        "neighboring",
        "central",
        "barred",
        "spiral",
        "elliptical",
        "massive",
        "supermassive",
    )
    modifier_pattern = "|".join(non_pause_modifiers)
    return re.sub(
        rf"\b({modifier_pattern}),\s+(({modifier_pattern})(?:\s+[a-z]+)?)\b",
        r"\1 \2",
        text,
        flags=re.I,
    )


def _prepare_commas_for_tts(text: str) -> str:
    numeric_comma = "__PIPHONE_NUMERIC_COMMA__"
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", numeric_comma, text)
    text = _remove_non_pause_commas(text)
    text = re.sub(r"\s*,\s*", " ", text)
    return text.replace(numeric_comma, ",")


def _split_long_gtts_chunk(text: str, *, max_len: int = 95) -> List[str]:
    chunks = []
    chunk = text.strip()
    while len(chunk) > max_len:
        window = chunk[:max_len]
        matches = list(re.finditer(
            r"\s(?:meaning|including|known as|located|while|because|which|due to|and|or|but|the presence)\b",
            window,
            flags=re.I,
        ))
        matches = [m for m in matches if m.start() >= 45]
        if matches:
            pos = matches[-1].start()
        else:
            fallback = window.rfind(" ")
            if fallback < 45:
                break
            pos = fallback
        chunks.append(chunk[:pos].strip())
        chunk = chunk[pos:].strip()
    if chunk:
        chunks.append(chunk)
    return chunks


def tokenize_for_gtts(text: str, *, max_len: int = 95) -> List[str]:
    """Split normalized speech into bounded chunks without losing sentences."""
    """
    Split normalized speech text for gTTS without adding punctuation to the text.

    gTTS has a practical chunk limit near 100 characters. This tokenizer keeps
    units like "2.5 million light years" together by choosing earlier natural
    connector boundaries when a sentence is too long.
    """
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", str(text).strip())
    chunks: List[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        chunks.extend(_split_long_gtts_chunk(part, max_len=max_len))
    return chunks


def _capitalize_sentence_starts(text: str) -> str:
    def repl(match: re.Match) -> str:
        return match.group(1) + match.group(2).upper()

    if not text:
        return text
    text = text[0].upper() + text[1:]
    return re.sub(r"([?.!]\s+)([a-z])", repl, text)


def normalize_for_tts(
    text: str,
    *,
    pronunciation_overrides: Optional[Mapping[str, str]] = None,
) -> str:
    """
    Convert readable response text into a TTS-friendly form.

    This belongs at the speech boundary, not inside command handlers. Text
    channels should keep normal punctuation, while spoken output gets explicit
    pause and pronunciation hints for gTTS.
    """
    if not text:
        return ""

    spoken = str(text).strip()
    spoken = spoken.replace("\n", " ")
    spoken = re.sub(r"\s+", " ", spoken)
    spoken = _strip_markdown_for_tts(spoken)
    spoken = _expand_dotted_acronyms(spoken)

    for pattern, replacement in _UNIT_REPLACEMENTS:
        spoken = pattern.sub(replacement, spoken)

    spoken = _apply_word_replacements(spoken, _BUILTIN_PRONUNCIATIONS)
    if pronunciation_overrides:
        spoken = _apply_word_replacements(spoken, pronunciation_overrides)
    spoken = _smooth_known_phrases(spoken)

    # Commas are inconsistent in gTTS. Preserve numeric commas and strip the
    # rest; gTTS chunking is handled separately by tokenize_for_gtts().
    spoken = _prepare_commas_for_tts(spoken)
    spoken = re.sub(r"\s*[;:]\s*", " ", spoken)
    spoken = re.sub(r"(?<=\d)\s*[—–-]\s*(?=\d)", " to ", spoken)
    spoken = spoken.replace("—", " ").replace("–", " ")
    spoken = re.sub(r"\s+-\s+", " ", spoken)

    # Avoid stacked punctuation after substitutions.
    spoken = re.sub(r"\s+", " ", spoken).strip()
    spoken = re.sub(r"\.{2,}", ".", spoken)
    spoken = re.sub(r"\.\s*\.", ".", spoken)
    spoken = re.sub(r"\s+([?.!])", r"\1", spoken)
    spoken = re.sub(r"([?.!]){2,}", r"\1", spoken)
    spoken = _capitalize_sentence_starts(spoken)
    if spoken and not re.search(r"[?.!]$", spoken):
        spoken += "."
    return spoken
