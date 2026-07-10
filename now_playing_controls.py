"""Format now-playing answers from Home Assistant media-player state.

The handler identifies the relevant Sonos or Apple TV entity from request/room
context, distinguishes TV passthrough from ordinary Sonos playback, and turns
available state attributes into concise spoken text. It is read-only and never
changes playback.
"""

import re
from typing import Optional, Dict, Any, List, Tuple


try:
    from app_config import (
        NOW_PLAYING_APPLE_TV_ORDER,
        NOW_PLAYING_APPLE_TV_INCLUDE,
        NOW_PLAYING_APPLE_TV_DEVICE_NAME,
        NOW_PLAYING_APPLE_TV_INCLUDE_EP_NUMBERS,
    )
except Exception:
    NOW_PLAYING_APPLE_TV_DEVICE_NAME = "Apple TV"
    NOW_PLAYING_APPLE_TV_ORDER = ["channel", "title", "app", "device"]
    NOW_PLAYING_APPLE_TV_INCLUDE = {"device": False, "app": False, "channel": True, "title": True}
    NOW_PLAYING_APPLE_TV_INCLUDE_EP_NUMBERS = False


def _join_parts(parts):
    parts = [p for p in (parts or []) if isinstance(p, str) and p.strip()]
    return ", ".join(parts).strip()


def _is_now_playing_query(tl: str) -> bool:
    t = (tl or "").strip().lower()
    # Normalize curly quotes that sometimes appear in STT
    t = t.replace("’", "'").replace("‘", "'")
    if not t:
        return False

    # Allow leading filler like: "hey", "ok", "so", "um", etc.
    # Whisper sometimes prefixes these, which used to break the ^-anchored patterns.
    t2 = re.sub(r"^(?:hey|ok(?:ay)?|so|well|um|uh|please)\b\s*[,\-:]*\s*", "", t).strip()

    patterns = [
        r"\bwhat('?s| is)\s+playing\b",
        r"\bwhat('?s| is)\s+on\b",
        r"\bnow\s+playing\b",
        r"\bwhat\s+song\s+is\s+this\b",
        r"\bwhat\s+is\s+this\s+song\b",
        r"\bwhat\s+am\s+i\s+listening\s+to\b",
        r"^\s*playing\s*[.?!]*\s*$",
    ]

    return any(re.search(p, t2) for p in patterns)


def _state_for_entity(states_snapshot: Optional[list], entity_id: str) -> Optional[dict]:
    if not states_snapshot or not entity_id:
        return None
    for s in states_snapshot:
        if s.get("entity_id") == entity_id:
            return s
    return None


def _is_media_active_state(state: Optional[str]) -> bool:
    return (state or "").lower() in ("playing", "paused")

def _is_activeish_entity(st: Optional[dict]) -> bool:
    """Treat HA 'idle/standby' with loaded metadata as active-ish."""
    st = st or {}
    state = _clean(st.get('state')).lower()
    if state in ('playing', 'paused', 'idle', 'standby', 'buffering'):
        return True
    attrs = st.get('attributes') or {}
    return bool(_clean(attrs.get('media_title')) or _clean(attrs.get('media_content_id')))



def _clean(s: Any) -> str:
    return str(s).strip() if s is not None else ""


def _pinned_station_name_from_content_id(content_id: str) -> Optional[str]:
    """
    Best-effort fallback: if Sonos/HA isn't exposing metadata for a raw stream,
    try to match the current media_content_id against PINNED_RADIO_STATIONS values.
    """
    try:
        from app_config import PINNED_RADIO_STATIONS
    except Exception:
        PINNED_RADIO_STATIONS = {}

    cid = _clean(content_id)
    if not cid or not isinstance(PINNED_RADIO_STATIONS, dict):
        return None

    # Match exact URI or substring (some stacks decorate/normalize the ID)
    for name, uri in (PINNED_RADIO_STATIONS or {}).items():
        u = _clean(uri)
        if not u:
            continue
        if cid == u or u in cid or cid in u:
            return _clean(name) or None
    return None


def _format_sonos_now_playing(st: dict) -> str:
    attrs = st.get("attributes") or {}
    state = _clean(st.get("state")).lower()

    title = _clean(attrs.get("media_title"))
    artist = _clean(attrs.get("media_artist"))
    album = _clean(attrs.get("media_album_name"))

    # Radio-ish fields vary by integration
    station = _clean(attrs.get("media_station")) or _clean(attrs.get("media_channel"))
    source = _clean(attrs.get("source"))
    content_id = _clean(attrs.get("media_content_id"))

    # If we have proper track metadata
    if title and artist:
        if album:
            return f"You're listening to {title} by {artist}, from {album}."
        return f"You're listening to {title} by {artist}."

    # If we have a station name
    if station:
        return f"You're listening to {station}."

    # If it’s TV audio through Sonos
    if source and source.lower() == "tv":
        # Some configs show source="TV" and no title/artist
        if state in ("playing", "paused"):
            return "You're listening to TV audio."
        return "The Sonos source is set to TV."

    # Try pinned-station fallback from content id
    pinned = _pinned_station_name_from_content_id(content_id)
    if pinned:
        return f"You're listening to {pinned}."

    # Last resort
    if state in ("playing", "paused"):
        return "Something is playing on Sonos, but I can't see the title."
    return "Nothing is currently playing."




def _format_apple_tv_now_playing(st: dict) -> str:
    attrs = st.get("attributes") or {}
    state = _clean(st.get("state")).lower()

    device = _clean(NOW_PLAYING_APPLE_TV_DEVICE_NAME) or "Apple TV"
    app = _clean(attrs.get("app_name")) or _clean(attrs.get("source"))

    # Title-ish fields
    title = _clean(attrs.get("media_title"))
    series = _clean(attrs.get("media_series_title"))

    # Episode numbers (optional)
    season = attrs.get("media_season")
    episode = attrs.get("media_episode")

    # Channel best-effort:
    # - For YouTube, HA commonly exposes channel as media_artist.
    # - Some integrations may put it in media_channel.
    channel = _clean(attrs.get("media_artist")) or _clean(attrs.get("media_channel"))
    # ---- Plex episodic normalization ----
    # In your HA snapshot for Plex-on-AppleTV, we often see:
    #   media_artist = Show name
    #   media_title  = "S2 · E3: Episode Title" (or similar)
    # We want: "Show, Episode Title" (no 'paused', no S/E prefix).
    app_l = (app or '').strip().lower()
    series_fallback = series or channel  # media_artist commonly carries the series for Plex
    title_clean = title
    if 'plex' in app_l and title_clean:
        m_ep = re.match(r"^\s*S\d+\s*[·\.]?\s*E\d+\s*[:\-–—]\s*(.+?)\s*$", title_clean)
        if m_ep:
            title_clean = _clean(m_ep.group(1))
    # Prefer a non-empty series name when possible
    if not series and series_fallback:
        series = _clean(series_fallback)
    if title_clean:
        title = title_clean
    # ---- end episodic normalization ----


    # Normalize "title" for episodic content:
    # Prefer "Show, Episode Title" when series exists.
    if series and title:
        if NOW_PLAYING_APPLE_TV_INCLUDE_EP_NUMBERS:
            try:
                if season is not None and episode is not None:
                    title_norm = f"{series} S{int(season)}E{int(episode)}, {title}"
                else:
                    title_norm = f"{series}, {title}"
            except Exception:
                title_norm = f"{series}, {title}"
        else:
            title_norm = f"{series}, {title}"
    elif series:
        title_norm = series
    else:
        title_norm = title

    # If no title at all but active, keep it short
    if not title_norm and state in ("playing", "paused"):
        # Build from whatever we have
        parts = []
        include = NOW_PLAYING_APPLE_TV_INCLUDE or {}
        order = NOW_PLAYING_APPLE_TV_ORDER or ["device", "app", "channel", "title"]
        mapping = {"device": device, "app": app, "channel": channel, "title": ""}
        for key in order:
            if include.get(key, False):
                parts.append(mapping.get(key, ""))
        out = _join_parts(parts)
        return out if out else "Apple TV is active, but I can't see the title."

    if not title_norm:
        return "Nothing is currently playing."

    # Build parts in preferred order
    include = NOW_PLAYING_APPLE_TV_INCLUDE or {}
    order = NOW_PLAYING_APPLE_TV_ORDER or ["device", "app", "channel", "title"]

    # If we are already speaking the show name as the 'channel', don't repeat it in the 'title'.
    # Example (Plex): channel='The Amazing World of Gumball', title_norm='The Amazing World of Gumball, The Treasure'
    title_for_parts = title_norm
    try:
        if include.get("channel", False) and include.get("title", False) and channel and title_norm:
            ch = channel.strip()
            tn = title_norm.strip()
            if ch and tn.lower().startswith(ch.lower()):
                rest = tn[len(ch):]
                rest = re.sub(r"^[\s,:\-–—]+", "", rest).strip()
                if rest:
                    title_for_parts = rest
    except Exception:
        title_for_parts = title_norm


    mapping = {
        "device": device,
        "app": app,
        "channel": channel,
        "title": title_for_parts,
    }

    parts = []
    for key in order:
        if include.get(key, False):
            parts.append(mapping.get(key, ""))

    out = _join_parts(parts)
    return out if out else title_norm


def handle_now_playing_controls(
    *,
    tl: str,
    states_snapshot: Optional[list],
    sonos_players: Dict[str, str],
    default_sonos_room: str,
    apple_tv_entity: str,
    get_transport_focus=None,
) -> Optional[str]:
    """Answer a recognized now-playing query from a state snapshot."""
    """
    Now-playing with sane arbitration when Sonos is a TV soundbar.

    KEY PRINCIPLE:
      - Use the SAME routing concept as bare transport: "transport focus" wins.
      - Room specified in the utterance wins over focus.
      - Otherwise:
          focus == tv    -> report Apple TV (even if Sonos music is playing elsewhere)
          focus == sonos -> report that Sonos player (unless it's TV passthrough, then report TV)
      - Only fall back to "what is playing" heuristics if focus is unknown.

    - Treat Sonos source=TV / htastream as "TV passthrough" (not music metadata).
    - Prefer real Sonos music when it's actually playing.
    - Otherwise prefer Apple TV when it has metadata (even if HA state is flaky around pause/resume).
    """
    if not _is_now_playing_query(tl):
        return None

    t = (tl or "").strip().lower()
    # Normalize curly quotes that sometimes appear in STT
    t = t.replace("’", "'").replace("‘", "'")
    states_snapshot = states_snapshot or []

    def _ts_num(st: Optional[dict]) -> float:
        # Return a numeric timestamp (seconds since epoch) so sorting is always safe.
        from datetime import datetime
        st = st or {}
        for k in ("last_changed", "last_updated"):
            v = _clean(st.get(k))
            if not v:
                continue
            try:
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                return float(dt.timestamp())
            except Exception:
                continue
        return 0.0

    def _is_tvsound_passthrough_sonos(st: dict) -> bool:
        attrs = (st or {}).get("attributes") or {}
        source = _clean(attrs.get("source")).strip().lower()
        cid = _clean(attrs.get("media_content_id")).strip()
        title = _clean(attrs.get("media_title")).strip().lower()

        # If we can see real track/radio metadata, treat it as music even if
        # Sonos still reports source='TV' for a moment (common with soundbars).
        artist = _clean(attrs.get("media_artist")).strip()
        station = _clean(attrs.get("media_station")).strip() or _clean(attrs.get("media_channel")).strip()
        album = _clean(attrs.get("media_album_name")).strip()
        has_trackish = bool((title and title != "tv") or artist or station or album)
        if has_trackish:
            return False

        cid_l = cid.lower()

        # Sonos TV input commonly shows htastream/spdif and/or title 'TV'
        if cid_l.startswith("x-sonos-htastream:"):
            return True
        if "spdif" in cid_l and (source == "tv" or title == "tv"):
            return True
        if source == "tv" and title == "tv":
            return True
        if title == "tv" and not artist and not station:
            return True
        return False

    def _is_playing(st: Optional[dict]) -> bool:
        return _clean((st or {}).get("state")).lower() == "playing"

    def _is_paused(st: Optional[dict]) -> bool:
        return _clean((st or {}).get("state")).lower() == "paused"

    def _tv_has_metadata(st: Optional[dict]) -> bool:
        # Apple TV can report non-(playing/paused) states while paused/resuming,
        # yet still retain useful metadata. If metadata exists, treat as reportable.
        attrs = (st or {}).get("attributes") or {}
        if _clean(attrs.get("media_title")):
            return True
        if _clean(attrs.get("media_series_title")):
            return True
        if _clean(attrs.get("media_artist")):
            return True
        if _clean(attrs.get("app_name")) or _clean(attrs.get("source")):
            return True
        if _clean(attrs.get("media_content_type")).lower() == "video":
            return True
        return False

    def _norm_room(s: str) -> str:
        return (s or "").strip().lower()

    def _find_room_in_text(tl2: str) -> Optional[str]:
        t2 = (tl2 or "").strip().lower()
        if not t2:
            return None
        # Prefer longest keys first (e.g., "living room" before "room")
        for room in sorted((sonos_players or {}).keys(), key=len, reverse=True):
            if re.search(rf"\b{re.escape(room)}\b", t2):
                return room
        return None

    tv_st = _state_for_entity(states_snapshot, apple_tv_entity)

    # Build Sonos candidates
    sonos_states = []
    for _room, eid in (sonos_players or {}).items():
        st = _state_for_entity(states_snapshot, eid)
        if st:
            sonos_states.append(st)

    sonos_music_playing = []
    sonos_music_paused = []
    sonos_any_active = []
    for st in sonos_states:
        if _is_playing(st) or _is_paused(st):
            sonos_any_active.append(st)
        if _is_tvsound_passthrough_sonos(st):
            continue
        if _is_playing(st):
            sonos_music_playing.append(st)
        elif _is_paused(st):
            sonos_music_paused.append(st)

    # ----------------------------
    # 0) Explicit targeting wins
    # ----------------------------
    # If user explicitly asked about a room, honor it (Sonos).
    room = _find_room_in_text(t)
    if room:
        eid = (sonos_players or {}).get(_norm_room(room))
        if eid:
            st = _state_for_entity(states_snapshot, eid)
            if st and (_is_playing(st) or _is_paused(st)):
                # If it's TV passthrough, defer to TV metadata instead of "TV audio".
                if _is_tvsound_passthrough_sonos(st):
                    if tv_st and ((_is_playing(tv_st) or _is_paused(tv_st)) or _tv_has_metadata(tv_st)):
                        return _format_apple_tv_now_playing(tv_st)
                    # fall back to sonos phrasing if we truly can't get TV details
                return _format_sonos_now_playing(st)
        return "Nothing is currently playing."

    # Explicit "on tv" / "apple tv" wins
    if " tv" in t or " apple tv" in t or " on tv" in t or " on apple tv" in t:
        if tv_st and ((_is_playing(tv_st) or _is_paused(tv_st)) or _tv_has_metadata(tv_st)):
            return _format_apple_tv_now_playing(tv_st)

    # Explicit "music" wins (real music only)
    if " music" in t or " on music" in t:
        default_eid = (sonos_players or {}).get(_norm_room(default_sonos_room))
        if default_eid:
            ds = _state_for_entity(states_snapshot, default_eid)
            if ds and not _is_tvsound_passthrough_sonos(ds) and (_is_playing(ds) or _is_paused(ds)):
                return _format_sonos_now_playing(ds)
        pool = sonos_music_playing or sonos_music_paused
        if pool:
            best = sorted(pool, key=_ts_num, reverse=True)[0]
            return _format_sonos_now_playing(best)

    # ----------------------------
    # 1) TRANSPORT FOCUS WINS
    # ----------------------------
    focus_kind = None
    focus_eid = None
    try:
        if callable(get_transport_focus):
            focus_kind, focus_eid = get_transport_focus()
    except Exception:
        focus_kind, focus_eid = None, None

    # If focus is TV, report TV (even if Sonos music is currently playing elsewhere)
    if focus_kind == "tv":
        if tv_st and ((_is_playing(tv_st) or _is_paused(tv_st)) or _tv_has_metadata(tv_st)):
            return _format_apple_tv_now_playing(tv_st)

    # If focus is Sonos, report that specific player if it's active.
    if focus_kind == "sonos" and focus_eid:
        st = _state_for_entity(states_snapshot, focus_eid)
        if st and (_is_playing(st) or _is_paused(st)):
            # If focused Sonos is TV passthrough, prefer TV metadata
            if _is_tvsound_passthrough_sonos(st):
                if tv_st and ((_is_playing(tv_st) or _is_paused(tv_st)) or _tv_has_metadata(tv_st)):
                    return _format_apple_tv_now_playing(tv_st)
            return _format_sonos_now_playing(st)

    # ----------------------------
    # 2) Fallback arbitration
    # ----------------------------
    # 2.1) If real Sonos music is playing, prefer newest
    if sonos_music_playing:
        best = sorted(sonos_music_playing, key=_ts_num, reverse=True)[0]
        return _format_sonos_now_playing(best)

    # 2.2) Otherwise, prefer Apple TV if reportable
    if tv_st and ((_is_playing(tv_st) or _is_paused(tv_st)) or _tv_has_metadata(tv_st)):
        return _format_apple_tv_now_playing(tv_st)

    # 2.3) Otherwise, real Sonos music paused
    if sonos_music_paused:
        best = sorted(sonos_music_paused, key=_ts_num, reverse=True)[0]
        return _format_sonos_now_playing(best)

    # 2.4) Otherwise any active Sonos (may be TV passthrough)
    if sonos_any_active:
        best = sorted(sonos_any_active, key=_ts_num, reverse=True)[0]
        # If it's TV passthrough and TV has metadata, prefer TV
        if _is_tvsound_passthrough_sonos(best):
            if tv_st and ((_is_playing(tv_st) or _is_paused(tv_st)) or _tv_has_metadata(tv_st)):
                return _format_apple_tv_now_playing(tv_st)
        return _format_sonos_now_playing(best)

    # 2.5) Last resort: if TV exists, format it; else nothing.
    if tv_st:
        return _format_apple_tv_now_playing(tv_st)

    return "Nothing is currently playing."
