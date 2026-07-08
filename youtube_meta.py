"""youtube_meta.py — video metadata (durations) via the Data API v3.

Provides video durations so build_digest can drop Shorts/short clips
(min_duration_sec) on channels whose monologue can't be isolated by title alone
(e.g. Kimmel, the science channels). Batched (up to 50 ids/call, 1 quota unit
each) with an in-process cache. Auth via youtube_oauth.

Defensive: a missing token or API error yields None for a duration, and
_passes_filters treats an unknown duration as "don't drop", so filtering simply
degrades to title-only when metadata is unavailable.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

import requests

import youtube_oauth

log = logging.getLogger("youtube_meta")

_API = "https://www.googleapis.com/youtube/v3"
_TIMEOUT = 25
_cache: Dict[str, Optional[int]] = {}

_ISO_DUR = re.compile(
    r"P(?:(?P<d>\d+)D)?T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?")


def _parse_iso8601(dur: str) -> Optional[int]:
    """ISO-8601 duration (e.g. PT12M34S) -> total seconds."""
    if not dur:
        return None
    m = _ISO_DUR.fullmatch(dur)
    if not m:
        return None
    d, h, mi, s = (int(m.group(k) or 0) for k in ("d", "h", "m", "s"))
    return ((d * 24 + h) * 60 + mi) * 60 + s


def prefetch(video_ids: List[str]) -> None:
    """Warm the cache for many ids at once (batches of 50)."""
    todo = [v for v in dict.fromkeys(video_ids) if v and v not in _cache]
    tok = youtube_oauth.get_access_token() if todo else None
    if todo and not tok:
        log.warning("youtube_meta: not authed; durations unavailable")
        return
    for i in range(0, len(todo), 50):
        batch = todo[i:i + 50]
        try:
            r = requests.get(f"{_API}/videos",
                             params={"part": "contentDetails", "id": ",".join(batch)},
                             headers={"Authorization": "Bearer " + tok},
                             timeout=_TIMEOUT)
            j = r.json()
            if r.status_code >= 400:
                log.warning("youtube_meta: videos.list %s: %s", r.status_code,
                            j.get("error", {}).get("message", j))
                continue
            got = set()
            for it in j.get("items", []):
                secs = _parse_iso8601(it.get("contentDetails", {}).get("duration", ""))
                _cache[it["id"]] = secs
                got.add(it["id"])
            # ids with no item back (deleted/private) -> cache None so we stop asking
            for vid in batch:
                _cache.setdefault(vid, None)
        except Exception as e:
            log.warning("youtube_meta: prefetch error: %s", e)


def get_duration(video_id: str) -> Optional[int]:
    """Duration in seconds, or None if unknown. Cached; fetches on miss."""
    if video_id in _cache:
        return _cache[video_id]
    prefetch([video_id])
    return _cache.get(video_id)
