from __future__ import annotations

import re
import time
from typing import Any, Optional

MUSIC_KINDS = {"track", "album", "artist"}
VIDEO_KINDS = {"movie", "show"}


def clean_media_title(value: Any) -> str:
    out = str(value or "").strip()
    out = out.strip(" \t\r\n\"'")
    out = re.sub(r"\s+", " ", out)
    return out


def _confidence(value: Any, default: float = 1.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def normalize_music_intent(
    item: Any,
    *,
    source: str = "ai",
    min_confidence: float = 0.55,
    include_type: bool = True,
    include_ts: bool = False,
) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    title = clean_media_title(item.get("title"))
    if not title:
        return None
    kind = str(item.get("kind") or "track").strip().lower()
    if kind not in MUSIC_KINDS:
        kind = "track"
    artist = clean_media_title(item.get("artist")) or None
    confidence = _confidence(item.get("confidence", item.get("confident", 1.0)))
    if confidence < min_confidence:
        return None
    if kind == "artist":
        artist = None

    out: dict[str, Any] = {
        "kind": kind,
        "title": title,
        "artist": artist,
        "confidence": confidence,
        "source": source,
    }
    if include_type:
        out["type"] = "music"
    if include_ts:
        out["ts"] = time.time()
    return out


def normalize_video_intent(
    item: Any,
    *,
    source: str = "ai",
    min_confidence: float = 0.55,
    include_type: bool = True,
    include_ts: bool = False,
) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    title = clean_media_title(item.get("title"))
    if not title:
        return None
    kind = str(item.get("kind") or "movie").strip().lower()
    if kind not in VIDEO_KINDS:
        kind = "movie"
    confidence = _confidence(item.get("confidence", item.get("confident", 1.0)))
    if confidence < min_confidence:
        return None

    out: dict[str, Any] = {
        "kind": kind,
        "title": title,
        "confidence": confidence,
        "source": source,
    }
    if include_type:
        out["type"] = "video"
    if include_ts:
        out["ts"] = time.time()
    return out


def public_music_result(intent: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": intent["title"],
        "artist": intent.get("artist"),
        "kind": intent["kind"],
    }


def public_video_result(intent: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": intent["title"],
        "kind": intent["kind"],
    }


def music_intent_to_command(intent: dict[str, Any], suffix: str = "") -> str:
    kind = str(intent.get("kind") or "track").lower()
    title = clean_media_title(intent.get("title"))
    artist = clean_media_title(intent.get("artist"))
    if kind == "artist":
        cmd = f"play artist {title}"
    elif kind == "album":
        cmd = f"play album {title}"
        if artist:
            cmd += f" by {artist}"
    else:
        cmd = f"play track {title}"
        if artist:
            cmd += f" by {artist}"
    return f"{cmd}{suffix or ''}".strip()


def video_intent_to_command(intent: dict[str, Any]) -> str:
    return f"watch {clean_media_title(intent.get('title'))}".strip()


def loggable_media_intent(intent: dict[str, Any]) -> dict[str, Any]:
    return {
        k: intent.get(k)
        for k in ("type", "kind", "title", "artist", "confidence", "source")
        if intent.get(k) is not None
    }
