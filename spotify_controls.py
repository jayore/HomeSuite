"""Spotify Web API resolution and Home Assistant/Sonos playback helpers.

Spotify Web API functions manage tokens, playlist metadata, search, likes, and
library mutations. Playback handlers then use configured Home Assistant or
Sonos integrations to start the resolved URI in a real room. Typed searches
prefer their requested media kind; fuzzy assistance is constrained to returned
Spotify candidates.

The module contains several dispatch entry points because account operations,
Spotcast, and Sonos media browsing have different configuration requirements
and failure behavior.
"""

from __future__ import annotations

import time
import re
import logging
from typing import Optional, Dict, Tuple
import requests

from runtime_mode import allow_real_effects

try:
    from private_config import (
        SPOTIFY_CLIENT_ID,
        SPOTIFY_CLIENT_SECRET,
        SPOTIFY_REFRESH_TOKEN,
    )
except Exception:
    SPOTIFY_CLIENT_ID = ""
    SPOTIFY_CLIENT_SECRET = ""
    SPOTIFY_REFRESH_TOKEN = ""

from integration_config import friendly_missing, missing, spotify_web_configured

from spotify_webapi_search import SpotifyClient, find_user_playlist_uri_by_name
_SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SPOTIFY_API_BASE = "https://api.spotify.com/v1"

# Cache: playlists name -> id
_playlists_cache: Dict[str, str] = {}
_playlists_cache_ts: float = 0.0
_PLAYLISTS_CACHE_TTL_SECONDS = 30 * 60  # 30 minutes

# Access token cache
_access_token: Optional[str] = None
_access_token_expires_ts: float = 0.0

_session = requests.Session()


def _norm_name(s: str) -> str:
    # Plex-like normalization: punctuation-tolerant + collapse whitespace
    s = (s or "").strip().lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _squash_name(s: str) -> str:
    # remove spaces after normalization ("vapor thump" == "vaporthump")
    return re.sub(r"\s+", "", _norm_name(s))

def _find_user_playlist_id(name: str) -> Optional[str]:
    """Find a playlist ID from *your* /me/playlists cache.
    - exact normalized match first
    - then squash match (spaces removed)
    """
    q = _norm_name(name)
    if not q:
        return None

    refresh_playlists_cache(force=False)

    pid = _playlists_cache.get(q)
    if pid:
        logging.info("MATCH_DEBUG Spotify: user playlist exact match q=%r pid=%s", q, pid)
        return pid

    want_sq = _squash_name(q)
    if want_sq:
        for nm, pid2 in (_playlists_cache or {}).items():
            if _squash_name(nm) == want_sq:
                logging.info(
                    "MATCH_DEBUG Spotify: user playlist SQUASH match want=%r hit=%r pid=%s",
                    q, nm, pid2,
                )
                return pid2

    logging.info("MATCH_DEBUG Spotify: user playlist miss q=%r", q)
    return None

def _get_access_token() -> Optional[str]:
    global _access_token, _access_token_expires_ts

    now = time.time()
    if _access_token and now < (_access_token_expires_ts - 30):
        return _access_token

    try:
        r = _session.post(
            _SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": SPOTIFY_REFRESH_TOKEN,
                "client_id": SPOTIFY_CLIENT_ID,
                "client_secret": SPOTIFY_CLIENT_SECRET,
            },
            timeout=10,
        )
        if r.status_code != 200:
            logging.error(f"Spotify token refresh failed: {r.status_code} {r.text[:200]}")
            return None
        data = r.json() or {}
        token = data.get("access_token")
        expires_in = float(data.get("expires_in") or 3600)
        if not token:
            logging.error("Spotify token refresh returned no access_token")
            return None
        _access_token = token
        _access_token_expires_ts = now + expires_in
        return _access_token
    except Exception as e:
        logging.error(f"Spotify token refresh exception: {e}")
        return None


def _api_headers() -> Optional[dict]:
    tok = _get_access_token()
    if not tok:
        return None
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def refresh_playlists_cache(force: bool = False) -> None:
    global _playlists_cache, _playlists_cache_ts

    now = time.time()
    if not force and _playlists_cache and (now - _playlists_cache_ts) < _PLAYLISTS_CACHE_TTL_SECONDS:
        return

    headers = _api_headers()
    if not headers:
        _playlists_cache = {}
        _playlists_cache_ts = now
        return

    playlists: Dict[str, str] = {}
    url = f"{_SPOTIFY_API_BASE}/me/playlists"
    params = {"limit": 50, "offset": 0}

    try:
        while True:
            r = _session.get(url, headers=headers, params=params, timeout=10)
            if r.status_code != 200:
                logging.error(f"Spotify playlists fetch failed: {r.status_code} {r.text[:200]}")
                break
            data = r.json() or {}
            items = data.get("items") or []
            for it in items:
                name = _norm_name(it.get("name") or "")
                pid = it.get("id")
                if name and pid:
                    playlists[name] = pid

            if not data.get("next"):
                break
            params["offset"] += params["limit"]

        _playlists_cache = playlists
        _playlists_cache_ts = now
        logging.info(f"Spotify playlists cached: {len(playlists)}")
    except Exception as e:
        logging.error(f"Spotify playlists fetch exception: {e}")
        _playlists_cache = {}
        _playlists_cache_ts = now



def get_artist_top_track_uri(artist_id: str, *, market: str = "US") -> Optional[str]:
    """
    Return a spotify:track:<id> URI for the artist's top track (market-specific).
    """
    artist_id = (artist_id or "").strip()
    if not artist_id:
        return None

    headers = _api_headers()
    if not headers:
        return None

    try:
        r = _session.get(
            f"{_SPOTIFY_API_BASE}/artists/{artist_id}/top-tracks",
            headers=headers,
            params={"market": market},
            timeout=10,
        )
        if r.status_code != 200:
            logging.error(f"Spotify top-tracks failed: {r.status_code} {r.text[:200]}")
            return None
        data = r.json() or {}
        tracks = data.get("tracks") or []
        for t in tracks:
            if isinstance(t, dict) and t.get("uri", "").startswith("spotify:track:"):
                return t["uri"]
        return None
    except Exception as e:
        logging.error(f"Spotify top-tracks exception: {e}")
        return None

def get_current_track() -> Optional[Tuple[str, str, str]]:
    """
    Returns (track_uri, title, artist) for the currently playing track.
    """
    headers = _api_headers()
    if not headers:
        return None

    try:
        r = _session.get(f"{_SPOTIFY_API_BASE}/me/player/currently-playing", headers=headers, timeout=10)
        if r.status_code == 204:
            return None  # nothing playing
        if r.status_code != 200:
            logging.error(f"Spotify currently-playing failed: {r.status_code} {r.text[:200]}")
            return None

        data = r.json() or {}
        item = data.get("item") or {}
        track_uri = item.get("uri") or ""
        title = (item.get("name") or "").strip()
        artists = item.get("artists") or []
        artist = (artists[0].get("name") if artists and isinstance(artists[0], dict) else "") or ""
        artist = str(artist).strip()

        if not track_uri:
            return None
        return (track_uri, title, artist)
    except Exception as e:
        logging.error(f"Spotify currently-playing exception: {e}")
        return None


def like_current_track() -> bool:
    cur = get_current_track()
    if not cur:
        return False
    track_uri, _, _ = cur
    # track_uri like spotify:track:<id>
    m = re.match(r"spotify:track:([A-Za-z0-9]+)$", track_uri.strip())
    if not m:
        return False
    track_id = m.group(1)

    headers = _api_headers()
    if not headers:
        return False

    try:
        r = _session.put(f"{_SPOTIFY_API_BASE}/me/tracks", headers=headers, params={"ids": track_id}, timeout=10)
        return r.status_code in (200, 201, 204)
    except Exception as e:
        logging.error(f"Spotify like track exception: {e}")
        return False


def add_current_track_to_playlist(playlist_name: str) -> bool:
    playlist_name = _norm_name(playlist_name)
    if not playlist_name:
        return False

    refresh_playlists_cache(force=False)
    pid = _playlists_cache.get(playlist_name)
    if not pid:
        return False

    cur = get_current_track()
    if not cur:
        return False
    track_uri, _, _ = cur

    headers = _api_headers()
    if not headers:
        return False

    try:
        r = _session.post(
            f"{_SPOTIFY_API_BASE}/playlists/{pid}/tracks",
            headers=headers,
            json={"uris": [track_uri]},
            timeout=10,
        )
        return r.status_code in (200, 201)
    except Exception as e:
        logging.error(f"Spotify add-to-playlist exception: {e}")
        return False



def resolve_play_request(query: str) -> Optional[Tuple[str, str]]:
    """
    Resolve a user query to a Spotify URI and media_content_type.
    Priority:
      1) your playlists (exact name match)
      2) artist search
      3) album search
      4) track search
    Returns: (spotify_uri, content_type) or None
    """
    q = _norm_name(query)
    if not q:
        return None

    # Prefer your playlists by name (exact OR squash)
    pid = _find_user_playlist_id(query)
    if pid:
        return (f"spotify:playlist:{pid}", "playlist")
    uri = _find_user_playlist_uri_web(query)
    if uri:
        return (uri, "playlist")
    uri = _find_user_playlist_uri_web(query)
    if uri:
        return (uri, "playlist")
    headers = _api_headers()
    if not headers:
        return None

    def search_one(stype: str) -> Optional[Tuple[str, str]]:
        try:
            r = _session.get(
                f"{_SPOTIFY_API_BASE}/search",
                headers=headers,
                params={"q": query, "type": stype, "limit": 1},
                timeout=10,
            )
            if r.status_code != 200:
                return None
            data = r.json() or {}
            items = (((data.get(stype + "s") or {}).get("items")) or [])
            if not items:
                return None
            top = items[0] if isinstance(items[0], dict) else None
            if not top:
                return None
            uri = top.get("uri")
            if not uri:
                return None
            return (uri, stype)
        except Exception:
            return None

    # Artist -> Album -> Track
    for stype in ("album", "track", "artist"):
        hit = search_one(stype)
        if hit:
            uri, k = hit
            if k == "artist" and isinstance(uri, str) and uri.startswith("spotify:artist:"):
                artist_id = uri.split(":")[-1]
                top = get_artist_top_track_uri(artist_id, market="US")
                if top:
                    return (top, "track")
            return hit

    return None

def resolve_typed_play_request(
    query: str,
    *,
    kind: Optional[str] = None,   # "playlist"|"artist"|"album"|"track"
    artist: Optional[str] = None, # optional disambiguation (primarily for track/album)
) -> Optional[Tuple[str, str]]:
    """
    Resolve a user query to a Spotify URI with optional type restriction and/or artist constraint.

    Returns: (spotify_uri, kind_returned) or None

    Notes:
    - When kind is "playlist", we still prefer your own playlists by exact name if possible.
    - For track/album + artist, we use Spotify search field filters where possible.
    """
    q = _norm_name(query)
    if not q:
        return None

    if kind is not None:
        kind = (kind or "").strip().lower()
        if kind not in ("playlist", "artist", "album", "track"):
            kind = None

    artist_q = _norm_name(artist) if artist else ""
    # Spotify user-playlist scan (full library):
    # - If kind=='playlist', do NOT fall back to global Spotify search.
    # - Prefer exact cached match (fast) then full paged scan (robust).
    if kind in (None, 'playlist'):
        refresh_playlists_cache(force=False)
        pid = _playlists_cache.get(q)
        if pid:
            return (f"spotify:playlist:{pid}", "playlist")
        try:
            sp = _get_web_spotify_client()
            if sp:
                uri = _find_user_playlist_uri_by_name(spotify=sp, name=query)
                if isinstance(uri, str) and uri.startswith("spotify:playlist:"):
                    return (uri, "playlist")
        except Exception as e:
            logging.error("Spotify user-playlist scan failed: %s", e)
        if kind == 'playlist':
            return None
    headers = _api_headers()
    if not headers:
        return None

    # 1) If they asked for a playlist, prefer *your* playlists first (exact OR squash) BEFORE global search
    if kind in (None, "playlist"):
        pid = _find_user_playlist_id(query)
        if pid:
            return (f"spotify:playlist:{pid}", "playlist")


    # Build Spotify query with optional filters
    # Spotify supports field filters like: track:... artist:...
    def build_spotify_query() -> str:
        if kind in ("track", "album") and artist_q:
            return f'{kind}:"{query}" artist:"{artist}"'
        if kind == "artist" and query:
            return f'artist:"{query}"'
        if kind == "playlist" and query:
            return f'playlist:"{query}"'
        if kind is None:
            # generic: let Spotify decide relevance
            if artist_q:
                return f'{query} artist:"{artist}"'
            return query
        return query

    spotify_q = build_spotify_query()

    def search_one(stype: str) -> Optional[Tuple[str, str]]:
        try:
            r = _session.get(
                f"{_SPOTIFY_API_BASE}/search",
                headers=headers,
                params={"q": spotify_q, "type": stype, "limit": 1},
                timeout=10,
            )
            if r.status_code != 200:
                return None
            data = r.json() or {}
            items = (((data.get(stype + "s") or {}).get("items")) or [])
            if not items:
                return None
            top = items[0] if isinstance(items[0], dict) else None
            if not top:
                return None
            uri = top.get("uri")
            if not uri:
                return None
            return (uri, stype)
        except Exception:
            return None

    # If kind specified, search only that kind
    if kind:
        hit = search_one(kind)
        if not hit:
            return None
        uri, k = hit
        # If HA can't play spotify:artist: directly, convert to top track when possible
        if k == "artist" and isinstance(uri, str) and uri.startswith("spotify:artist:"):
            artist_id = uri.split(":")[-1]
            top = get_artist_top_track_uri(artist_id, market="US")
            if top:
                return (top, "track")
        return hit

    # Otherwise keep existing general preference order (artist -> album -> track)
    for stype in ("artist", "album", "track"):
        hit = search_one(stype)
        if hit:
            return hit

    return None


def handle_spotify_controls(tl: str, *, maybe_say) -> Optional[str]:
    """
    Returns:
      - None: not a spotify control phrase
      - "" or spoken string: handled
    """
    t = (tl or "").strip().lower()

    # Now playing / who is this by
    if re.search(r"\b(what's playing|what is playing|who's playing|who is playing|who is this by|who's this by)\b", t):
        cur = get_current_track()
        if not cur:
            return maybe_say("Nothing is playing.")
        _, title, artist = cur
        if title and artist:
            return f"It's {title} by {artist}."
        if title:
            return f"It's {title}."
        if artist:
            return f"It's by {artist}."
        return maybe_say("Something is playing, but I can't read the track details.")

    # Like / save
    if re.search(r"\b(like this|save this|favorite this|add this to library|save this song|like this song)\b", t):
        if not spotify_web_configured():
            return friendly_missing("Spotify", missing("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN"))
        if not allow_real_effects():
            return maybe_say("Test preview: would save the current track.")
        ok = like_current_track()
        return maybe_say("Saved.") if ok else maybe_say("I couldn't save that with Spotify.")

    # Add to playlist
    m = re.search(r"\badd (?:this|this song|current song|current track) to (?:playlist )?(.+)\b", t)
    if m:
        if not spotify_web_configured():
            return friendly_missing("Spotify", missing("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN"))
        name = m.group(1).strip()
        if not allow_real_effects():
            return maybe_say(f"Test preview: would add the current track to {name}.")
        ok = add_current_track_to_playlist(name)
        return maybe_say(f"Added to {name}.") if ok else maybe_say(f"I couldn't add that to {name} with Spotify.")

    return None


# =========================
# SPOTCAST (Home Assistant)
# =========================

from typing import Any


def _is_pinned_playlist_name(name: str) -> bool:
    try:
        from app_config import PINNED_SPOTIFY_PLAYLISTS
    except Exception:
        PINNED_SPOTIFY_PLAYLISTS = {}

    n = (name or "").strip().lower()
    n = re.sub(r"[^\w\s]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return bool(n and (PINNED_SPOTIFY_PLAYLISTS or {}).get(n))


def _clean_spotcast(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _extract_spotcast_device_name(
    tl: str,
    device_aliases: Dict[str, str],
    default_device_name: str,
) -> str:
    """
    Parse: '... on <device>' / '... in <device>' and map via aliases.
    Returns a Spotcast device_name string.
    """
    m = re.search(r"\b(?:on|in)\s+(.+?)\s*$", tl)
    if not m:
        return default_device_name

    raw = _clean_spotcast(m.group(1)).lower()
    raw = re.sub(r"\b(the|a|an)\b", "", raw).strip()
    raw = re.sub(r"\s+", " ", raw)

    if raw in device_aliases:
        return device_aliases[raw]

    for k, v in device_aliases.items():
        if k and k in raw:
            return v

    return m.group(1).strip()


def handle_spotcast_play_controls(
    tl: str,
    *,
    call_ha_service,
    maybe_say,
    default_device_name: str,
    device_aliases: Dict[str, str],
) -> Optional[str]:
    """
    Spotcast playback starter (DEVICE command).

    Returns:
      - None: not a Spotcast play request OR failed to execute (caller should error-tone)
      - "" or spoken string: handled
    """
    t = (tl or "").strip().lower()
    if not t:
        return None

    if not re.match(r"^(play|put on|listen to|start)\b", t):
        return None

    query_part = re.sub(r"\b(?:on|in)\s+.+?\s*$", "", t).strip()
    query_part = re.sub(r"^(play|put on|listen to|start)\s+", "", query_part).strip()

    if not query_part:
        return None

    device_name = _extract_spotcast_device_name(t, device_aliases, default_device_name)
    if not str(device_name or "").strip():
        return None

    data: Dict[str, Any] = {"device_name": device_name}

    if re.search(r"\bspotify:(track|album|playlist|artist|show|episode):", query_part):
        data["uri"] = query_part
    else:
        m_playlist = re.match(r"^(?:my\s+)?playlist\s+(.+)$", query_part)
        m_album = re.match(r"^album\s+(.+)$", query_part)
        m_track = re.match(r"^(?:song|track)\s+(.+)$", query_part)
        m_artist = re.match(r"^artist\s+(.+)$", query_part)

        if m_playlist:
            data["playlist_name"] = _clean_spotcast(m_playlist.group(1))
        elif m_album:
            data["album_name"] = _clean_spotcast(m_album.group(1))
        elif m_track:
            data["track_name"] = _clean_spotcast(m_track.group(1))
        elif m_artist:
            data["artist_name"] = _clean_spotcast(m_artist.group(1))
        else:
            data["playlist_name"] = _clean_spotcast(query_part)

    if "playlist_name" in data and _is_pinned_playlist_name(data["playlist_name"]):
        logging.info(f"Spotcast: skipping pinned playlist {data['playlist_name']!r}")
        return None

    ok = call_ha_service("spotcast/start", data)
    if not ok:
        return None

    return maybe_say(f"Playing on {device_name}.")


# =========================
# SONOS + SPOTIFY (via HA browse_media/play_media)
# =========================

from typing import Any, List


def _norm_title(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _extract_play_query(tl: str) -> Optional[str]:
    """
    Returns the thing to play from phrases like:
      - "play <x>"
      - "put on <x>"
      - "listen to <x>"
      - "start <x>"
    Strips optional trailing "on|in <device>".
    """
    t = (tl or "").strip().lower()
    if not t:
        return None
    if not re.match(r"^(play|put on|listen to|start)\b", t):
        return None

    t = re.sub(r"\b(?:on|in)\s+.+?\s*$", "", t).strip()
    t = re.sub(r"^(play|put on|listen to|start)\s+", "", t).strip()
    return t or None


def _ha_browse_media(
    *,
    ha_url: str,
    ha_token: str,
    entity_id: str,
    media_content_type: Optional[str] = None,
    media_content_id: Optional[str] = None,
    timeout_s: int = 25,
) -> Optional[dict]:
    """
    Calls HA media_player.browse_media (response service).
    Returns the browse node dict for this entity_id or None.
    """
    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
    payload: dict = {"entity_id": entity_id}
    if media_content_type is not None:
        payload["media_content_type"] = media_content_type
    if media_content_id is not None:
        payload["media_content_id"] = media_content_id

    try:
        r = requests.post(
            f"{ha_url}/api/services/media_player/browse_media?return_response",
            headers=headers,
            json=payload,
            timeout=timeout_s,
        )
        if r.status_code != 200:
            logging.error(f"HA browse_media failed: {r.status_code} {r.text[:300]}")
            return None
        data = r.json()
        # Response service returns: {"service_response": {"media_player.entity": {...}}}
        sr = (data or {}).get("service_response") if isinstance(data, dict) else None
        if not isinstance(sr, dict):
            return None
        ent = sr.get(entity_id)
        if not isinstance(ent, dict):
            return None
        return ent
    except Exception as e:
        logging.error(f"HA browse_media exception: {e}")
        return None


def _pick_best_child(children: List[dict], query: str) -> Optional[dict]:
    qn = _norm_title(query)
    if not qn:
        return None

    # Exact normalized title match first
    for c in children:
        title = c.get("title") if isinstance(c, dict) else None
        if title and _norm_title(title) == qn:
            return c

    # Prefix match
    for c in children:
        title = c.get("title") if isinstance(c, dict) else None
        nt = _norm_title(title) if title else ""
        if nt and (nt.startswith(qn) or qn.startswith(nt)):
            return c

    # Contains match
    for c in children:
        title = c.get("title") if isinstance(c, dict) else None
        nt = _norm_title(title) if title else ""
        if nt and (qn in nt or nt in qn):
            return c

    return None


def _sonos_spotify_find_and_play(
    *,
    tl: str,
    ha_url: str,
    ha_token: str,
    sonos_entity_id: str,
    call_ha_service,
) -> bool:
    """
    Resolve "play X" into a Sonos-browseable Spotify item and play it.
    Strategy:
      1) Playlists
      2) Artists
      3) Albums
      4) Tracks
    """
    query = _extract_play_query(tl)
    if not query:
        return False

    # First browse root to locate the Spotify library node (user id can differ)
    root = _ha_browse_media(ha_url=ha_url, ha_token=ha_token, entity_id=sonos_entity_id)
    if not root:
        return False

    children = root.get("children") or []
    spotify_node = None
    for c in children:
        if not isinstance(c, dict):
            continue
        mct = c.get("media_content_type")
        if isinstance(mct, str) and mct.startswith("spotify://library"):
            spotify_node = c
            break
    if not spotify_node:
        return False

    # Browse inside Spotify library
    lib = _ha_browse_media(
        ha_url=ha_url,
        ha_token=ha_token,
        entity_id=sonos_entity_id,
        media_content_type=spotify_node.get("media_content_type"),
        media_content_id=spotify_node.get("media_content_id"),
    )
    if not lib:
        return False

    lib_children = lib.get("children") or []
    # Map title -> node for the categories we care about
    by_title = { _norm_title(c.get("title","")): c for c in lib_children if isinstance(c, dict) }

    # Prefer playlists first (best UX)
    category_order = [
        ("playlists", "spotify://current_user_playlists"),
        ("artists", "spotify://current_user_followed_artists"),
        ("albums", "spotify://current_user_saved_albums"),
        ("tracks", "spotify://current_user_saved_tracks"),
    ]

    for title_key, expected_prefix in category_order:
        cat = by_title.get(title_key)
        if not cat:
            continue
        mct = cat.get("media_content_type")
        mcid = cat.get("media_content_id")
        if not (isinstance(mct, str) and isinstance(mcid, str)):
            continue
        if not mct.startswith(expected_prefix):
            # still try; integrations can vary slightly
            pass

        node = _ha_browse_media(
            ha_url=ha_url,
            ha_token=ha_token,
            entity_id=sonos_entity_id,
            media_content_type=mct,
            media_content_id=mcid,
        )
        if not node:
            continue

        items = node.get("children") or []
        if not isinstance(items, list) or not items:
            continue

        best = _pick_best_child(items, query)
        if not best:
            continue

        best_type = best.get("media_content_type")
        best_id = best.get("media_content_id")
        can_play = bool(best.get("can_play"))

        # If the leaf isn't directly playable, try to expand once and pick first playable child
        if (not can_play) and isinstance(best_type, str) and isinstance(best_id, str):
            expanded = _ha_browse_media(
                ha_url=ha_url,
                ha_token=ha_token,
                entity_id=sonos_entity_id,
                media_content_type=best_type,
                media_content_id=best_id,
            )
            kids = (expanded or {}).get("children") or []
            playable = next((k for k in kids if isinstance(k, dict) and k.get("can_play")), None)
            if playable:
                best = playable
                best_type = best.get("media_content_type")
                best_id = best.get("media_content_id")

        if isinstance(best_type, str) and isinstance(best_id, str):
            ok = call_ha_service(
                "media_player/play_media",
                {
                    "entity_id": sonos_entity_id,
                    "media_content_type": best_type,
                    "media_content_id": best_id,
                },
            )
            return bool(ok)

    return False


def handle_sonos_spotify_play_controls(
    tl: str,
    *,
    ha_url: str,
    ha_token: str,
    sonos_entity_id: str,
    call_ha_service,
    maybe_say,
) -> Optional[str]:
    """
    DEVICE handler: "play X" routed to Sonos Spotify browse/play.

    Returns:
      - None: not a play request OR failed to resolve/play (caller should error-tone)
      - "" or spoken string: handled
    """
    q = _extract_play_query(tl)
    if not q:
        return None

    ok = _sonos_spotify_find_and_play(
        tl=tl,
        ha_url=ha_url,
        ha_token=ha_token,
        sonos_entity_id=sonos_entity_id,
        call_ha_service=call_ha_service,
    )
    if not ok:
        return None

    return maybe_say("Playing.")


# Web Spotify client (paged playlist scan / robust matching)
_web_spotify_client = None
def _get_web_spotify_client():
    global _web_spotify_client
    if _web_spotify_client is not None:
        return _web_spotify_client
    if not spotify_web_configured():
        logging.info("SpotifyClient unavailable: Spotify Web API is not configured")
        return None
    try:
        _web_spotify_client = SpotifyClient(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            refresh_token=SPOTIFY_REFRESH_TOKEN,
        )
        return _web_spotify_client
    except Exception as e:
        logging.error("SpotifyClient init failed: %s", e)
        _web_spotify_client = None
        return None
