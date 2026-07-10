"""Route spoken play-by-name requests to a configured Sonos/Spotify target.

The handler extracts an optional room and typed media intent, checks pinned
playlists first where appropriate, then delegates catalog resolution to the
injected Spotify resolvers. It starts only returned/configured URIs and leaves
generic transport or volume language to their dedicated handlers.
"""

import re
import logging
from typing import Optional, Dict, Tuple, Callable

from spotify_controls import get_artist_top_track_uri
from spotify_resolver import _looks_fuzzy_music_query
from integration_config import friendly_missing, missing, spotify_web_configured


def _norm_pinned_key(query: str) -> str:
    # Match the existing normalization you used inline:
    # qn = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", query.lower())).strip()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (query or "").lower())).strip()


def _strip_quotes(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 2 and ((s[0] == s[-1]) and s[0] in ("'", '"')):
        return s[1:-1].strip()
    return s


def _parse_typed_play_intent(rest: str) -> dict:
    """
    Parse the portion after "play ..." into a structured intent.

    Returns dict with:
      - kind: Optional[str] in {"playlist","artist","album","track"} or None
      - query: str
      - artist: Optional[str]
    """
    r = (rest or "").strip()

    # Normalize common phrasing
    r = re.sub(r"^the\s+", "", r).strip()

    # songs by <artist> / music by <artist>
    m = re.match(r"^(?:songs|music)\s+by\s+(.+)$", r, flags=re.I)
    if m:
        return {"kind": "artist", "query": m.group(1).strip(), "artist": None}

    # explicit type prefix
    m = re.match(r"^(playlist|artist|band|album|song|track)\s+(.+)$", r, flags=re.I)
    if m:
        k = m.group(1).strip().lower()
        q = _strip_quotes(m.group(2).strip())
        if k == "song":
            k = "track"
        if k == "band":
            k = "artist"
        return {"kind": k, "query": q, "artist": None}

    # "<thing> by <artist>" (primarily for tracks; also usable for albums)
    m = re.match(r"^(.+?)\s+by\s+(.+)$", r, flags=re.I)
    if m:
        q = _strip_quotes(m.group(1).strip())
        a = _strip_quotes(m.group(2).strip())
        return {"kind": "track", "query": q, "artist": a}

    return {"kind": None, "query": _strip_quotes(r), "artist": None}


def _is_playlist_uri(uri: str) -> bool:
    if not isinstance(uri, str) or not uri:
        return False
    if uri.startswith("spotify:playlist:"):
        return True
    if uri.startswith("spotify://playlist"):
        return True
    # HA Sonos format you’re using elsewhere: spotify://<account>/<something>/spotify:playlist:<id>
    if "spotify:playlist:" in uri:
        return True
    return False


def handle_play_by_name_controls(
    *,
    tl: str,
    default_sonos_room: str,
    sonos_players: Dict[str, str],
    pinned_spotify_playlists: Dict[str, str],
    resolve_play_request: Callable[[str], Optional[Tuple[str, str]]],
    resolve_typed_play_request: Optional[Callable[..., Optional[Tuple[str, str]]]] = None,
    call_ha_service,
    maybe_say,
    resolve_description=None,
) -> Optional[str]:
    """
    Spotify play-by-name handler (playlist/artist/album/track) via Sonos.

    Returns:
      - None: not handled or failed (caller may error-tone / route further)
      - "" or str: handled (silent or spoken via maybe_say)
    """
    t = (tl or "").strip().lower()
    if not t:
        return None

    m_play = re.search(r"\b(?:shuffle\s+)?play\s+(.+)$", t)
    if not m_play:
        return None

    logging.info(f"DEBUG_PLAY_BY_NAME_ENTER tl={t!r}")

    # Guard: don't steal volume/other device phrases that accidentally contain "play"
    if "volume" in t:
        return None

    rest = m_play.group(1).strip()

    # Optional room: "... in kitchen"
    room = (default_sonos_room or "").strip().lower()
    raw_query = rest
    m_in = re.search(
        r"^(.*)\s+(?:in|on)\s+(?:the\s+)?(living room|bedroom|kitchen|office|bookshelf)\s*$",
        rest,
    )
    if m_in:
        raw_query = m_in.group(1).strip()
        room = m_in.group(2).strip().lower()

    logging.info(f"DEBUG_PLAY_BY_NAME_PARSED query={raw_query!r} room={room!r}")

    # Typed intent parsing MUST happen before stripping, otherwise we lose scope like "playlist ..."
    intent = _parse_typed_play_intent(raw_query)
    kind_req = intent.get("kind") or None
    query = (intent.get("query") or raw_query).strip()
    artist = intent.get("artist") or None

    if not query:
        return None

    # AI description resolution: if the query looks like a fuzzy description
    # ("song about a walrus", "lyrics I've got a hunger...") and a resolver is
    # available, ask AI to identify the exact title/artist before searching.
    if resolve_description and _looks_fuzzy_music_query(raw_query):
        ai = resolve_description(raw_query)
        if ai:
            kind_req = ai.get("kind") or kind_req
            query = (ai.get("title") or query).strip()
            artist = ai.get("artist") or artist
            logging.info(
                "DEBUG_PLAY_BY_NAME_AI_RESOLVED kind=%r query=%r artist=%r",
                kind_req, query, artist,
            )

    effective_query = raw_query  # keep original phrasing for spoken confirmation
    logging.info(
        "DEBUG_PLAY_BY_NAME_INTENT kind=%r query=%r artist=%r",
        kind_req,
        query,
        artist,
    )

    target_eid = (sonos_players or {}).get(room)
    if not target_eid:
        return None

    # Pinned playlists always win (but only when the user didn't request a non-playlist type)
    qn = _norm_pinned_key(query)
    pinned_uri = (pinned_spotify_playlists or {}).get(qn)

    if kind_req in (None, "playlist") and isinstance(pinned_uri, str) and pinned_uri.startswith("spotify:"):
        ok = call_ha_service(
            "media_player/play_media",
            {
                "entity_id": target_eid,
                "media_content_type": "music",
                "media_content_id": pinned_uri,
            },
        )
        if ok:
            logging.info("CLAIM: play_by_name_pinned")
            return maybe_say(f"Playing {effective_query}.")
        return None

    # Resolve (typed resolver preferred)
    if not spotify_web_configured():
        logging.info("CLAIM: play_by_name_spotify_not_configured query=%r", query)
        return friendly_missing("Spotify", missing("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN"))

    resolved = None
    if resolve_typed_play_request:
        try:
            resolved = resolve_typed_play_request(
                query,
                kind=kind_req,
                artist=artist,
            )
        except Exception:
            resolved = None

    # Backward compatible fallback
    if not resolved:
        resolved = resolve_play_request(query)

    if not resolved:
        return None

    uri, ctype = resolved

    # Scope guard: if user explicitly asked for a playlist, reject non-playlist URIs.
    if kind_req == "playlist" and not _is_playlist_uri(uri):
        logging.info("DEBUG_PLAY_BY_NAME_SCOPE_REJECT kind=playlist uri=%r ctype=%r", uri, ctype)
        return None

    # HA/Sonos in this environment cannot play spotify:artist: URIs directly.
    # Convert artist -> top track for a reliable playable URI.
    if isinstance(uri, str) and uri.startswith("spotify:artist:"):
        artist_id = uri.split(":")[-1]
        top = get_artist_top_track_uri(artist_id, market="US")
        if isinstance(top, str) and top.startswith("spotify:track:"):
            logging.info("CLAIM: play_by_name_artist_top_track")
            uri, ctype = top, "track"
        else:
            return None

    # HA/Sonos accepts Spotify URIs when media_content_type is "music".
    mct = "music" if isinstance(uri, str) and uri.startswith("spotify:") else ctype

    ok = call_ha_service(
        "media_player/play_media",
        {
            "entity_id": target_eid,
            "media_content_id": uri,
            "media_content_type": mct,
        },
    )
    if ok:
        logging.info("CLAIM: play_by_name_resolver")
        return maybe_say(f"Playing {effective_query}.")
    return None
