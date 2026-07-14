"""Remember media references and resolve follow-up commands such as 'play it'.

The module stores bounded, context-bubble-scoped music and video referents
extracted from earlier interactions. Follow-up text is rewritten only when its
media domain and confidence agree with a recent referent; otherwise the original
text is returned for normal routing. Storage uses ``dialogue_state``; this
module retains media-specific extraction and command generation and never
performs playback itself.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional
from dialogue_state import forget_referent, remember_referent, resolve_referent
from media_intents import (
    loggable_media_intent,
    music_intent_to_command,
    normalize_music_intent,
    normalize_video_intent,
    video_intent_to_command,
)

_MEDIA_WORD_RE = re.compile(
    r"\b("
    r"song|track|album|artist|band|music|playlist|beatles|movie|film|show|series|episode|"
    r"cartoon|anime|sitcom|documentary|character|watch|listen|play|popular|famous|hit|single"
    r")\b",
    re.IGNORECASE,
)

_VIDEO_WORD_RE = re.compile(
    r"\b(movie|film|show|series|episode|cartoon|anime|sitcom|documentary|character)\b",
    re.IGNORECASE,
)

_MUSIC_WORD_RE = re.compile(
    r"\b(song|track|album|artist|band|music|playlist|listen)\b",
    re.IGNORECASE,
)

_PRONOUN_COMMAND_RE = re.compile(
    r"^\s*(?P<verb>play|put on|listen to|start|watch)\s+"
    r"(?P<target>it|that|this|that one|this one|the song|that song|this song|"
    r"the track|that track|this track|the album|that album|this album|"
    r"the artist|that artist|this artist|the band|that band|this band|"
    r"the movie|that movie|this movie|the film|that film|this film|"
    r"the show|that show|this show|the series|that series|this series)"
    r"(?P<suffix>\s+(?:in|on)\s+.+)?\s*[.?!]*\s*$",
    re.IGNORECASE,
)

_SYSTEM_PROMPT = (
    "Extract media referents from the user's question and the assistant's answer. "
    "These referents will be used by deterministic Plex and Spotify handlers later. "
    "Do not invent IDs, URLs, Spotify URIs, Plex IDs, or IMDb IDs. Store only names "
    "that can be searched in the user's own services.\n\n"
    "Return ONLY JSON with this shape:\n"
    "{\n"
    '  "music": {"kind": "track|album|artist", "title": string, "artist": string|null, "confidence": number} | null,\n'
    '  "video": {"kind": "movie|show", "title": string, "confidence": number} | null\n'
    "}\n\n"
    "Rules:\n"
    "- Only include a referent if the assistant clearly identified or recommended a specific media item.\n"
    "- For songs, prefer kind=track and include artist when known.\n"
    "- For albums, use kind=album and include artist when known.\n"
    "- For artists/bands, use kind=artist and title as the artist name.\n"
    "- For movies/shows, use kind=movie or kind=show.\n"
    "- Use confidence 0.0 to 1.0. Omit low-confidence guesses by returning null.\n"
)


def _pref_value(name: str, default=None):
    try:
        import app_config as prefs

        return getattr(prefs, name, default)
    except Exception:
        return default


def _enabled() -> bool:
    v = _pref_value("MEDIA_REFERENT_EXTRACTION_ENABLED", True)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def _ttl_seconds() -> float:
    try:
        return float(_pref_value("MEDIA_REFERENT_TTL_SECONDS", 5 * 60))
    except Exception:
        return 5 * 60


def _model(default_model: str) -> str:
    v = str(_pref_value("MEDIA_REFERENT_MODEL", "") or "").strip()
    return v or default_model


def _looks_media_related(user_text: str, assistant_text: str) -> bool:
    blob = f"{user_text or ''}\n{assistant_text or ''}"
    return bool(_MEDIA_WORD_RE.search(blob))


def _normalize_music(item: Any) -> Optional[dict[str, Any]]:
    return normalize_music_intent(item, source="chatgpt", include_type=True, include_ts=True)


def _normalize_video(item: Any) -> Optional[dict[str, Any]]:
    return normalize_video_intent(item, source="chatgpt", include_type=True, include_ts=True)


def _store_media_referent(media_type: str, ref: dict[str, Any]) -> bool:
    title = str(ref.get("title") or "").strip()
    if not title:
        return False
    artist = str(ref.get("artist") or "").strip()
    key = f"{ref.get('kind') or media_type}:{title}"
    if artist:
        key += f":{artist}"
    return bool(
        remember_referent(
            media_type,
            key,
            label=title,
            capabilities={"play_media"},
            data=ref,
            confidence=float(ref.get("confidence") or 0.0),
            ttl_seconds=_ttl_seconds(),
            source=str(ref.get("source") or "chatgpt"),
        )
    )


def _expects_music(user_text: str, assistant_text: str) -> bool:
    return bool(_MUSIC_WORD_RE.search(f"{user_text or ''}\n{assistant_text or ''}"))


def _expects_video(user_text: str, assistant_text: str) -> bool:
    return bool(_VIDEO_WORD_RE.search(f"{user_text or ''}\n{assistant_text or ''}"))


def _clear_referent(media_type: str, reason: str) -> None:
    entry = resolve_referent(kinds={media_type}, max_age_seconds=_ttl_seconds())
    old = (entry or {}).get("data") or {}
    if entry:
        logging.info(
            "MEDIA_REFERENT_CLEAR type=%s title=%r reason=%s",
            media_type,
            old.get("title"),
            reason,
        )
    forget_referent(media_type)


def _clean_candidate_title(value: str) -> str:
    title = re.sub(r"[*_`]+", "", value or "").strip()
    title = title.strip(" \t\r\n\"'“”‘’")
    title = re.sub(r"^(?:probably|likely|almost certainly|the answer is)\s+", "", title, flags=re.I)
    title = re.split(
        r"\s+(?:where|which|because|since|with|featuring|from|about|and)\s+",
        title,
        maxsplit=1,
        flags=re.I,
    )[0]
    title = re.split(r"\s+[-–—]\s+", title, maxsplit=1)[0]
    title = title.strip(" \t\r\n\"'“”‘’.,:;!?")
    return title


def remember_music(*, kind: str, title: str, artist: Optional[str] = None, confidence: float = 1.0) -> bool:
    ref = _normalize_music(
        {"kind": kind, "title": title, "artist": artist, "confidence": confidence}
    )
    if not ref:
        return False
    if not _store_media_referent("music", ref):
        return False
    logging.info("MEDIA_REFERENT_REMEMBER music=%r", loggable_media_intent(ref))
    return True


def remember_video(*, kind: str, title: str, confidence: float = 1.0) -> bool:
    ref = _normalize_video({"kind": kind, "title": title, "confidence": confidence})
    if not ref:
        return False
    if not _store_media_referent("video", ref):
        return False
    logging.info("MEDIA_REFERENT_REMEMBER video=%r", loggable_media_intent(ref))
    return True


def _heuristic_capture(user_text: str, assistant_text: str) -> set[str]:
    text = assistant_text or ""
    stored: set[str] = set()

    music_patterns = [
        r"(?:song|track)\s+(?:is|would be|was|:)\s+[\"']?([^\"'.\n]+?)[\"']?\s+by\s+([^,.\n]+)",
        r"[\"']([^\"']+)[\"']\s+by\s+([^,.\n]+)",
    ]
    for pat in music_patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        title = _clean_candidate_title(m.group(1)) if m else ""
        artist = _clean_candidate_title(m.group(2)) if m else ""
        if m and remember_music(kind="track", title=title, artist=artist, confidence=0.7):
            stored.add("music")
            break

    video_kind = (
        "show"
        if re.search(r"\b(show|series|episode|cartoon|anime|sitcom|character)\b", user_text or "", re.I)
        else "movie"
    )
    video_patterns = [
        r"(?:movie|film|show|series)\s+(?:is|would be|was|:)\s+[\"']?([^\"'.\n]+)[\"']?",
        r"(?:cartoon|anime|sitcom|documentary)\s+(?:is|would be|was|:)\s+[\"']?([^\"'.\n]+)[\"']?",
        r"(?:that|this|it|the show|the series|the cartoon|the movie|the film)\s+(?:is|was|would be|sounds like)\s+(?:probably\s+|likely\s+)?[\"']?([^\"'.\n]+)[\"']?",
        r"(?:sounds like|you're thinking of|you are thinking of|you mean)\s+(?:probably\s+|likely\s+)?[\"']?([^\"'.\n]+)[\"']?",
        r"(?:watch|recommend)\s+[\"']([^\"']+)[\"']",
    ]
    for pat in video_patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        title = _clean_candidate_title(m.group(1)) if m else ""
        if m and remember_video(kind=video_kind, title=title, confidence=0.65):
            stored.add("video")
            break

    return stored


def capture_from_chatgpt_turn(
    user_text: str,
    assistant_text: str,
    openai_client,
    *,
    default_model: str,
) -> None:
    if not _enabled() or not user_text or not assistant_text:
        return
    if not _looks_media_related(user_text, assistant_text):
        return

    expects_music = _expects_music(user_text, assistant_text)
    expects_video = _expects_video(user_text, assistant_text)
    stored_types = _heuristic_capture(user_text, assistant_text)

    if not openai_client:
        if expects_music and "music" not in stored_types:
            _clear_referent("music", "media_turn_without_music")
        if expects_video and "video" not in stored_types:
            _clear_referent("video", "media_turn_without_video")
        return

    try:
        response = openai_client.chat.completions.create(
            model=_model(default_model),
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"User question:\n{user_text.strip()}\n\n"
                        f"Assistant answer:\n{assistant_text.strip()}"
                    ),
                },
            ],
            temperature=0.0,
            max_completion_tokens=180,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        data = json.loads(raw)
        music = _normalize_music(data.get("music"))
        video = _normalize_video(data.get("video"))
        if music and _store_media_referent("music", music):
            stored_types.add("music")
            logging.info("MEDIA_REFERENT_EXTRACT music=%r", loggable_media_intent(music))
        if video and _store_media_referent("video", video):
            stored_types.add("video")
            logging.info("MEDIA_REFERENT_EXTRACT video=%r", loggable_media_intent(video))
    except Exception as e:
        logging.info("MEDIA_REFERENT_EXTRACT_SKIP err=%s", e)

    if expects_music and "music" not in stored_types:
        _clear_referent("music", "media_turn_without_music")
    if expects_video and "video" not in stored_types:
        _clear_referent("video", "media_turn_without_video")


def _latest(media_type: Optional[str] = None) -> Optional[dict[str, Any]]:
    kinds = {media_type} if media_type else {"music", "video"}
    entry = resolve_referent(
        kinds=kinds,
        max_age_seconds=max(1.0, _ttl_seconds()),
    )
    if not entry:
        return None
    ref = dict(entry.get("data") or {})
    ref["ts"] = float(entry.get("ts") or ref.get("ts") or time.time())
    return ref


def rewrite_media_pronoun_command(text: str) -> Optional[str]:
    m = _PRONOUN_COMMAND_RE.match(text or "")
    if not m:
        return None

    verb = m.group("verb").strip().lower()
    target = m.group("target").strip().lower()
    suffix = m.group("suffix") or ""

    wants_music = (
        verb in ("listen to",)
        or re.search(r"\b(song|track|album|artist|band)\b", target) is not None
    )
    wants_video = verb == "watch" or re.search(r"\b(movie|film|show|series)\b", target) is not None

    ref = None
    if wants_music and not wants_video:
        ref = _latest("music")
        rewritten = music_intent_to_command(ref, suffix) if ref else None
    elif wants_video and not wants_music:
        ref = _latest("video")
        rewritten = video_intent_to_command(ref) if ref else None
    else:
        ref = _latest(None)
        if not ref:
            rewritten = None
        elif ref.get("type") == "music":
            rewritten = music_intent_to_command(ref, suffix)
        else:
            rewritten = video_intent_to_command(ref)

    if rewritten:
        logging.info(
            "MEDIA_REFERENT_REWRITE %r -> %r ref=%r",
            text,
            rewritten,
            loggable_media_intent(ref or {}),
        )
    return rewritten


def snapshot() -> dict[str, Optional[dict[str, Any]]]:
    return {"music": _latest("music"), "video": _latest("video")}
