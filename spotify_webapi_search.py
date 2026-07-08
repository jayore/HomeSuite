import base64
import time
import re
import logging
from typing import Optional, Dict, Any, Tuple, List

import requests


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


class SpotifyClient:
    """
    Minimal Spotify Web API client using refresh token.
    Caches access token in-process.

    Public methods intentionally kept small:
      - get_access_token()
      - search(q, types, limit)
      - get_me_playlists_page(limit, offset)
    """

    _TOKEN_URL = "https://accounts.spotify.com/api/token"
    _API_BASE = "https://api.spotify.com/v1"

    def __init__(self, *, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token

        self._access_token: Optional[str] = None
        self._access_token_exp_ts: float = 0.0

        self._session = requests.Session()

    def _basic_auth_header(self) -> str:
        raw = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("utf-8")

    def get_access_token(self) -> Optional[str]:
        now = time.time()
        if self._access_token and now < (self._access_token_exp_ts - 30):
            return self._access_token

        try:
            r = self._session.post(
                self._TOKEN_URL,
                headers={"Authorization": self._basic_auth_header()},
                data={"grant_type": "refresh_token", "refresh_token": self.refresh_token},
                timeout=15,
            )
            if r.status_code != 200:
                logging.error("Spotify token error %s: %s", r.status_code, (r.text or "")[:200])
                return None
            data = r.json() or {}
            tok = data.get("access_token")
            exp = float(data.get("expires_in") or 3600)
            if not tok:
                logging.error("Spotify token response missing access_token")
                return None
            self._access_token = tok
            self._access_token_exp_ts = now + exp
            return tok
        except Exception as e:
            logging.error("Spotify token exception: %s", e)
            return None

    def _auth_headers(self) -> Optional[dict]:
        tok = self.get_access_token()
        if not tok:
            return None
        return {"Authorization": f"Bearer {tok}"}

    def search(self, *, q: str, types: List[str], limit: int = 8) -> Optional[Dict[str, Any]]:
        """
        Call Spotify Search API.

        Args:
          q: query string
          types: list like ["playlist","artist","album","track"]
          limit: max items per type (Spotify API limit applies)

        Returns:
          Spotify JSON dict or None on failure.
        """
        q = (q or "").strip()
        if not q:
            return None
        types = [t.strip() for t in (types or []) if isinstance(t, str) and t.strip()]
        if not types:
            return None

        headers = self._auth_headers()
        if not headers:
            return None

        try:
            r = self._session.get(
                f"{self._API_BASE}/search",
                headers=headers,
                params={"q": q, "type": ",".join(types), "limit": int(limit)},
                timeout=15,
            )
            if r.status_code != 200:
                logging.error("Spotify search error %s: %s", r.status_code, (r.text or "")[:200])
                return None
            return r.json() or {}
        except Exception as e:
            logging.error("Spotify search exception: %s", e)
            return None

    def get_me_playlists_page(self, limit: int = 50, offset: int = 0) -> Optional[Dict[str, Any]]:
        """
        Fetch one page of the current user's playlists.
        Returns Spotify API JSON (items/next/etc) or None on failure.
        """
        headers = self._auth_headers()
        if not headers:
            return None
        try:
            r = self._session.get(
                f"{self._API_BASE}/me/playlists",
                headers=headers,
                params={"limit": int(limit), "offset": int(offset)},
                timeout=15,
            )
            if r.status_code != 200:
                logging.error("Spotify me/playlists error %s: %s", r.status_code, (r.text or "")[:200])
                return None
            return r.json() or {}
        except Exception as e:
            logging.error("Spotify me/playlists exception: %s", e)
            return None


def pick_best_spotify_item(
    *,
    query: str,
    search_json: Dict[str, Any],
    prefer_types: List[str],
    artist: Optional[str] = None,
) -> Optional[Tuple[str, str, str]]:
    """
    Returns (kind, uri, title) where kind in {playlist, artist, album, track}.
    Deterministic selection with simple confidence rules.
    """
    qn = _norm(query)
    if not qn:
        return None
    artist_norm = _norm(artist or "")

    def iter_items(kind: str):
        block = (search_json.get(kind + "s") or {})
        items = block.get("items") or []
        for it in items:
            if isinstance(it, dict):
                yield it

    def item_artist_names(item: Dict[str, Any]) -> List[str]:
        artists = item.get("artists") or []
        out = []
        if isinstance(artists, list):
            for a in artists:
                if isinstance(a, dict):
                    name = _norm(a.get("name") or "")
                    if name:
                        out.append(name)
        return out

    def version_penalty(kind: str, title: str) -> int:
        if kind != "track":
            return 0
        tn = _norm(title)
        if not tn or qn not in tn:
            return 0
        rest = tn.replace(qn, " ", 1)
        if re.search(r"\b(take|demo|live|acoustic|instrumental|karaoke|edit|mix|version)\b", rest):
            return 25
        if re.search(r"\b(remaster|remastered|mono|stereo)\b", rest):
            return 3
        return 0

    def score_item(kind: str, item: Dict[str, Any]) -> Optional[int]:
        title = item.get("name") or ""
        tn = _norm(title)
        if not tn:
            return None
        if tn == qn:
            score = 0
        elif qn in tn or tn in qn:
            score = 10 + abs(len(tn) - len(qn))
        else:
            return None

        score += version_penalty(kind, title)
        if artist_norm and kind in ("track", "album"):
            artists = item_artist_names(item)
            if artists and artist_norm not in artists:
                if any(artist_norm in a or a in artist_norm for a in artists):
                    score += 3
                else:
                    score += 40
        return score

    best = None  # (score, -popularity, kind, uri, title)
    for kind in prefer_types:
        for it in iter_items(kind):
            title = it.get("name") or ""
            uri = it.get("uri") or ""
            sc = score_item(kind, it)
            if sc is None:
                continue
            if not (isinstance(uri, str) and uri.startswith("spotify:")):
                continue
            try:
                popularity = int(it.get("popularity") or 0)
            except Exception:
                popularity = 0
            cand = (sc, -popularity, kind, uri, title)
            if best is None or cand < best:
                best = cand

        # If we got an exact match in a preferred kind, stop early.
        if best and best[0] == 0:
            break

    if not best:
        return None

    score, _neg_popularity, kind, uri, title = best

    # Confidence gating: only accept very close matches.
    if score == 0:
        return (kind, uri, title)

    # Contains-match: allow only if reasonably close.
    if score <= 18:
        return (kind, uri, title)

    # Title + artist field filters often return canonical tracks with release
    # suffixes such as "Remastered 2009". Accept a looser score once the artist
    # was explicitly matched; variant penalties still affect which item wins.
    if artist_norm and score <= 40:
        return (kind, uri, title)

    return None


def find_user_playlist_uri_by_name(
    *,
    spotify: SpotifyClient,
    name: str,
    max_pages: int = 6,
    page_size: int = 50,
) -> Optional[str]:
    want = _norm(name)
    want_squash = want.replace(' ', '') if want else ''
    if not want:
        return None

    offset = 0
    for _ in range(max_pages):
        data = spotify.get_me_playlists_page(limit=page_size, offset=offset)
        if not data:
            return None
        items = data.get("items") or []
        for it in items:
            if not isinstance(it, dict):
                continue
            nm = it.get("name") or ""
            nm_norm = _norm(nm)
            if nm_norm == want:
                uri = it.get("uri")
                if isinstance(uri, str) and uri.startswith("spotify:"):
                    return uri
            # Also allow space-insensitive exact match (e.g. "vapor thump" vs "vaporthump")
            if want_squash:
                nm_squash = nm_norm.replace(" ", "") if nm_norm else ""
                if nm_squash and nm_squash == want_squash:
                    uri = it.get("uri")
                    if isinstance(uri, str) and uri.startswith("spotify:"):
                        return uri
        if not data.get("next"):
            break
        offset += page_size

    return None
