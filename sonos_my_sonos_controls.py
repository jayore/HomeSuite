import re
import time
import logging
from typing import Optional, Dict, Any, List, Tuple

# Simple normalization (keep consistent with existing Sonos Spotify module)
def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _extract_play_query(tl: str) -> Optional[str]:
    """
    Extract a My Sonos / Sonos Favorites query.

    Important routing rule:
    By default, this handler is opt-in only. It should not claim generic
    music requests like "play Death Cab for Cutie", because those should reach
    the Spotify/Sonos music resolver.

    Explicit examples this handler WILL accept by default:
      - play favorite KCLU
      - play favorites KCLU
      - play sonos favorite KCLU
      - play my sonos KCLU

    Generic behavior can be restored with:
      SONOS_MY_SONOS_GENERIC_PLAY_ENABLED = True
    """
    t = (tl or "").strip().lower()
    if not t:
        return None

    m = re.match(r"^(play|put on|listen to|start)\s+(.+)$", t)
    if not m:
        return None

    q_raw = (m.group(2) or "").strip()
    q_raw = re.sub(r"\b(?:on|in)\s+[a-z0-9 _-]+\s*$", "", q_raw).strip()
    q_raw = re.sub(r"\s+", " ", q_raw).strip()
    if not q_raw:
        return None

    # Never steal explicit typed music requests. These belong to the Spotify
    # music resolver:
    #   play artist X
    #   play album X
    #   play track X
    #   play playlist X
    q_type = re.sub(r"^(the|my)\s+", "", q_raw).strip()
    if re.match(r"^(artist|band|album|track|song|playlist)\s+.+$", q_type):
        logging.info("MySonos: declining explicit typed music request query=%r", q_raw)
        return None

    # Explicit My Sonos / Favorites forms.
    explicit_patterns = [
        r"^(?:sonos\s+)?favorites?\s+(.+)$",
        r"^my\s+sonos\s+(.+)$",
        r"^from\s+my\s+sonos\s+(.+)$",
    ]

    for pat in explicit_patterns:
        mm = re.match(pat, q_raw)
        if mm:
            qq = (mm.group(1) or "").strip()
            qq = re.sub(r"^(the|my)\s+", "", qq).strip()
            if qq:
                logging.info("MySonos: accepting explicit favorite query=%r", qq)
                return qq

    try:
        from app_config import SONOS_MY_SONOS_GENERIC_PLAY_ENABLED
    except Exception:
        SONOS_MY_SONOS_GENERIC_PLAY_ENABLED = False

    if not bool(SONOS_MY_SONOS_GENERIC_PLAY_ENABLED):
        logging.info("MySonos: generic play disabled; declining query=%r", q_raw)
        return None

    q = re.sub(r"^(the|my)\s+", "", q_raw).strip()
    logging.info("MySonos: generic play enabled; accepting query=%r", q)
    return q or None

def _pick_best(children: List[Dict[str, Any]], query: str) -> Optional[Dict[str, Any]]:
    qn = _norm(query)
    if not qn:
        return None

    # exact
    for c in children:
        if not isinstance(c, dict):
            continue
        title = c.get("title")
        if isinstance(title, str) and _norm(title) == qn:
            return c

    # contains / substring
    best = None
    best_len = 10**9
    for c in children:
        if not isinstance(c, dict):
            continue
        title = c.get("title") or ""
        tn = _norm(title)
        if not tn:
            continue
        if qn in tn or tn in qn:
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
            logging.error("MySonos browse_media failed %s: %s", r.status_code, (r.text or "")[:200])
            return None
        data = r.json() or {}
        sr = data.get("service_response") or {}
        ent = sr.get(entity_id)
        if not isinstance(ent, dict):
            return None
        return ent
    except Exception as e:
        logging.error("MySonos browse_media exception: %s", e)
        return None

# Cache: (sonos_entity_id) -> (ts, items)
# items are dicts with title/media_content_type/media_content_id
_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_CACHE_TTL_S = 600.0

def _looks_like_mysonos_node(node: Dict[str, Any]) -> bool:
    # Title-based hints
    title = _norm(node.get("title") or "")
    if title in ("my sonos", "sonos favorites", "favorites", "favourites"):
        return True
    if "favorite" in title or "favourite" in title:
        return True
    # media_content_type hints (integration-dependent)
    mct = node.get("media_content_type")
    if isinstance(mct, str):
        mctn = mct.lower()
        if "favorite" in mctn or "favourite" in mctn or "my_sonos" in mctn or "my sonos" in mctn:
            return True
    return False

def _flatten_playable_items(tree: Dict[str, Any], *, max_depth: int = 2) -> List[Dict[str, Any]]:
    # Collect playable children. If a child is a container, we may expand 1–2 levels.
    out: List[Dict[str, Any]] = []

    def walk(node: Dict[str, Any], depth: int):
        if depth < 0 or not isinstance(node, dict):
            return
        kids = node.get("children") or []
        if not isinstance(kids, list):
            return

        for c in kids:
            if not isinstance(c, dict):
                continue
            title = c.get("title")
            mct = c.get("media_content_type")
            mcid = c.get("media_content_id")

            # If it looks playable, keep it
            if isinstance(mct, str) and isinstance(mcid, str) and (title is None or isinstance(title, str)):
                out.append(c)

            # Expand containers too (some favorites are nested)
            # We only expand if we can browse it: requires both mct+mcid.
            if depth > 0 and isinstance(mct, str) and isinstance(mcid, str):
                # Heuristic: containers usually have children_count or lack "can_play"
                # but HA schemas vary; browsing is safest.
                yield ("expand", c, depth - 1)

    # We implement expansion iteratively to avoid deep recursion and to reuse browse calls outside
    # (actual browsing is done in _get_mysonos_items()).
    # Here, we just return immediate-playable + placeholders; expansion is handled elsewhere.
    return out

def _get_mysonos_items(
    *,
    ha_session,
    ha_url: str,
    headers: dict,
    sonos_entity_id: str,
) -> List[Dict[str, Any]]:
    now = time.time()
    cached = _CACHE.get(sonos_entity_id)
    if cached and (now - cached[0]) < _CACHE_TTL_S:
        return cached[1]

    root = _browse(ha_session=ha_session, ha_url=ha_url, headers=headers, entity_id=sonos_entity_id)
    if not root:
        _CACHE[sonos_entity_id] = (now, [])
        return []

    # Find the My Sonos / Favorites node at root (fast, no guessing deep traversal)
    fav_node = None
    for c in (root.get("children") or []):
        if not isinstance(c, dict):
            continue
        if _looks_like_mysonos_node(c):
            fav_node = c
            break

    if not fav_node:
        # No favorites node exposed -> nothing to do (avoid slow broad traversal)
        _CACHE[sonos_entity_id] = (now, [])
        return []

    # Browse favorites node
    fav_tree = _browse(
        ha_session=ha_session,
        ha_url=ha_url,
        headers=headers,
        entity_id=sonos_entity_id,
        media_content_type=fav_node.get("media_content_type"),
        media_content_id=fav_node.get("media_content_id"),
    )
    if not fav_tree:
        _CACHE[sonos_entity_id] = (now, [])
        return []

    items: List[Dict[str, Any]] = []
    # Level 1 items
    kids = fav_tree.get("children") or []
    if isinstance(kids, list):
        for c in kids:
            if isinstance(c, dict):
                items.append(c)

    # Optional: one extra expansion level for container-like nodes
    expanded: List[Dict[str, Any]] = []
    for c in list(items):
        mct = c.get("media_content_type")
        mcid = c.get("media_content_id")
        # Expand only if it looks like a container (has children_count or title "Line-In" etc.)
        if isinstance(mct, str) and isinstance(mcid, str):
            try:
                sub = _browse(
                    ha_session=ha_session,
                    ha_url=ha_url,
                    headers=headers,
                    entity_id=sonos_entity_id,
                    media_content_type=mct,
                    media_content_id=mcid,
                    timeout_s=12,
                )
                sk = (sub or {}).get("children") or []
                if isinstance(sk, list) and sk:
                    for cc in sk:
                        if isinstance(cc, dict):
                            expanded.append(cc)
            except Exception:
                pass

    # Combine; filter to playable-looking entries (must have mct+mcid)
    all_items = items + expanded
    playable: List[Dict[str, Any]] = []
    for c in all_items:
        if not isinstance(c, dict):
            continue
        mct = c.get("media_content_type")
        mcid = c.get("media_content_id")
        if isinstance(mct, str) and isinstance(mcid, str):
            playable.append(c)

    _CACHE[sonos_entity_id] = (now, playable)
    return playable

def handle_sonos_my_sonos_controls(
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
    Resolve "play X" against Sonos 'My Sonos' / 'Favorites' items exposed via HA browse_media.

    Returns:
        None -> not handled or no match
        ""/str -> handled (silent or spoken)
    """
    query = _extract_play_query(tl)
    if not query:
        return None

    items = _get_mysonos_items(
        ha_session=ha_session,
        ha_url=ha_url,
        headers=ha_headers,
        sonos_entity_id=sonos_entity_id,
    )
    if not items:
        return None

    best = _pick_best(items, query)
    if not best:
        return None

    mct = best.get("media_content_type")
    mcid = best.get("media_content_id")
    if not (isinstance(mct, str) and isinstance(mcid, str)):
        return None

    logging.info("MySonos match %r -> title=%r mct=%r mcid=%r", query, best.get("title"), mct, mcid)
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
