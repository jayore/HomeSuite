"""youtube_feed.py — latest uploads per channel (free RSS) + daily reel builder.

Uses each channel's public RSS feed
(https://www.youtube.com/feeds/videos.xml?channel_id=UC...) — no API key, no
quota — parsed with stdlib xml.etree. Builds the "daily reel": the newest upload
from each in_digest channel, ordered newest-first, de-duplicated against a small
watch-state file so the reel doesn't repeat day to day.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

import youtube_channels

log = logging.getLogger("youtube_feed")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE_DIR = os.path.join(_BASE_DIR, "state")
_WATCH_PATH = os.path.join(_STATE_DIR, "youtube_watch.json")

_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_MEDIA = "{http://search.yahoo.com/mrss/}"

_lock = threading.Lock()
_MAX_PLAYED = 1000  # cap watch-state growth


# ---------------------------------------------------------------------------
# Feed fetch / parse
# ---------------------------------------------------------------------------

def _parse_published(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def latest_videos(channel_id: str, limit: int = 1) -> List[dict]:
    """Return the newest uploads for a channel (newest first)."""
    cid = (channel_id or "").strip()
    if not cid:
        return []
    try:
        import requests
        resp = requests.get(_FEED_URL.format(cid=cid), timeout=10,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200 or not resp.text:
            return []
        root = ET.fromstring(resp.text)
    except Exception as e:
        log.warning("youtube_feed: fetch/parse failed for %s: %s", cid, e)
        return []

    feed_title = root.findtext(f"{_ATOM}title") or ""
    out: List[dict] = []
    for entry in root.findall(f"{_ATOM}entry"):
        vid = entry.findtext(f"{_YT}videoId")
        if not vid:
            continue
        group = entry.find(f"{_MEDIA}group")
        description = ""
        if group is not None:
            description = (group.findtext(f"{_MEDIA}description") or "").strip()
        out.append({
            "video_id": vid,
            "title": (entry.findtext(f"{_ATOM}title") or "").strip(),
            "description": description,
            "published": (entry.findtext(f"{_ATOM}published") or "").strip(),
            "channel_id": cid,
            "channel_title": (youtube_channels.channel_title(cid) or feed_title or "").strip(),
        })
    out.sort(key=lambda v: v.get("published") or "", reverse=True)
    return out[: max(1, int(limit))]


# ---------------------------------------------------------------------------
# Watch-state (atomic, defensive)
# ---------------------------------------------------------------------------

def _load_watch() -> dict:
    try:
        with open(_WATCH_PATH, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("youtube_feed: watch load failed: %s", e)
        return {}


def _save_watch(data: dict) -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        tmp = _WATCH_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _WATCH_PATH)
    except Exception as e:
        log.warning("youtube_feed: watch save failed: %s", e)


def mark_played(video_ids: List[str], *, digest: Optional[List[str]] = None) -> None:
    with _lock:
        data = _load_watch()
        played = list(data.get("played") or [])
        seen = set(played)
        for vid in video_ids:
            if vid and vid not in seen:
                played.append(vid)
                seen.add(vid)
        data["played"] = played[-_MAX_PLAYED:]
        if digest is not None:
            data["last_digest"] = list(digest)
            data["last_built"] = datetime.now(timezone.utc).isoformat()
        _save_watch(data)


def last_digest() -> List[str]:
    with _lock:
        return list(_load_watch().get("last_digest") or [])


# ---------------------------------------------------------------------------
# Daily reel builder
# ---------------------------------------------------------------------------

def _matches_any(text: str, patterns: List[str]) -> bool:
    t = (text or "").lower()
    return any(p in t for p in patterns)


def _passes_filters(v: dict, cfg: dict, *, now: datetime, within_hours: Optional[int],
                    get_duration) -> bool:
    title = v.get("title", "")
    desc = v.get("description", "")

    inc = cfg.get("include_title") or []
    if inc and not _matches_any(title, inc):
        return False
    if _matches_any(title, cfg.get("exclude_title") or []):
        return False
    if _matches_any(desc, cfg.get("exclude_description") or []):
        return False

    if within_hours is not None:
        pub = _parse_published(v.get("published", ""))
        if pub is None:
            return False
        if (now - pub).total_seconds() / 3600.0 > within_hours:
            return False

    min_dur = cfg.get("min_duration_sec")
    if min_dur and get_duration is not None:
        try:
            dur = get_duration(v["video_id"])
        except Exception:
            dur = None
        # If we can resolve a duration, enforce it; if we can't, don't drop it.
        if dur is not None and dur < min_dur:
            return False

    return True


def build_digest(*, within_hours: Optional[int] = 24, include_watched: bool = False,
                 get_duration=None, scan_depth: int = 15,
                 group: Optional[str] = None) -> List[dict]:
    """Build a reel: the newest *qualifying* upload(s) per source channel.

    The source channels are the in_digest set by default, or — when `group` is
    given — the channels tagged with that group (a themed "roundup"), independent
    of in_digest. Scans up to `scan_depth` recent entries per channel (RSS gives
    ~15) and applies that channel's filters — include/exclude title, exclude
    description, 24h window, and (when `get_duration` is supplied) min-duration to
    drop short clips — then keeps the newest qualifying videos up to the channel's
    `max_per_channel`.
    """
    with _lock:
        played = set(_load_watch().get("played") or [])

    if group:
        source = youtube_channels.channels_in_group(group)
    else:
        source = youtube_channels.digest_channels()

    now = datetime.now(timezone.utc)
    videos: List[dict] = []
    for cid in source:
        cfg = youtube_channels.get_channel(cid) or {}
        max_pc = int(cfg.get("max_per_channel") or 1)
        kept = 0
        for v in latest_videos(cid, limit=scan_depth):  # newest first
            if not include_watched and v["video_id"] in played:
                continue
            if not _passes_filters(v, cfg, now=now, within_hours=within_hours,
                                   get_duration=get_duration):
                continue
            videos.append(v)
            kept += 1
            if kept >= max_pc:
                break

    videos.sort(key=lambda v: v.get("published") or "", reverse=True)
    return videos


def newest_qualifying(channel_id: str, *, within_hours: Optional[int] = None,
                      get_duration=None, scan_depth: int = 15) -> Optional[dict]:
    """Newest upload from a channel that passes that channel's filters
    (include/exclude title + description, optional duration). Used by
    'watch <channel>' so it skips the Shorts/clips the filters exclude and lands
    on the proper segment (e.g. Seth's "A Closer Look"). Falls back to the
    absolute newest upload if nothing qualifies, so a watch always plays something.
    """
    cfg = youtube_channels.get_channel(channel_id) or {}
    vids = latest_videos(channel_id, limit=scan_depth)  # newest first
    now = datetime.now(timezone.utc)
    for v in vids:
        if _passes_filters(v, cfg, now=now, within_hours=within_hours,
                           get_duration=get_duration):
            return v
    return vids[0] if vids else None
