"""youtube_playlist.py — manage real YouTube playlists via the Data API v3.

Backs the "roundup" reels with persistent, reusable playlists on the user's
account (stable URL, resumable, externally playable, castable by listId). The
nightly job clears+rebuilds them; "watch tonight's monologues" plays one by id.

Auth comes from youtube_oauth.get_access_token() (youtube scope). All calls are
defensive — failures log and return None/False, never raise into the command path.

Quota note: insert/delete cost 50 units each; a clear+rebuild of ~10 videos is
~1k units against the 10k/day default — fine for a nightly refresh.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional

import requests

import youtube_oauth

log = logging.getLogger("youtube_playlist")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE_DIR = os.path.join(_BASE_DIR, "state")
_MAP_PATH = os.path.join(_STATE_DIR, "youtube_playlists.json")  # name -> playlist_id cache

_API = "https://www.googleapis.com/youtube/v3"
_TIMEOUT = 25


# ---------------------------------------------------------------------------
# Low-level request helper
# ---------------------------------------------------------------------------

def _req(method: str, path: str, *, params=None, body=None) -> Optional[dict]:
    tok = youtube_oauth.get_access_token()
    if not tok:
        log.warning("youtube_playlist: not authed (run tools/youtube_oauth.py)")
        return None
    try:
        r = requests.request(method, f"{_API}/{path}",
                             params=params, json=body,
                             headers={"Authorization": "Bearer " + tok},
                             timeout=_TIMEOUT)
        if r.status_code == 204:
            return {}  # successful DELETE
        j = r.json()
        if r.status_code >= 400:
            log.warning("youtube_playlist: %s %s -> %s: %s", method, path,
                        r.status_code, j.get("error", {}).get("message", j))
            return None
        return j
    except Exception as e:
        log.warning("youtube_playlist: %s %s error: %s", method, path, e)
        return None


# ---------------------------------------------------------------------------
# name -> id cache (avoids a playlists.list search every run)
# ---------------------------------------------------------------------------

def _load_map() -> Dict[str, str]:
    try:
        with open(_MAP_PATH, "r") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("youtube_playlist: map load failed: %s", e)
        return {}


def _save_map(d: Dict[str, str]) -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        tmp = _MAP_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, _MAP_PATH)
    except Exception as e:
        log.warning("youtube_playlist: map save failed: %s", e)


# ---------------------------------------------------------------------------
# Playlist operations
# ---------------------------------------------------------------------------

def _find_by_title(title: str) -> Optional[str]:
    """Search the account's own playlists for an exact-title match."""
    page = None
    while True:
        params = {"part": "snippet", "mine": "true", "maxResults": 50}
        if page:
            params["pageToken"] = page
        j = _req("GET", "playlists", params=params)
        if not j:
            return None
        for it in j.get("items", []):
            if it.get("snippet", {}).get("title") == title:
                return it["id"]
        page = j.get("nextPageToken")
        if not page:
            return None


def search_by_title(query: str) -> Optional[tuple]:
    """Best-match playlist on the account by title (case-insensitive). Exact match
    wins; otherwise the shortest title that contains the query. Returns
    (playlist_id, title) or None. Used by 'watch my <name> playlist'."""
    q = (query or "").strip().lower()
    if not q:
        return None
    best = None  # (title_len, id, title)
    page = None
    while True:
        params = {"part": "snippet", "mine": "true", "maxResults": 50}
        if page:
            params["pageToken"] = page
        j = _req("GET", "playlists", params=params)
        if not j:
            break
        for it in j.get("items", []):
            title = it.get("snippet", {}).get("title", "")
            tl = title.lower()
            if tl == q:
                return (it["id"], title)
            if q in tl:
                cand = (len(tl), it["id"], title)
                if best is None or cand < best:
                    best = cand
        page = j.get("nextPageToken")
        if not page:
            break
    return (best[1], best[2]) if best else None


def find_or_create(title: str, *, description: str = "",
                   privacy: str = "unlisted", validate: bool = True) -> Optional[str]:
    """Return the playlist id for `title`, creating it if needed. Cached by title
    in state/youtube_playlists.json. validate=True confirms the cached id still
    exists (an API call); validate=False trusts the cache (no call) — used on the
    latency-sensitive playback path, where a stale id just means playback fails
    and the next refresh re-resolves it."""
    cache = _load_map()
    pid = cache.get(title)
    if pid and not validate:
        return pid
    if pid:
        # validate it still exists
        j = _req("GET", "playlists", params={"part": "id", "id": pid})
        if j and j.get("items"):
            return pid
        log.info("youtube_playlist: cached id for %r stale, re-resolving", title)

    pid = _find_by_title(title)
    if not pid:
        j = _req("POST", "playlists", params={"part": "snippet,status"}, body={
            "snippet": {"title": title, "description": description},
            "status": {"privacyStatus": privacy},
        })
        if not j or not j.get("id"):
            return None
        pid = j["id"]
        log.info("youtube_playlist: created %r -> %s", title, pid)

    cache[title] = pid
    _save_map(cache)
    return pid


def list_item_ids(playlist_id: str) -> List[str]:
    """playlistItem ids (not video ids) — needed to delete items."""
    ids: List[str] = []
    page = None
    while True:
        params = {"part": "id", "playlistId": playlist_id, "maxResults": 50}
        if page:
            params["pageToken"] = page
        j = _req("GET", "playlistItems", params=params)
        if not j:
            break
        ids.extend(it["id"] for it in j.get("items", []))
        page = j.get("nextPageToken")
        if not page:
            break
    return ids


def list_video_ids(playlist_id: str) -> List[str]:
    """Video ids currently in the playlist, in order — used to diff against a
    freshly-built digest so the 5-min refresh only inserts what's new (cheap)."""
    ids: List[str] = []
    page = None
    while True:
        params = {"part": "contentDetails", "playlistId": playlist_id, "maxResults": 50}
        if page:
            params["pageToken"] = page
        j = _req("GET", "playlistItems", params=params)
        if not j:
            break
        ids.extend(it["contentDetails"]["videoId"] for it in j.get("items", [])
                   if it.get("contentDetails", {}).get("videoId"))
        page = j.get("nextPageToken")
        if not page:
            break
    return ids


def clear(playlist_id: str) -> bool:
    """Remove all items from the playlist."""
    ok = True
    for item_id in list_item_ids(playlist_id):
        if _req("DELETE", "playlistItems", params={"id": item_id}) is None:
            ok = False
    return ok


def add(playlist_id: str, video_ids: List[str]) -> int:
    """Append videos (in order). Returns the count successfully added."""
    n = 0
    for vid in video_ids:
        j = _req("POST", "playlistItems", params={"part": "snippet"}, body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": vid},
            },
        })
        if j:
            n += 1
    return n


def rebuild(title: str, video_ids: List[str], *, description: str = "",
            privacy: str = "unlisted") -> Optional[str]:
    """find_or_create -> clear -> add. Returns the playlist id, or None on failure
    to resolve the playlist (partial add failures still return the id)."""
    pid = find_or_create(title, description=description, privacy=privacy)
    if not pid:
        return None
    clear(pid)
    add(pid, video_ids)
    return pid


def playlist_url(playlist_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={playlist_id}"
