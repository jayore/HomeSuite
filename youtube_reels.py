"""youtube_reels.py — real playlists for the reels/roundups (Stage A.2/B glue).

Each "scope" maps to one reusable playlist named "PiPhone · <Label>":
  * scope None  -> the global daily reel (in_digest channels) -> "PiPhone · Daily Reel"
  * scope "<group>" -> a roundup (group-tagged channels) -> "PiPhone · <Group>"

Playback is *static*: 'play my X roundup' plays the existing playlist by id. The
scheduler keeps it fresh via refresh_tick():
  * once per day (first tick in the window) it WIPES each playlist, then rebuilds;
  * every later tick DIFFS the live digest against the playlist and only inserts
    newly-posted episodes (cheap — full rebuilds every 5 min would blow quota).

All Data API work is defensive (youtube_playlist/youtube_meta return None/False on
failure); this module logs and returns partial results, never raises.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

import youtube_channels
import youtube_feed
import youtube_meta
import youtube_oauth
import youtube_playlist

log = logging.getLogger("youtube_reels")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE_DIR = os.path.join(_BASE_DIR, "state")
_STATE_PATH = os.path.join(_STATE_DIR, "youtube_reels.json")

PLAYLIST_PREFIX = "PiPhone · "

# Pretty labels for known group keys; unknown groups are title-cased.
_GROUP_LABELS = {"latenight": "Late Night", "science": "Science"}

# Freshness window per scope. Late-night/daily refresh nightly (24h); channels
# that post less often (science) get a wider window so the playlist isn't empty.
_DEFAULT_WINDOW_H = 24
_SCOPE_WINDOW_H = {"science": 168}


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def scope_label(scope: Optional[str]) -> str:
    if not scope:
        return "Daily Reel"
    return _GROUP_LABELS.get(scope, scope.title())


def playlist_title(scope: Optional[str]) -> str:
    return PLAYLIST_PREFIX + scope_label(scope)


def _window_hours(scope: Optional[str]) -> int:
    return _SCOPE_WINDOW_H.get(scope or "", _DEFAULT_WINDOW_H)


def scopes() -> List[Optional[str]]:
    """All scopes we maintain: the daily reel plus every registry group."""
    return [None] + youtube_channels.list_groups()


# ---------------------------------------------------------------------------
# State (last wipe date)
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    try:
        with open(_STATE_PATH, "r") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("youtube_reels: state load failed: %s", e)
        return {}


def _save_state(d: dict) -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, _STATE_PATH)
    except Exception as e:
        log.warning("youtube_reels: state save failed: %s", e)


def _scope_key(scope: Optional[str]) -> str:
    return scope or "_daily"


def note_playlist_ids(scope: Optional[str], playlist_id: str, video_ids: List[str]) -> None:
    """Cache a scope's playlist id + ordered video ids locally so playback can skip
    the ~0.9s Data API list call (the scheduler refresh and first-run build call
    this). Consistent with static playback: 'as fresh as the last refresh'."""
    if not playlist_id:
        return
    state = _load_state()
    pls = state.get("playlists")
    if not isinstance(pls, dict):
        pls = {}
    pls[_scope_key(scope)] = {
        "playlist_id": playlist_id,
        "video_ids": list(video_ids or []),
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    state["playlists"] = pls
    _save_state(state)


def cached_play_target(scope: Optional[str]):
    """(playlist_id, first_video_id) from the local cache — zero API calls — or
    (None, None) if not cached yet."""
    entry = (_load_state().get("playlists") or {}).get(_scope_key(scope)) or {}
    pid = entry.get("playlist_id")
    vids = entry.get("video_ids") or []
    if pid and vids:
        return (pid, vids[0])
    return (None, None)


# ---------------------------------------------------------------------------
# Build / sync
# ---------------------------------------------------------------------------

def _build_ids(scope: Optional[str]) -> List[str]:
    gd = youtube_meta.get_duration if youtube_oauth.is_authed() else None
    vids = youtube_feed.build_digest(group=scope, within_hours=_window_hours(scope),
                                     include_watched=True, get_duration=gd)
    return [v["video_id"] for v in vids]


def sync_playlist(scope: Optional[str], *, wipe: bool = False) -> Optional[dict]:
    """Ensure the scope's playlist exists and is up to date.
    wipe=True: clear then rebuild from the digest (daily reset).
    wipe=False: diff — insert only digest videos not already present.
    Returns {playlist_id, added, present, want} or None if unresolved/unauthed."""
    if not youtube_oauth.is_authed():
        log.warning("youtube_reels: not authed; cannot sync playlists")
        return None
    title = playlist_title(scope)
    pid = youtube_playlist.find_or_create(title, description=(
        f"PiPhone {scope_label(scope)} — latest episodes, auto-refreshed."))
    if not pid:
        return None
    if wipe:
        youtube_playlist.clear(pid)
    want = _build_ids(scope)
    have = [] if wipe else youtube_playlist.list_video_ids(pid)
    have_set = set(have)
    new = [v for v in want if v not in have_set]
    added = youtube_playlist.add(pid, new) if new else 0
    # Cache the resulting playlist contents for zero-API-call playback.
    current_ids = want if wipe else (have + new)
    note_playlist_ids(scope, pid, current_ids)
    log.info("youtube_reels: sync %r wipe=%s want=%d added=%d", title, wipe, len(want), added)
    return {"playlist_id": pid, "added": added, "present": len(have), "want": len(want)}


def refresh_all(*, wipe: bool = False) -> Dict[str, Optional[dict]]:
    return {scope_label(s): sync_playlist(s, wipe=wipe) for s in scopes()}


def refresh_tick() -> Dict[str, Optional[dict]]:
    """One scheduler tick. The first tick each day wipes+rebuilds every playlist;
    later ticks diff-add newly-posted episodes. Idempotent and cheap when nothing
    is new."""
    today = date.today().isoformat()
    state = _load_state()
    wipe = state.get("last_wipe_date") != today
    res = refresh_all(wipe=wipe)
    if wipe:
        state["last_wipe_date"] = today
        _save_state(state)
    return res


# ---------------------------------------------------------------------------
# Playback (static): resolve a playable playlist id for a scope
# ---------------------------------------------------------------------------

def get_playlist_id(scope: Optional[str], *, build_if_empty: bool = False) -> Optional[str]:
    """The scope's playlist id for playback. Trusts the name->id cache (validate
    =False, no API call) so playback is fast — callers fetch the items separately
    and handle the empty/first-run case. With build_if_empty=True it also builds
    a never-populated playlist (an extra API call); the playback path passes False
    and does the single items fetch itself."""
    if not youtube_oauth.is_authed():
        return None
    pid = youtube_playlist.find_or_create(playlist_title(scope), validate=False)
    if not pid:
        return None
    if build_if_empty and not youtube_playlist.list_video_ids(pid):
        sync_playlist(scope, wipe=True)
    return pid
