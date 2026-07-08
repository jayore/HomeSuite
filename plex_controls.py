import re
import logging
from typing import Optional, Dict, Any, List, Tuple
import requests
import xml.etree.ElementTree as ET

# ============================================================
# Advanced Plex title matching (feature-flagged)
# ============================================================
import os

try:
    from env_compat import install_homesuite_env_aliases
    install_homesuite_env_aliases()
except Exception:
    pass
from runtime_mode import allow_real_effects

# ------------------------------------------------------------
# Production defaults (safe):
# Enable Plex advanced matching unless explicitly disabled via env.
# ------------------------------------------------------------
PIPHONE_PLEX_ADV_MATCH_DEFAULT_ON = True
if PIPHONE_PLEX_ADV_MATCH_DEFAULT_ON:
    try:
        if os.getenv("PIPHONE_PLEX_ADV_MATCH") is None:
            os.environ["PIPHONE_PLEX_ADV_MATCH"] = "1"
    except Exception:
        pass

# ------------------------------------------------------------
# Plex ordinal rewrite (feature-flagged)
# ------------------------------------------------------------
ENABLE_PLEX_ORDINAL_REWRITE = True

_ORDINAL_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
}

def _is_live_mode() -> bool:
    """
    Backward-compatible local wrapper around the shared runtime-mode helper.

    Important:
    - explicit test mode disables real playback
    - no-runtime-init alone does not imply dry-run behavior
    """
    return allow_real_effects()

def _rewrite_query_with_ordinals(query: str) -> str:
    """
    Rewrite queries like:
      - 'second matrix movie' -> 'Matrix Reloaded'
      - 'matrix 2' -> 'Matrix 2'
    Movies only. Rewrite-only (no scoring here).
    """
    if not ENABLE_PLEX_ORDINAL_REWRITE:
        return query

    q = query.lower().strip()

    # Must contain an ordinal or number AND a franchise-like noun
    m_num = re.search(r"\b(\d+)\b", q)
    m_word = re.search(r"\b(" + "|".join(_ORDINAL_WORDS.keys()) + r")\b", q)

    if not (m_num or m_word):
        return query

    # Extract number
    if m_num:
        n = int(m_num.group(1))
    else:
        n = _ORDINAL_WORDS.get(m_word.group(1))
        if not n:
            return query

    # Remove ordinal / movie words to get base title
    base = re.sub(r"\b(movie|film|the)\b", "", q)
    base = re.sub(r"\b(" + "|".join(_ORDINAL_WORDS.keys()) + r"|\d+)\b", "", base)
    base = re.sub(r"\s+", " ", base).strip()

    if not base:
        return query

    # Title-case base
    rewritten = f"{base.title()} {n}"
    logging.info(f"Plex ordinal rewrite: {query!r} -> {rewritten!r}")
    return rewritten


try:
    from plex_match_utils import score_candidate
except Exception:
    score_candidate = None


_PLEX_SEARCH_NOISE = {
    "latest", "newest", "most", "recent", "watch", "movie", "movies",
    "film", "films", "show", "shows", "series", "tv", "starring",
    "featuring", "with", "that", "the", "a", "an", "about", "when",
    "where", "of", "is", "was", "has", "have", "are", "were", "in",
    "on", "at", "for", "by", "from", "and", "or", "its", "their",
    "his", "her", "him", "they", "which", "who", "what", "says",
    "said", "hes", "shes", "its", "guy", "girl", "man", "woman",
}


def _extract_plex_search_keywords(query: str) -> str:
    words = re.sub(r"[^a-z0-9\s]", " ", (query or "").lower()).split()
    meaningful = [w for w in words if w not in _PLEX_SEARCH_NOISE and len(w) > 1]
    return " ".join(meaningful[:6])


def _plex_hub_search_candidates(
    *,
    plex_url: str,
    plex_token: str,
    query: str,
    limit: int = 15,
) -> list:
    """
    Search /hubs/search for movie/show candidates matching query keywords.
    Returns [{"title", "year", "kind"}, ...].
    """
    keywords = _extract_plex_search_keywords(query)
    if not keywords:
        return []

    try:
        r = requests.get(
            f"{plex_url}/hubs/search",
            headers={"X-Plex-Token": plex_token, "Accept": "application/json"},
            params={"query": keywords, "limit": limit},
            timeout=8,
        )
        if r.status_code != 200:
            logging.warning("[plex] hubs/search failed status=%s keywords=%r", r.status_code, keywords)
            return []

        candidates = []
        for hub in r.json().get("MediaContainer", {}).get("Hub", []):
            hub_type = (hub.get("type") or "").lower()
            if hub_type not in ("movie", "show"):
                continue
            for item in hub.get("Metadata", []):
                title = (item.get("title") or "").strip()
                year = item.get("year")
                if title:
                    candidates.append({"title": title, "year": year, "kind": hub_type})

        logging.info("[plex] hubs/search keywords=%r → %d candidates", keywords, len(candidates))
        return candidates

    except Exception as e:
        logging.warning("[plex] hubs/search error: %s", e)
        return []


PLEX_DEBUG_HUB_CANDIDATES = False


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[.!,?]+$", "", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s



def _plex_expand_query(query: str) -> List[str]:
    """
    Expand a user query into multiple Plex search queries to improve recall.
    This is recall-only; scoring happens later.
    """
    q = query.strip().lower()
    if not q:
        return []

    def _add_title_variants(out_set: set[str], value: str) -> None:
        v = (value or "").strip().lower()
        if not v:
            return
        out_set.add(v)

        dash_norm = re.sub(r"[–—−]", "-", v)
        out_set.add(dash_norm)

        # Canonical titles often arrive as "Franchise: Episode V - Subtitle"
        # while a local Plex library may store only "Subtitle".
        for sep in (":", " - "):
            if sep in dash_norm:
                tail = dash_norm.split(sep, 1)[1].strip()
                if tail:
                    out_set.add(tail)
                    stripped_tail = re.sub(
                        r"^(?:episode|part|chapter|volume|vol\.?)\s+"
                        r"(?:[ivxlcdm]+|\d+)(?:\s*[-:]\s*|\s+)",
                        "",
                        tail,
                        flags=re.IGNORECASE,
                    ).strip()
                    if stripped_tail:
                        out_set.add(stripped_tail)

        stripped = re.sub(
            r"\b(?:episode|part|chapter|volume|vol\.?)\s+"
            r"(?:[ivxlcdm]+|\d+)(?:\s*[-:]\s*|\s+)",
            " ",
            dash_norm,
            flags=re.IGNORECASE,
        )
        stripped = re.sub(r"\s+", " ", stripped).strip()
        if stripped and stripped != dash_norm:
            out_set.add(stripped)

    base_variants: set[str] = set()
    _add_title_variants(base_variants, q)
    out: set[str] = set(base_variants)

    # hyphen / colon variants
    for base in list(base_variants):
        out.add(base.replace(" ", "-"))
        out.add(base.replace("-", " "))
        out.add(base.replace(":", " "))
    out.add(q.replace(" ", ": "))

    # punctuation removal
    for base in list(base_variants):
        out.add(re.sub(r"[^a-z0-9]+", "", base))

    # v / vs / versus normalization
    out.add(re.sub(r"\bv\b", "vs", q))
    out.add(re.sub(r"\bvs\b", "versus", q))

    # movie hint (helps Plex bias movies)
    for base in list(base_variants):
        out.add(base + " movie")

    # de-duplicate & keep reasonable length
    return [x for x in sorted(out) if 2 <= len(x) <= 80]


def _extract_watch_title(tl: str) -> Optional[str]:
    t = _norm(tl)
    if not t:
        return None

    m_watch = re.match(r"^(watch)\s+(.+)$", t)
    m_play_plex = re.match(r"^(play)\s+(.+?)\s+on\s+plex$", t)
    if not (m_watch or m_play_plex):
        return None

    title = (m_watch.group(2) if m_watch else m_play_plex.group(2)).strip()
    title = re.sub(r"^(the|a|an)\s+", "", title).strip()
    return title or None


def _plex_get_clients(*, plex_url: str, plex_token: str, timeout_s: int = 8) -> List[Dict[str, str]]:
    """
    Returns a list of dicts with keys: name, host, port, machineIdentifier, product.
    Uses Plex server /clients (LAN discovery).
    """
    headers = {
        "X-Plex-Token": plex_token,
        "X-Plex-Client-Identifier": "piphone",
        "X-Plex-Product": "PiPhone",
        "X-Plex-Version": "1.0",
    }
    r = requests.get(f"{plex_url}/clients", headers=headers, timeout=timeout_s)
    if r.status_code != 200:
        logging.error(f"Plex /clients failed {r.status_code}: {r.text[:200]}")
        return []

    try:
        root = ET.fromstring(r.text)
    except Exception as e:
        logging.error(f"Plex /clients XML parse error: {e}")
        return []

    out = []
    for el in list(root):
        # In your output, clients show up as <Server .../>
        attrs = el.attrib or {}
        mi = attrs.get("machineIdentifier")
        host = attrs.get("host") or attrs.get("address")
        port = attrs.get("port")
        if mi and host and port:
            out.append({
                "name": attrs.get("name", ""),
                "host": host,
                "port": str(port),
                "machineIdentifier": mi,
                "product": attrs.get("product", ""),
            })
    return out



def _plex_get_view_offset_ms(*, plex_url: str, plex_token: str, rating_key: str, timeout_s: int = 10) -> Optional[int]:
    """
    Returns viewOffset (ms) if Plex has a resume point for this item; else None.
    """
    headers = {"X-Plex-Token": plex_token}

    # Ask Plex directly for metadata on this ratingKey
    r = requests.get(
        f"{plex_url}/library/metadata/{rating_key}",
        headers=headers,
        timeout=timeout_s,
    )
    if r.status_code != 200:
        logging.error(f"Plex metadata failed {r.status_code}: {r.text[:200]}")
        return None

    try:
        root = ET.fromstring(r.text)
    except Exception as e:
        logging.error(f"Plex metadata XML parse error: {e}")
        return None

    # viewOffset is in milliseconds
    el = root.find(".//Video")
    if el is None:
        return None

    vo = el.attrib.get("viewOffset")
    if not vo:
        return None

    try:
        return int(vo)
    except Exception:
        return None

def _plex_pick_show_episode(
    *,
    plex_url: str,
    plex_token: str,
    show_rating_key: str,
    show_title: Optional[str] = None,
    timeout_s: int = 12,
) -> Optional[Tuple[str, str]]:
    """
    Choose what to play for a show:

      1) Resume an in-progress episode (viewOffset > 0)
      2) Otherwise pick the next unwatched episode (viewCount missing/0)
      3) Fallback: first episode

    Returns (episode_rating_key, pretty_title).
    """
    headers = {"X-Plex-Token": plex_token}

    r = requests.get(
        f"{plex_url}/library/metadata/{show_rating_key}/allLeaves",
        headers=headers,
        timeout=timeout_s,
    )
    if r.status_code != 200:
        logging.error(f"Plex show allLeaves failed {r.status_code}: {r.text[:200]}")
        return None

    try:
        root = ET.fromstring(r.text)
    except Exception as e:
        logging.error(f"Plex show allLeaves XML parse error: {e}")
        return None

    def _to_int(x):
        try:
            return int(x) if x is not None else None
        except Exception:
            return None

    episodes = []
    for v in root.findall(".//Video"):
        if (v.attrib.get("type") or "").strip().lower() != "episode":
            continue

        rk = (v.attrib.get("ratingKey") or "").strip()
        title = (v.attrib.get("title") or "").strip()
        if not rk or not title:
            continue

        gp = (v.attrib.get("grandparentTitle") or "").strip()  # show title
        season = _to_int(v.attrib.get("parentIndex")) or 0
        ep = _to_int(v.attrib.get("index")) or 0
        vc = _to_int(v.attrib.get("viewCount"))
        vo = _to_int(v.attrib.get("viewOffset"))
        va = _to_int(v.attrib.get("viewedAt"))

        pretty = f"{gp} S{str(season).zfill(2)}E{str(ep).zfill(2)}. {title}" if gp and season and ep else title

        episodes.append({
            "rk": rk,
            "pretty": pretty,
            "season": season,
            "ep": ep,
            "viewCount": vc,
            "viewOffset": vo,
            "viewedAt": va,
        })

    if not episodes:
        logging.error(f"Plex show pick: no episodes found for show_rating_key={show_rating_key}")
        return None

    # canonical order
    episodes.sort(key=lambda x: (x["season"], x["ep"]))

    # 1) Resume in-progress (prefer most recently viewedAt; else latest season/ep)
    in_progress = [e for e in episodes if isinstance(e["viewOffset"], int) and e["viewOffset"] > 0]
    if in_progress:
        in_progress.sort(
            key=lambda x: (
                x["viewedAt"] or 0,
                x["season"],
                x["ep"],
            ),
            reverse=True,
        )
        pick = in_progress[0]
        logging.info(f"Plex show pick: resume rk={pick['rk']} pretty={pick['pretty']!r} viewOffset={pick['viewOffset']}")
        return (pick["rk"], pick["pretty"])

    # 2) Next unwatched
    for e in episodes:
        vc = e["viewCount"]
        if vc is None or vc == 0:
            logging.info(f"Plex show pick: next-unwatched rk={e['rk']} pretty={e['pretty']!r}")
            return (e["rk"], e["pretty"])

    # 3) Fallback: first episode
    pick = episodes[0]
    logging.info(f"Plex show pick: fallback-first rk={pick['rk']} pretty={pick['pretty']!r}")
    return (pick["rk"], pick["pretty"])

def _plex_search_best_title(
    *,
    plex_url: str,
    plex_token: str,
    query: str,
    timeout_s: int = 12
) -> Optional[Dict[str, str]]:
    """
    Resolve a query to either a movie or a show.

    Preserves the known-good behavior:
      - spoken-title aliases (app_config.PLEX_TITLE_ALIASES)
      - query expansion
      - episode filtering unless explicitly requested
      - verbatim dominance
      - advanced scoring fallback (score_candidate)

    Adds ordinal movie support (surgical):
      - "second matrix movie" => search "matrix", then pick 2nd matching movie by year
      - "watch second matrix" => same
    """

    orig_query = query

    # ------------------------------------------------------------
    # Spoken-title aliases (from app_config)
    # ------------------------------------------------------------
    try:
        from app_config import PLEX_TITLE_ALIASES

        def _norm_alias(s: str) -> str:
            s = (s or "").lower()
            s = re.sub(r"[^a-z0-9]+", " ", s)
            return re.sub(r"\s+", " ", s).strip()

        spoken = _norm_alias(query)

        canonical = None
        for canon, aliases in (PLEX_TITLE_ALIASES or {}).items():
            if spoken == _norm_alias(canon):
                canonical = canon
                break
            for a in (aliases or []):
                if spoken == _norm_alias(a):
                    canonical = canon
                    break
            if canonical:
                break

        if canonical:
            logging.info(f"Plex alias rewrite: {query!r} -> {canonical!r}")
            query = canonical
    except Exception:
        pass

    headers = {"X-Plex-Token": plex_token}

    # ------------------------------------------------------------
    # Ordinal intent parsing (pre-search)
    #   If ordinal present, we search only the stem/franchise words
    # ------------------------------------------------------------
    ORD_WORDS = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
        "sixth": 6,
        "seventh": 7,
        "eighth": 8,
        "ninth": 9,
        "tenth": 10,
    }

    STOP = {
        "watch","play","the","a","an","of","for","to","on","in","at",
        "movie","film","movies","films","please","plex"
    }

    def _norm_tokens(s: str):
        s = (s or "").lower()
        s = re.sub(r"[^a-z0-9]+", " ", s)
        return [t for t in s.split() if t]

    def _extract_ordinal_and_stem(q: str):

        ql = (q or '').lower()

        n = None

        from_end = False


        # Reverse-ordinal intent words (implicit 1st-from-end)

        if re.search(r"\bnewest\b", ql) or re.search(r"\blatest\b", ql) or re.search(r"\bmost\s+recent\b", ql):

            from_end = True

            ql = re.sub(r"\bnewest\b", " ", ql)

            ql = re.sub(r"\blatest\b", " ", ql)

            ql = re.sub(r"\bmost\s+recent\b", " ", ql)


        # Strip generic media words so they don't pollute the stem

        ql = re.sub(r"\b(movie|movies|film|films)\b", " ", ql)


        # word ordinals

        for w, v in ORD_WORDS.items():

            if re.search(rf"\b{re.escape(w)}\b", ql):

                n = v

                ql = re.sub(rf"\b{re.escape(w)}\b", " ", ql)

                break


        # numeric ordinals like "2nd"

        if n is None:

            m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", ql)

            if m:

                try:

                    cand = int(m.group(1))

                except Exception:

                    cand = None

                if cand is not None and 1 <= cand <= 20:

                    n = cand

                    ql = re.sub(rf"\b{re.escape(m.group(0))}\b", " ", ql)


        if n is None and not from_end:

            return None

        if n is None and from_end:

            n = 1


        toks = [t for t in _norm_tokens(ql) if t not in STOP]

        if not toks:

            return None

        return {'n': n, 'stem': toks, 'from_end': from_end}


    ord_info = None
    try:
        ord_raw = _extract_ordinal_and_stem(query)
        if isinstance(ord_raw, tuple):
            ord_info = {'n': ord_raw[0], 'stem': ord_raw[1], 'from_end': False}
        else:
            ord_info = ord_raw
    except Exception:
        ord_info = None

    search_query = query
    if ord_info:
        if ord_info.get('from_end') and not ord_info.get('n'):
            ord_info['n'] = 1
        ord_n = ord_info.get('n')
        stem_tokens_list = ord_info.get('stem') or []
        search_query = " ".join(stem_tokens_list).strip()
        logging.info(
            f"CLAIM: plex_ordinal_pre_search orig={orig_query!r} aliased={query!r} "
            f"stem={stem_tokens_list!r} n={ord_n} search_query={search_query!r}"
        )

    expanded_queries = _plex_expand_query(search_query)
    if not expanded_queries:
        return None

    seen = set()
    all_candidates = []

    # ------------------------------------------------------------
    # Plex search
    # ------------------------------------------------------------
    for q in expanded_queries:
        r = requests.get(
            f"{plex_url}/search",
            headers=headers,
            params={"query": q},
            timeout=timeout_s,
        )
        if r.status_code != 200:
            continue

        try:
            root = ET.fromstring(r.text)
        except Exception:
            continue

                # Movies + episodes + shows (namespace-safe; Provider fan-out)
        roots = [root]
        try:
            provs = []
            provs += list(root.findall('.//Provider'))
            provs += list(root.findall('.//{*}Provider'))
            keys = []
            seenk = set()
            for p in provs:
                k = p.attrib.get('key')
                if k and k not in seenk:
                    seenk.add(k)
                    keys.append(k)
            for k in keys[:6]:
                url = None
                if k.startswith('http://') or k.startswith('https://'):
                    url = k
                elif k.startswith('/'):
                    url = plex_url + k
                if not url:
                    continue
                rr = requests.get(url, headers=headers, timeout=timeout_s)
                if rr.status_code != 200:
                    continue
                try:
                    roots.append(ET.fromstring(rr.text))
                except Exception:
                    pass
        except Exception:
            pass

        for rx in roots:
            vids = []
            vids += list(rx.findall('.//Video'))
            vids += list(rx.findall('.//{*}Video'))
            vids += list(rx.findall('.//Metadata'))
            vids += list(rx.findall('.//{*}Metadata'))
            for v in vids:
                rk = v.attrib.get('ratingKey')
                title = (v.attrib.get('title') or '').strip()
                typ = (v.attrib.get('type') or '').strip().lower()
                year_s = (v.attrib.get('year') or '').strip()
                if not rk or not title or rk in seen:
                    continue
                if not typ:
                    continue
                seen.add(rk)
                pretty = f"{title} ({year_s})" if year_s else title
                try:
                    year_i = int(year_s) if year_s.isdigit() else 0
                except Exception:
                    year_i = 0
                all_candidates.append({'type': typ, 'title': title, 'ratingKey': rk, 'pretty': pretty, 'year': year_i})

            dirs = []
            dirs += list(rx.findall('.//Directory'))
            dirs += list(rx.findall('.//{*}Directory'))
            for d in dirs:
                if (d.attrib.get('type') or '').strip().lower() != 'show':
                    continue
                rk = d.attrib.get('ratingKey')
                title = (d.attrib.get('title') or '').strip()
                if not rk or not title or rk in seen:
                    continue
                seen.add(rk)
                all_candidates.append({'type': 'show', 'title': title, 'ratingKey': rk, 'pretty': title})

    if not all_candidates:
        # NOTE:
        # Do NOT return yet — ordinal collection resolver may still succeed
        pass

    # ------------------------------------------------------------
    # Ordinal resolver (movies only; franchise-aware by token subset + year sort)
    # ------------------------------------------------------------
    if ord_info:
        ord_n = ord_info.get('n')
        stem_tokens_list = ord_info.get('stem') or []
        stem_set = set(stem_tokens_list)

        # ------------------------------------------------------------
        # Plex COLLECTION-based ordinal resolver (movies only)
        #   Uses Plex collections for franchises (James Bond, Star Wars, etc.)
        #   Falls back cleanly to title-based ordinal logic.
        # ------------------------------------------------------------
        try:
            from app_config import PLEX_COLLECTION_ALIASES, PLEX_ORDINAL_EXCLUDES
        except Exception:
            PLEX_COLLECTION_ALIASES = {}
            PLEX_ORDINAL_EXCLUDES = {}

        def _norm_coll(s: str) -> str:
            s = (s or "").lower()
            s = re.sub(r"[^a-z0-9]+", " ", s)
            return re.sub(r"\s+", " ", s).strip()

        # ------------------------------------------------------------
        # Resolve collection aliases
        #   Canonical collection name -> [spoken aliases]
        # ------------------------------------------------------------
        def _norm_alias(s: str) -> str:
            s = (s or "").lower()
            s = re.sub(r"[^a-z0-9]+", " ", s)
            return re.sub(r"\s+", " ", s).strip()

        spoken = _norm_alias(" ".join(stem_tokens_list))

        # ------------------------------------------------------------
        # Numeric franchise normalization (e.g. "007" -> "james bond")
        # ------------------------------------------------------------
        if spoken.isdigit() and spoken == "007":
            logging.info("Plex collection normalize: '007' -> 'james bond'")
            spoken = "james bond"

        canonical = None
        for canon, aliases in (PLEX_COLLECTION_ALIASES or {}).items():
            canon_norm = _norm_alias(canon)
            if spoken == canon_norm:
                canonical = canon
                break
            for a in aliases or []:
                if spoken == _norm_alias(a):
                    canonical = canon
                    break
            if canonical:
                break

        # Fall back to derived stem if no alias matched
        coll_key = canonical or " ".join(sorted(stem_set))
        coll_query = coll_key
        coll_query_norm = _norm_coll(coll_query)
        logging.info(f"CLAIM: plex_collection_try key={coll_key!r} query={coll_query!r}")

        try:
            r_sec = requests.get(
                f"{plex_url}/library/sections",
                headers=headers,
                timeout=timeout_s,
            )
            if r_sec.status_code == 200:
                root_sec = ET.fromstring(r_sec.text)
                movie_sections = [
                    d.attrib.get("key")
                    for d in root_sec.findall(".//Directory")
                    if d.attrib.get("type") == "movie"
                ]

                for sid in movie_sections:
                    r_col = requests.get(
                        f"{plex_url}/library/sections/{sid}/collections",
                        headers=headers,
                        timeout=timeout_s,
                    )
                    if r_col.status_code != 200:
                        continue

                    root_col = ET.fromstring(r_col.text)
                    for c in root_col.findall(".//Directory"):
                        title = c.attrib.get("title") or ""
                        tnorm = _norm_coll(title.replace("collection", ""))
                        if coll_query_norm not in tnorm:
                            continue

                        r_items = requests.get(
                            f"{plex_url}{c.attrib.get('key')}",
                            headers=headers,
                            timeout=timeout_s,
                        )
                        if r_items.status_code != 200:
                            continue

                        root_items = ET.fromstring(r_items.text)
                        movies_c = []
                        for v in root_items.findall(".//Video"):
                            if v.attrib.get("type") != "movie":
                                continue
                            title_v = v.attrib.get("title") or ""
                            rk_v = v.attrib.get("ratingKey")
                            year_v = int(v.attrib.get("year") or 0)
                            pretty_v = (
                                f"{title_v} ({year_v})" if year_v else title_v
                            )
                            movies_c.append(
                                (
                                    year_v,
                                    title_v,
                                    {
                                        "type": "movie",
                                        "ratingKey": rk_v,
                                        "pretty": pretty_v,
                                    },
                                )
                            )

                        excl = set(
                            e.lower()
                            for e in PLEX_ORDINAL_EXCLUDES.get(coll_key, [])
                        )
                        if excl:
                            movies_c = [
                                m
                                for m in movies_c
                                if not any(e in m[1].lower() for e in excl)
                            ]

                        movies_c.sort(key=lambda x: (x[0], x[1]))

                        try:
                            n_int = int(ord_n or 1)
                        except Exception:
                            n_int = 1
                        if ord_info.get("from_end"):
                            # reverse ordinal: newest=1 => last item
                            idx = len(movies_c) - n_int
                        else:
                            idx = n_int - 1
                        if 0 <= idx < len(movies_c):
                            pick = movies_c[idx][2]
                            logging.info(
                                f"CLAIM: plex_ordinal_resolved (collection) -> "
                                f"{pick.get('pretty')!r}"
                            )
                            return pick
        except Exception:
            logging.exception("mark_action_occurred failed")
            # IMPORTANT: fall through to title-based ordinal resolver

        movies = []
        for c in all_candidates:
            if c.get("type") != "movie":
                continue
            title_tokens = set(_norm_tokens(c.get("title") or ""))
            if not stem_set.issubset(title_tokens):
                continue
            movies.append((int(c.get("year") or 0), (c.get("title") or ""), c))

        if movies:
            # Apply ordinal excludes (prevents known bad matches like 'Dune Drifter')
            try:
                excl = set(e.lower() for e in PLEX_ORDINAL_EXCLUDES.get(coll_key, []))
            except Exception:
                excl = set()
            if excl:
                movies = [m for m in movies if not any(e in ((m[1] or '').lower()) for e in excl)]

            movies.sort(key=lambda x: (x[0], x[1]))
            if ord_info.get("from_end"):
                idx2 = len(movies) - int(ord_n)
            else:
                idx2 = int(ord_n) - 1
            if 0 <= idx2 < len(movies):
                pick = movies[idx2][2]
                logging.info(

                    f"CLAIM: plex_ordinal_resolved orig={orig_query!r} -> {pick.get('pretty')!r} "
                    f"n={ord_n} candidates={len(movies)}"
                )
                return {
                    "kind": pick.get("type"),
                    "ratingKey": pick.get("ratingKey"),
                    "pretty": pick.get("pretty"),
                }

    # ------------------------------------------------------------
    # Verbatim dominance (short-circuit exact-ish matches)
    # ------------------------------------------------------------
    def _norm_title_for_match(s: str) -> str:
        s = (s or "").strip().lower()
        s = re.sub(r"[\-–—_:./\\]+", " ", s)
        s = re.sub(r"[^a-z0-9\s]+", "", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    qn = _norm_title_for_match(query)
    if qn:
        wants_episode = bool(
            re.search(r"(episode|season|s\d+e\d+)", (query or "").lower())
        )

        exact = []
        for c in all_candidates:
            if c.get("type") == "episode" and not wants_episode:
                continue
            tn = _norm_title_for_match(c.get("title"))
            if not tn:
                continue

            if tn == qn:
                exact.append(c)
                continue

            tn_no = re.sub(r"^(the|a|an)\s+", "", tn)
            qn_no = re.sub(r"^(the|a|an)\s+", "", qn)
            if tn_no and qn_no and tn_no == qn_no:
                exact.append(c)

        if exact:
            def _type_rank(t: str) -> int:
                t = (t or "").lower()
                if t == "movie":
                    return 3
                if t == "show":
                    return 2
                if t == "episode":
                    return 1
                return 0

            exact.sort(key=lambda c: _type_rank(c.get("type")), reverse=True)
            best = exact[0]
            logging.info(
                f"Plex VERBATIM pick kind={best.get('type')} pretty={best.get('pretty')!r}"
            )
            return {
                "kind": best.get("type"),
                "ratingKey": best.get("ratingKey"),
                "pretty": best.get("pretty"),
            }

    # ------------------------------------------------------------
    # Advanced scoring fallback
    # ------------------------------------------------------------
    if score_candidate:
        scored = []
        wants_episode = bool(
            re.search(r"(episode|season|s\d+e\d+)", (query or "").lower())
        )

        for c in all_candidates:
            if c.get("type") == "episode" and not wants_episode:
                continue
            s_val, breakdown = score_candidate(query, c)
            scored.append((s_val, c, breakdown))

        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            best_score, best, breakdown = scored[0]
            logging.info(
                f"Plex ADV pick score={best_score} kind={best.get('type')} "
                f"pretty={best.get('pretty')!r} breakdown={breakdown}"
            )
            return {
                "kind": best.get("type"),
                "ratingKey": best.get("ratingKey"),
                "pretty": best.get("pretty"),
            }

    # ------------------------------------------------------------
    # Legacy fallback
    # ------------------------------------------------------------
    if not all_candidates:
        return None

    best = all_candidates[0]
    return {
        "kind": best.get("type"),
        "ratingKey": best.get("ratingKey"),
        "pretty": best.get("pretty"),
    }



def _plex_create_playqueue(
    *,
    plex_url: str,
    plex_token: str,
    rating_key: str,
    server_machine_id: str,
    timeout_s: int = 12,
) -> Optional[str]:
    """
    Create a Plex playQueue for a given ratingKey.
    Returns playQueueID as string.
    """
    headers = {
        "X-Plex-Token": plex_token,
        "X-Plex-Client-Identifier": "piphone",
        "X-Plex-Product": "PiPhone",
        "X-Plex-Version": "1.0",
        "Accept": "application/xml",
    }
    key = f"/library/metadata/{rating_key}"

    params = {
        "type": "video",
        # server://<serverMachineId>/<key> is the typical URI form
        # This Plex server expects library://x/directory/%2Fmetadata%2F<ratingKey>
        "uri": f"library://x/directory/%2Fmetadata%2F{rating_key}",
        "shuffle": "0",
        "repeat": "0",
        "own": "1",
    }

    url = f"{plex_url}/playQueues"
    logging.info(f"Plex playQueues request url={url} params={params!r}")
    safe_headers = dict(headers)
    if "X-Plex-Token" in safe_headers:
        safe_headers["X-Plex-Token"] = "<redacted>"
    logging.info(f"Plex playQueues request headers={safe_headers!r}")
    r = requests.post(url, headers=headers, params=params, timeout=timeout_s)
    logging.info(f"Plex playQueues response status={r.status_code} body={r.text[:300]!r}")
    if r.status_code not in (200, 201):
        logging.error(f"Plex playQueues failed {r.status_code}: {r.text[:500]}")
        return None

    try:
        root = ET.fromstring(r.text)
        pqid = root.attrib.get("playQueueID") or root.attrib.get("playQueueId")
        logging.info(f"Plex playQueues parsed playQueueID={pqid!r}")
        return str(pqid) if pqid else None
    except Exception as e:
        logging.error(f"Plex playQueues XML parse error: {e}")
        return None

def _plex_play_on_client(
    *,
    plex_url: str,
    plex_token: str,
    client_machine_id: str,
    client_address: str,
    client_port: str,
    rating_key: str,
    offset_ms: int = 0,
    timeout_s: int = 12,
) -> bool:
    """
    Play using Play Queue workflow (more compatible across Plex versions).
    """
    headers = {
        "X-Plex-Token": plex_token,
        "X-Plex-Client-Identifier": "piphone",
        "X-Plex-Target-Client-Identifier": client_machine_id,
        "X-Plex-Product": "PiPhone",
        "X-Plex-Version": "1.0",
        "Accept": "application/xml",
    }

    # We need the Plex server's machine id for server:// URIs.
    # This is available from the Plex server itself.
    r_id = requests.get(f"{plex_url}/", headers={"X-Plex-Token": plex_token}, timeout=8)
    if r_id.status_code != 200:
        logging.error(f"Plex server root failed {r_id.status_code}: {r_id.text[:200]}")
        return False
    try:
        root = ET.fromstring(r_id.text)
        server_machine_id = root.attrib.get("machineIdentifier")
    except Exception as e:
        logging.error(f"Plex server root XML parse error: {e}")
        return False
    if not server_machine_id:
        logging.error("Plex server machineIdentifier missing from /")
        return False

    pqid = _plex_create_playqueue(
        plex_url=plex_url,
        plex_token=plex_token,
        rating_key=rating_key,
        server_machine_id=server_machine_id,
    )
    if not pqid:
        return False

    # Remote-control contract: send command to CLIENT, describe SERVER in params.
    m = re.match(r"^(https?)://([^/:]+)(?::(\d+))?", plex_url.strip())
    if not m:
        logging.error(f"Plex: could not parse plex_url={plex_url!r}")
        return False
    server_protocol = m.group(1)
    server_address = m.group(2)
    server_port = m.group(3) or "32400"

    client_url = f"http://{client_address}:{client_port}/player/playback/playMedia"
    media_key = f"/library/metadata/{rating_key}"
    container_key = f"/playQueues/{pqid}?window=100&own=1"

    params = {
        # SERVER routing (client uses this to fetch container/media)
        "machineIdentifier": server_machine_id,
        "protocol": server_protocol,
        "address": server_address,
        "port": str(server_port),
        # Playback
        "type": "video",
            "offset": str(int(offset_ms or 0)),
        "key": media_key,
        "containerKey": container_key,
        "playQueueID": str(pqid),
        "commandID": "1",
        # Some clients expect token as query param
        "token": plex_token,
    }

    safe_params = dict(params)
    if "token" in safe_params:
        safe_params["token"] = "<redacted>"
    # Try direct play (no playQueue container) first; some clients choke on /playQueues containers.
    params_direct = {
        "machineIdentifier": server_machine_id,
        "protocol": server_protocol,
        "address": server_address,
        "port": str(server_port),
        "type": "video",
        "offset": str(int(offset_ms or 0)),
        "key": media_key,
        "path": f"{server_protocol}://{server_address}:{server_port}{media_key}",
        "commandID": "1",
        "token": plex_token,
    }
    safe_pd = dict(params_direct)
    if "token" in safe_pd:
        safe_pd["token"] = "<redacted>"

    # PLEX_CLIENT_RETRY: Apple TV Plex player endpoint (32500) may not be listening yet
    # right after we launch Plex. Retry briefly instead of crashing pplive.
    def _client_get_with_retry(params_obj, *, label: str):
        import time as _time
        import requests as _requests
        attempts = 10  # ~4–6s worst case depending on backoff
        delay = 0.25
        last_err = None
        for i in range(attempts):
            try:
                return _requests.get(client_url, headers=headers, params=params_obj, timeout=timeout_s)
            except _requests.exceptions.ConnectionError as e:
                last_err = e
                # If this was the last try, log and give up gracefully.
                if i >= attempts - 1:
                    logging.error(
                        "Plex playMedia(%s): client endpoint not ready after retries url=%s err=%s",
                        label, client_url, e,
                    )
                    return None
                _time.sleep(delay)
                delay = min(delay * 1.6, 1.0)
        if last_err:
            logging.error("Plex playMedia(%s): unexpected retry fallthrough err=%s", label, last_err)
        return None

    logging.info(f"Plex playMedia(client,direct) request url={client_url} params={safe_pd!r}")
    r = _client_get_with_retry(params_direct, label="direct")

    if r is None:

        return False
    logging.info(f"Plex playMedia(client,direct) response status={r.status_code} body={r.text[:300]!r}")
    if r.status_code in (200, 201):
        return True

    logging.info(f"Plex playMedia(client) request url={client_url} params={safe_params!r}")
    r = _client_get_with_retry(params, label="queue")

    if r is None:

        return False
    logging.info(f"Plex playMedia(client) response status={r.status_code} body={r.text[:300]!r}")
    if r.status_code not in (200, 201):
        logging.error(f"Plex playMedia(client) failed {r.status_code}: {r.text[:500]}")
        return False
    return True

def handle_plex_controls(
    *,
    tl: str,
    maybe_say,
    plex_url: str,
    plex_token: str,
    prefer_client_name: Optional[str] = None,
    mark_action_occurred=None,
    resolve_description=None,
    **_ignored,
) -> Optional[str]:

    """
    Real Plex handler:
      - "watch <title>" -> search Plex -> play on Apple TV Plex client (if discoverable)
    """
    title = _extract_watch_title(tl)
    if not title:
        return None

    # AI description resolution: if query looks fuzzy and resolver is available,
    # search Plex for library candidates first, then ask AI to pick from actual results.
    if resolve_description:
        try:
            from plex_resolver import _looks_fuzzy_plex_query
            import re as _re
            if _looks_fuzzy_plex_query(title):
                candidates = _plex_hub_search_candidates(
                    plex_url=plex_url, plex_token=plex_token, query=title
                )
                # Scope candidates to the requested media type when stated explicitly.
                _tl = title.lower()
                _wants_movie = bool(_re.search(r"\b(movie|film)\b", _tl))
                _wants_show = bool(_re.search(r"\b(show|series|tv)\b", _tl))
                if _wants_movie and not _wants_show:
                    candidates = [c for c in candidates if c.get("kind") == "movie"]
                elif _wants_show and not _wants_movie:
                    candidates = [c for c in candidates if c.get("kind") == "show"]

                # For explicit recency queries, skip AI and just pick the most recent
                # movie/show from library candidates directly — no hallucination risk.
                _is_recency = bool(_re.search(r"\b(latest|newest|most\s+recent)\b", _tl))
                if _is_recency and candidates:
                    best = max(candidates, key=lambda c: c.get("year") or 0)
                    logging.info("[plex] recency pick: %r → %r (year=%s)", title, best["title"], best.get("year"))
                    title = best["title"]
                else:
                    ai = resolve_description(title, candidates=candidates or None)
                    if ai:
                        resolved_title = (ai.get("title") or "").strip()
                        if resolved_title:
                            logging.info(
                                "[plex] AI resolved: %r → %r (kind=%r, via_library=%s)",
                                title, resolved_title, ai.get("kind"), bool(candidates),
                            )
                            title = resolved_title
        except Exception as e:
            logging.warning("[plex] resolve_description failed: %s", e)

    logging.info(f"CLAIM: plex_watch_request title={title!r}")

    # Find a discoverable Plex client to play on
    clients = _plex_get_clients(plex_url=plex_url, plex_token=plex_token)
    if not clients:
        logging.error("Plex: no clients discovered via /clients (Apple TV likely not advertising).")
        return f"I found {title}, but I can't reach the Plex player yet."

    target = None
    if prefer_client_name:
        for c in clients:
            if (c.get("name") or "").strip().lower() == prefer_client_name.strip().lower():
                target = c
                break
    if target is None:
        # Prefer Apple TV / Plex for Apple TV if present
        for c in clients:
            prod = (c.get("product") or "").lower()
            nm = (c.get("name") or "").lower()
            if "apple tv" in nm or "plex for apple tv" in prod:
                target = c
                break
    if target is None:
        target = clients[0]

    logging.info(f"Plex: using client name={target.get('name')!r} product={target.get('product')!r} machineIdentifier={target.get('machineIdentifier')!r}")

    hit = _plex_search_best_title(plex_url=plex_url, plex_token=plex_token, query=title)
    if not hit:
        logging.error(f"Plex: no search results for {title!r}")
        return f"I couldn't find {title} in Plex."

    kind = hit.get('kind')
    rating_key = hit.get('ratingKey')
    pretty = hit.get('pretty') or title
    if not rating_key:
        return None

    # If it's a show, pick an episode to play (Patch #1: first episode; Patch #2: next episode/on deck)
    if kind == 'show':
        ep = _plex_pick_show_episode(
            plex_url=plex_url,
            plex_token=plex_token,
            show_rating_key=rating_key,
            show_title=pretty,
        )
        if not ep:
            return f"I found {pretty}, but couldn't pick an episode to play."
        rating_key, pretty = ep

    # ------------------------------------------------------------
    # TEST MODE GUARD — do not play media when testing
    # ------------------------------------------------------------
    if not _is_live_mode():
        logging.info(
            f"Plex TEST MODE: would play title={pretty!r} ratingKey={rating_key} — skipping playback"
        )
        return ""
    _vo = _plex_get_view_offset_ms(plex_url=plex_url, plex_token=plex_token, rating_key=rating_key)
    logging.info(f"Plex resume viewOffset_ms={_vo} ratingKey={rating_key} title={pretty}")
    ok = _plex_play_on_client(
        plex_url=plex_url,
        plex_token=plex_token,
        client_machine_id=target["machineIdentifier"],
        client_address=target["host"],
        client_port=target["port"],
        rating_key=rating_key,
        offset_ms=_vo or 0,
    )
    if not ok:
        return f"I found {pretty}, but Plex didn't start playback."

    if mark_action_occurred:
        try:
            mark_action_occurred()
        except Exception:
            logging.exception("Plex collection ordinal resolver failed")

    logging.info(f"CLAIM: plex_play title={pretty!r} ratingKey={rating_key}")
    # Gated by SPEAK_MEDIA_CONFIRMATIONS (dispatcher passes _maybe_say_media).
    # mark_action_occurred already fired above, so with media confirmations off
    # this is a silent success (tone), not an error.
    return maybe_say(f"Playing {pretty}.") or ""
