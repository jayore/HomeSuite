import re
import logging
from typing import Optional, Dict, Any, List
from spotify_webapi_search import SpotifyClient, pick_best_spotify_item, find_user_playlist_uri_by_name
from spotify_controls import get_artist_top_track_uri


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", _norm(s))

def _get_pinned_spotify_playlist_uri(query: str) -> Optional[str]:
    """Return a pinned Spotify playlist URI for this query, if configured in app_config.py."""
    try:
        from app_config import PINNED_SPOTIFY_PLAYLISTS
    except Exception:
        PINNED_SPOTIFY_PLAYLISTS = {}

    qn = _norm(query)
    if not qn:
        return None

    uri = (PINNED_SPOTIFY_PLAYLISTS or {}).get(qn)
    if isinstance(uri, str) and uri.startswith("spotify:"):
        return uri
    return None



def _extract_play_query(tl: str) -> Optional[str]:
    t = (tl or "").strip().lower()
    if not t:
        return None

    # basic play verbs
    m = re.match(r"^(play|put on|listen to|start)\s+(.+)$", t)
    if not m:
        return None

    q = m.group(2).strip()

    # strip trailing "on/in <device>" (we only support the configured Sonos entity)
    q = re.sub(r"\b(?:on|in)\s+[a-z0-9 _-]+\s*$", "", q).strip()

    # common filler
    q = re.sub(r"^(the|my)\s+", "", q).strip()
    return q or None




# =========================
# PLAY INTENT PARSING
# =========================

def _parse_play_intent(query: str) -> Dict[str, Optional[str]]:
    """
    Parse a play query into structured intent.

    Returns dict:
      {
        "kind": "track" | "album" | "playlist" | "artist" | None,
        "title": "<thing to play>",
        "artist": "<artist name>" | None
      }

    Examples:
      "imagine by the beatles" ->
        {"kind": "track", "title": "imagine", "artist": "the beatles"}

      "the album narrow stairs by death cab for cutie" ->
        {"kind": "album", "title": "narrow stairs", "artist": "death cab for cutie"}

      "songs by daft punk" ->
        {"kind": "artist", "title": "daft punk", "artist": None}
    """
    q = (query or "").strip()
    if not q:
        return {"kind": None, "title": "", "artist": None}

    # Normalize leading articles
    q = re.sub(r"^(the|my)\s+", "", q, flags=re.I).strip()
    ql = q.lower()

    # 1) "songs by <artist>" / "music by <artist>" => artist intent
    m_songs_by = re.match(r"^(songs|music)\s+by\s+(.+)$", ql, flags=re.I)
    if m_songs_by:
        return {"kind": "artist", "title": m_songs_by.group(2).strip(), "artist": None}

    # 2) Optional explicit type prefix:
    #    "album <x>", "the album <x>", "track <x>", "song <x>", "playlist <x>", "artist <x>"
    kind = None
    rest = q

    # allow "the album ..." as well
    m_type = re.match(r"^(?:the\s+)?(album|playlist|track|song|artist|band)\s+(.+)$", ql, flags=re.I)
    if m_type:
        kind = m_type.group(1).strip().lower()
        rest = q[len(m_type.group(0)) - len(m_type.group(2)):].strip()

    # normalize synonym
    if kind == "song":
        kind = "track"
    if kind == "band":
        kind = "artist"

    title = rest.strip() if rest else q
    artist = None

    # 3) "<title> by <artist>" (works for track or album; if kind unspecified => track)
    #    e.g. "imagine by the beatles"
    m_by = re.match(r"^(.+?)\s+by\s+(.+)$", title, flags=re.I)
    if m_by:
        title = m_by.group(1).strip().strip('"').strip("'")
        artist = m_by.group(2).strip().strip('"').strip("'")
        if kind is None:
            kind = "track"

    return {"kind": kind, "title": title.strip(), "artist": artist.strip() if artist else None}
def _pick_best(children: List[Dict[str, Any]], query: str) -> Optional[Dict[str, Any]]:
    qn = _norm(query)
    if not qn:
        return None
    qs = _squash(query)

    # exact (normalized)
    for c in children:
        title = c.get("title") if isinstance(c, dict) else None
        if title:
            tn = _norm(title)
            if tn == qn:
                return c
            # also allow space-insensitive exact match (e.g. 'vapor thump' vs 'vaporthump')
            tn2 = tn.replace(" ", "")
            qn2 = qn.replace(" ", "")
            if tn2 and qn2 and tn2 == qn2:
                return c

    # exact (squashed) - handles 'vaporthump' vs 'vapor thump'
    if qs:
        for c in children:
            title = c.get("title") if isinstance(c, dict) else None
            if title and _squash(title) == qs:
                return c

    # contains (prefer shorter distance); try squashed first, then normalized
    best = None
    best_len = 10**9

    for c in children:
        if not isinstance(c, dict):
            continue
        title = c.get("title") or ""
        tn = _norm(title)
        if not tn:
            continue

        # squashed contains
        ts = _squash(title)
        if qs and ts and (qs in ts or ts in qs):
            score = abs(len(ts) - len(qs))
            if score < best_len:
                best = c
                best_len = score
            continue

        # normalized contains
        tn2 = tn.replace(" ", "")
        qn2 = qn.replace(" ", "")
        if (qn in tn) or (tn in qn) or (tn2 and qn2 and ((qn2 in tn2) or (tn2 in qn2))):
            score = abs(len(tn) - len(qn))
            if score < best_len:
                best = c
                best_len = score

    return best


def _browse(
    *,
    ha_session,
    ha_url: str,
    headers: dict,
    entity_id: str,
    media_content_type: Optional[str] = None,
    media_content_id: Optional[str] = None,
    timeout_s: int = 25,
) -> Optional[Dict[str, Any]]:
    payload: Dict[str, Any] = {"entity_id": entity_id}
    if media_content_type is not None:
        payload["media_content_type"] = media_content_type
    if media_content_id is not None:
        payload["media_content_id"] = media_content_id

    try:
        r = ha_session.post(
            f"{ha_url}/api/services/media_player/browse_media?return_response",
            headers=headers,
            json=payload,
            timeout=timeout_s,
        )
        if r.status_code != 200:
            logging.error(f"browse_media failed {r.status_code}: {r.text[:200]}")
            return None

        data = r.json() or {}
        sr = data.get("service_response") or {}
        ent = sr.get(entity_id)
        if not isinstance(ent, dict):
            return None
        return ent
    except Exception as e:
        logging.error(f"browse_media exception: {e}")
        return None



_SPOTIFY_CLIENT = None

def _get_spotify_client():
    global _SPOTIFY_CLIENT
    if _SPOTIFY_CLIENT is not None:
        return _SPOTIFY_CLIENT
    try:
        from private_config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN
        _SPOTIFY_CLIENT = SpotifyClient(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            refresh_token=SPOTIFY_REFRESH_TOKEN,
        )
        return _SPOTIFY_CLIENT
    except Exception:
        return None


def _try_play_spotify_uri_on_sonos(*, uri: str, sonos_entity_id: str, call_ha_service) -> bool:
    """Try a few common media_content_type values for spotify: URIs."""
    if not (isinstance(uri, str) and uri.startswith("spotify:")):
        return False
    for mct_try in ("playlist", "music", "spotify"):
        ok = call_ha_service(
            "media_player/play_media",
            {
                "entity_id": sonos_entity_id,
                "media_content_type": mct_try,
                "media_content_id": uri,
            },
        )
        if ok:
            return True
    return False


def handle_sonos_spotify_browse_play(
    tl: str,
    *,
    ha_session,
    ha_url: str,
    ha_headers: dict,
    sonos_entity_id: str,
    call_ha_service,
    maybe_say,
) -> Optional[str]:
    """
    DEVICE handler: resolve "play X" using Sonos Spotify browse tree and play it.

    Returns:
        None -> not handled OR failed to resolve/play (caller should error tone)
        ""/str -> handled (silent or spoken)
    """
    query = _extract_play_query(tl)
    if not query:
        return None


    # Parse typed requests: "album X", "track X", "playlist X", and "X by Y"
    intent = _parse_play_intent(query)
    kind = (intent.get("kind") or None)
    title = (intent.get("title") or query).strip()
    artist = (intent.get("artist") or None)


    # Fast-path: pinned playlists (non-secret config)
    pinned_uri = _get_pinned_spotify_playlist_uri(intent.get("title"))
    if pinned_uri:
        logging.info(f"Sonos Spotify: pinned playlist match for {query!r} -> {pinned_uri}")
        ok = call_ha_service(
            "media_player/play_media",
            {
                "entity_id": sonos_entity_id,
                "media_content_type": "music",
                "media_content_id": pinned_uri,
            },
        )
        if ok:
            return maybe_say("Playing.")

    # Fast-path: pinned Discover Weekly (avoids Sonos browse tree + Spotify search ambiguity)
    qn = _norm(query)
    if qn in ("discover weekly", "my discover weekly"):
        try:
            from private_config import SPOTIFY_DISCOVER_WEEKLY_URI
        except Exception:
            SPOTIFY_DISCOVER_WEEKLY_URI = None

        if isinstance(SPOTIFY_DISCOVER_WEEKLY_URI, str) and SPOTIFY_DISCOVER_WEEKLY_URI.startswith("spotify:"):
            logging.info(f"Sonos Spotify: fast-path Discover Weekly -> {SPOTIFY_DISCOVER_WEEKLY_URI}")
            # Use the same format that works in the direct HA test
            ok = call_ha_service(
                "media_player/play_media",
                {
                    "entity_id": sonos_entity_id,
                    "media_content_type": "music",
                    "media_content_id": SPOTIFY_DISCOVER_WEEKLY_URI,
                },
            )
            if ok:
                return maybe_say("Playing.")
            logging.error("Sonos Spotify: Discover Weekly fast-path play_media failed")
        else:
            logging.error("Sonos Spotify: SPOTIFY_DISCOVER_WEEKLY_URI missing/invalid in private_config.py")
        return None

    # ------------------------------------------------------------------
    # Explicit typed Spotify requests must respect the requested type.
    #
    # Previously, even "play artist Death Cab for Cutie" could be stolen by
    # the playlist-browse path if a playlist/radio item had the same name.
    # For explicit artist/album/track requests, skip playlist browsing and
    # resolve directly via Spotify Web API.
    # ------------------------------------------------------------------
    if kind in ("artist", "album", "track"):
        sp = _get_spotify_client()
        if not sp:
            logging.error("Sonos Spotify typed request: Spotify client unavailable kind=%r title=%r", kind, title)
            return None

        if kind == "artist":
            search_q = f'artist:"{title}"'
            search_types = ["artist"]
            prefer_types = ["artist"]

        elif kind == "album":
            if artist:
                search_q = f'album:"{title}" artist:"{artist}"'
            else:
                search_q = f'album:"{title}"'
            search_types = ["album"]
            prefer_types = ["album"]

        else:  # track
            if artist:
                search_q = f'track:"{title}" artist:"{artist}"'
            else:
                search_q = f'track:"{title}"'
            search_types = ["track"]
            prefer_types = ["track"]

        logging.info(
            "Sonos Spotify typed request kind=%r title=%r artist=%r search_q=%r",
            kind,
            title,
            artist,
            search_q,
        )

        sj = sp.search(q=search_q, types=search_types, limit=8)
        if not sj:
            logging.error("Sonos Spotify typed request search miss kind=%r title=%r", kind, title)
            return None

        picked = pick_best_spotify_item(
            query=title,
            search_json=sj,
            prefer_types=prefer_types,
            artist=artist,
        )
        if not picked:
            logging.error("Sonos Spotify typed request no confident pick kind=%r title=%r", kind, title)
            return None

        picked_kind, uri, picked_title = picked

        # HA/Sonos cannot play spotify:artist: URIs directly in this setup.
        # Convert artist -> top track, preserving existing project behavior.
        if isinstance(uri, str) and uri.startswith("spotify:artist:"):
            artist_id = uri.split(":")[-1]
            top = get_artist_top_track_uri(artist_id, market="US")
            if isinstance(top, str) and top.startswith("spotify:track:"):
                uri = top
                picked_kind = "track"
                logging.info("CLAIM: sonos_spotify_typed_artist_top_track artist=%r", picked_title)
            else:
                logging.error("Sonos Spotify typed artist: could not resolve top track for uri=%s", uri)
                return None

        ok = call_ha_service(
            "media_player/play_media",
            {
                "entity_id": sonos_entity_id,
                "media_content_type": "music",
                "media_content_id": uri,
            },
        )
        if ok:
            logging.info(
                "CLAIM: sonos_spotify_typed_play kind=%r title=%r uri=%r",
                picked_kind,
                picked_title,
                uri,
            )
            return maybe_say("Playing.")
        return None

    # 1) root browse (to find Spotify library node)
    root = _browse(
        ha_session=ha_session,
        ha_url=ha_url,
        headers=ha_headers,
        entity_id=sonos_entity_id,
    )
    if not root:
        return None

    spotify_node = None
    for c in (root.get("children") or []):
        if not isinstance(c, dict):
            continue
        mct = c.get("media_content_type")
        if isinstance(mct, str) and mct.startswith("spotify://library"):
            spotify_node = c
            break

    if not spotify_node:
        return None

    # 2) browse Spotify library
    lib = _browse(
        ha_session=ha_session,
        ha_url=ha_url,
        headers=ha_headers,
        entity_id=sonos_entity_id,
        media_content_type=spotify_node.get("media_content_type"),
        media_content_id=spotify_node.get("media_content_id"),
    )
    if not lib:
        return None

    # 3) Prefer the direct "current_user_playlists" endpoint (matches what you verified works)
    playlists_root_id = "spotify://3dab6da2d0091fe48a6059026e4e2a4c/current_user_playlists"
    node = _browse(
        ha_session=ha_session,
        ha_url=ha_url,
        headers=ha_headers,
        entity_id=sonos_entity_id,
        media_content_type="spotify://current_user_playlists",
        media_content_id=playlists_root_id,
    )
    if not node:
        # Fallback: browse via library -> Playlists category
        playlists_cat = None
        for c in (lib.get("children") or []):
            if not isinstance(c, dict):
                continue
            if _norm(c.get("title") or "") == "playlists":
                playlists_cat = c
                break
        if not playlists_cat:
            return None
        node = _browse(
            ha_session=ha_session,
            ha_url=ha_url,
            headers=ha_headers,
            entity_id=sonos_entity_id,
            media_content_type=playlists_cat.get("media_content_type"),
            media_content_id=playlists_cat.get("media_content_id"),
        )
        if not node:
            return None

    items = node.get("children") or []
    if not isinstance(items, list) or not items:
        return None

    best = _pick_best(items, title)
    if not best:
        # Fallback: Spotify Web API search -> play on Sonos via HA
        sp = _get_spotify_client()
        if sp:
            # Special-case: personalized Spotify playlists by name (e.g., Discover Weekly)
            qn = _norm(query)
            if qn in ("discover weekly", "my discover weekly"):
                pinned_uri = _get_pinned_spotify_playlist_uri("discover weekly")
                if pinned_uri and _try_play_spotify_uri_on_sonos(
                    uri=pinned_uri,
                    sonos_entity_id=sonos_entity_id,
                    call_ha_service=call_ha_service,
                ):
                    return maybe_say("Playing.")
                uri = find_user_playlist_uri_by_name(spotify=sp, name='Discover Weekly')
                if uri:
                    # If Spotify search returned an artist URI, HA/Sonos cannot play it directly in this environment.
                    # Convert artist -> top track and play that instead.
                    if isinstance(uri, str) and uri.startswith("spotify:artist:"):
                        artist_id = uri.split(":")[-1]
                        top = get_artist_top_track_uri(artist_id, market="US")
                        if isinstance(top, str) and top.startswith("spotify:track:"):
                            uri = top
                            logging.info("CLAIM: sonos_spotify_artist_top_track")
                        else:
                            logging.error("Sonos Spotify: could not resolve top track for artist uri=%s", uri)
                            return None

                    ok = call_ha_service(
                        'media_player/play_media',
                        {
                            'entity_id': sonos_entity_id,
                            'media_content_type': 'music',
                            'media_content_id': uri,
                        },
                    )
                    if ok:
                        return maybe_say('Playing.')

            # Prefer *user* playlists (paged /me/playlists) before global search.
            # This prevents "rando Spotify" matches when the user actually has a playlist with that name.
            try:
                user_pl_uri = find_user_playlist_uri_by_name(spotify=sp, name=title)
            except Exception as e:
                user_pl_uri = None
                logging.error("MATCH_DEBUG Sonos Spotify: user playlist lookup exception title=%r err=%s", title, e)

            if isinstance(user_pl_uri, str) and user_pl_uri.startswith("spotify:playlist:"):
                logging.info("MATCH_DEBUG Sonos Spotify: user playlist short-circuit title=%r uri=%s", title, user_pl_uri)
                ok = call_ha_service(
                    'media_player/play_media',
                    {
                        'entity_id': sonos_entity_id,
                        'media_content_type': 'music',
                        'media_content_id': user_pl_uri,
                    },
                )
                if ok:
                    return maybe_say('Playing.')
                return None

            # If they explicitly asked for a playlist and we didn't find it in *their* library,
            # do NOT fall back to global search (better to fail than play unrelated content).
            if kind == "playlist":
                logging.info("MATCH_DEBUG Sonos Spotify: user playlist miss (no global fallback) title=%r", title)
                return None

            # Build Spotify search query based on parsed intent (kind/title/artist)
            if kind in ("track", "album") and artist:
                search_q = f'{kind}:"{title}" artist:"{artist}"'
                # broaden types a bit for resilience
                search_types = [kind, "track", "album", "artist", "playlist"]
            elif kind == "artist":
                search_q = f'artist:"{title}"'
                search_types = ["artist", "track", "album", "playlist"]
            elif kind == "playlist":
                search_q = f'playlist:"{title}"'
                search_types = ["playlist", "artist", "album", "track"]
            else:
                search_q = title
                search_types = ["playlist", "artist", "album", "track"]

            sj = sp.search(q=search_q, types=search_types, limit=8)
            if sj:
                picked = pick_best_spotify_item(
                    query=title,
                    search_json=sj,
                    prefer_types=['playlist','artist','album','track'],
                    artist=artist,
                )
                if picked:
                    kind, uri, title = picked

                    # HA/Sonos cannot play spotify:artist: URIs directly (500 errors in this environment).
                    # Convert artist -> top track.
                    if isinstance(uri, str) and uri.startswith("spotify:artist:"):
                        artist_id = uri.split(":")[-1]
                        top = get_artist_top_track_uri(artist_id, market="US")
                        if isinstance(top, str) and top.startswith("spotify:track:"):
                            uri = top
                            logging.info("CLAIM: sonos_spotify_artist_top_track")
                        else:
                            logging.error("Sonos Spotify: could not resolve top track for artist uri=%s", uri)
                            return None

                    ok = call_ha_service(
                        'media_player/play_media',
                        {
                            'entity_id': sonos_entity_id,
                            'media_content_type': 'music',
                            'media_content_id': uri,
                        },
                    )
                    if ok:
                        return maybe_say('Playing.')
        return None

    mct = best.get("media_content_type")
    mcid = best.get("media_content_id")
    if not (isinstance(mct, str) and isinstance(mcid, str)):
        return None

    ok = call_ha_service(
        "media_player/play_media",
        {
            "entity_id": sonos_entity_id,
            "media_content_type": mct,
            "media_content_id": mcid,
        },
    )
    if not ok:
        return None

    return maybe_say("Playing.")
