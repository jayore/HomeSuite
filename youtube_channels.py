"""youtube_channels.py — registry of YouTube channels PiPhone knows about.

Each channel maps a friendly name (and aliases/handle) to a YouTube channel id
(UC...). Channels flagged `in_digest` make up the daily reel. The registry is a
JSON file under state/ (mutable: added/edited at runtime), optionally seeded with
curated defaults from app_config.YOUTUBE_CHANNELS.

A later OAuth subscription sync (Phase 2) can upsert entries here automatically;
the data model already supports that (in_digest defaults off for synced subs).

Entry shape:
    "<channel_id>": {
        "title":    "Channel Title",
        "handle":   "@handle" | None,
        "aliases":  ["short name", ...],
        "in_digest": bool,
        "groups":   ["news", ...],
    }
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Dict, List, Optional

log = logging.getLogger("youtube_channels")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE_DIR = os.path.join(_BASE_DIR, "state")
_DB_PATH = os.path.join(_STATE_DIR, "youtube_channels.json")

_lock = threading.Lock()
_cache: Optional[Dict[str, dict]] = None

_CHANNEL_ID_RE = re.compile(r"UC[0-9A-Za-z_-]{22}")


# ---------------------------------------------------------------------------
# Persistence (atomic, defensive)
# ---------------------------------------------------------------------------

def _seed_from_prefs() -> Dict[str, dict]:
    try:
        from app_config import YOUTUBE_CHANNELS  # optional
        if isinstance(YOUTUBE_CHANNELS, dict):
            return {str(k): dict(v) for k, v in YOUTUBE_CHANNELS.items() if isinstance(v, dict)}
    except Exception:
        pass
    return {}


def _str_list(v) -> list:
    return [str(x).strip().lower() for x in (v or []) if str(x).strip()]


def _normalize_entry(e: dict) -> dict:
    try:
        min_dur = int(e["min_duration_sec"]) if e.get("min_duration_sec") not in (None, "") else None
    except Exception:
        min_dur = None
    try:
        max_pc = max(1, int(e.get("max_per_channel") or 1))
    except Exception:
        max_pc = 1
    return {
        "title": str(e.get("title") or "").strip(),
        "handle": (str(e.get("handle")).strip() or None) if e.get("handle") else None,
        "aliases": [str(a).strip() for a in (e.get("aliases") or []) if str(a).strip()],
        "in_digest": bool(e.get("in_digest")),
        "groups": _str_list(e.get("groups")),
        # Digest filters (all optional):
        "include_title": _str_list(e.get("include_title")),       # if set, title must match ANY
        "exclude_title": _str_list(e.get("exclude_title")),       # title must match NONE
        "exclude_description": _str_list(e.get("exclude_description")),
        "min_duration_sec": min_dur,                              # needs duration source
        "max_per_channel": max_pc,
    }


def _load_locked() -> Dict[str, dict]:
    global _cache
    if _cache is not None:
        return _cache
    merged: Dict[str, dict] = {cid: _normalize_entry(e) for cid, e in _seed_from_prefs().items()}
    try:
        with open(_DB_PATH, "r") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for cid, e in raw.items():
                if isinstance(e, dict):
                    merged[str(cid)] = _normalize_entry(e)
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("youtube_channels: load failed: %s", e)
    _cache = merged
    return _cache


def _save_locked(data: Dict[str, dict]) -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        tmp = _DB_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _DB_PATH)
    except Exception as e:
        log.warning("youtube_channels: save failed: %s", e)


def _invalidate() -> None:
    global _cache
    _cache = None


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("@", " ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def get_all() -> Dict[str, dict]:
    with _lock:
        return dict(_load_locked())


def resolve_channel(name: str) -> Optional[str]:
    """Resolve a spoken/typed channel name to a channel id, or None."""
    q = _norm(name)
    if not q:
        return None
    if _CHANNEL_ID_RE.fullmatch((name or "").strip()):
        return name.strip()
    with _lock:
        reg = _load_locked()
    # 1) exact match on title / handle / alias
    for cid, e in reg.items():
        names = [e.get("title", ""), (e.get("handle") or "")] + (e.get("aliases") or [])
        if any(_norm(n) == q for n in names if n):
            return cid
    # 2) substring (longest title wins to avoid greedy partials)
    cands = []
    for cid, e in reg.items():
        names = [e.get("title", ""), (e.get("handle") or "")] + (e.get("aliases") or [])
        for n in names:
            nn = _norm(n)
            if nn and (q in nn or nn in q):
                cands.append((len(nn), cid))
                break
    if cands:
        cands.sort(reverse=True)
        return cands[0][1]
    return None


def channel_title(channel_id: str) -> Optional[str]:
    with _lock:
        e = _load_locked().get(channel_id)
    return (e or {}).get("title") or None


def get_channel(channel_id: str) -> Optional[dict]:
    """Return the normalized registry entry (incl. filter config) for a channel."""
    with _lock:
        e = _load_locked().get(channel_id)
    return dict(e) if e else None


def digest_channels() -> List[str]:
    """Channel ids flagged in_digest, in registry order."""
    with _lock:
        return [cid for cid, e in _load_locked().items() if e.get("in_digest")]


def _gnorm(s: str) -> str:
    """Group-name normalization: like _norm but space-insensitive, so a spoken
    'late night' matches a stored 'latenight'."""
    return _norm(s).replace(" ", "")


def list_groups() -> List[str]:
    """All distinct group keys present in the registry, in first-seen order."""
    seen: List[str] = []
    with _lock:
        for e in _load_locked().values():
            for g in (e.get("groups") or []):
                if g and g not in seen:
                    seen.append(g)
    return seen


def resolve_group(name: str) -> Optional[str]:
    """Map a spoken/typed group name to a stored group key, or None."""
    gq = _gnorm(name)
    if not gq:
        return None
    for g in list_groups():
        if _gnorm(g) == gq:
            return g
    return None


def channels_in_group(group: str) -> List[str]:
    """Channel ids tagged with `group` (space-insensitive), in registry order.
    Independent of in_digest — group membership alone qualifies a channel."""
    target = _gnorm(group)
    if not target:
        return []
    with _lock:
        return [cid for cid, e in _load_locked().items()
                if any(_gnorm(g) == target for g in (e.get("groups") or []))]


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

def upsert_channel(channel_id: str, *, title: str = "", handle: Optional[str] = None,
                   aliases: Optional[List[str]] = None, in_digest: Optional[bool] = None,
                   groups: Optional[List[str]] = None,
                   include_title: Optional[List[str]] = None,
                   exclude_title: Optional[List[str]] = None,
                   exclude_description: Optional[List[str]] = None,
                   min_duration_sec: Optional[int] = None,
                   max_per_channel: Optional[int] = None) -> bool:
    cid = (channel_id or "").strip()
    if not _CHANNEL_ID_RE.fullmatch(cid):
        return False
    _updates = {
        "title": title or None,
        "handle": handle,
        "aliases": aliases,
        "in_digest": in_digest,
        "groups": groups,
        "include_title": include_title,
        "exclude_title": exclude_title,
        "exclude_description": exclude_description,
        "min_duration_sec": min_duration_sec,
        "max_per_channel": max_per_channel,
    }
    with _lock:
        data = dict(_load_locked())
        cur = dict(data.get(cid) or {})
        for k, v in _updates.items():
            if v is not None:
                cur[k] = v
        data[cid] = _normalize_entry(cur)
        _save_locked(data)
        _invalidate()
    return True


def set_in_digest(channel_id: str, value: bool) -> bool:
    if channel_id not in get_all():
        return False
    return upsert_channel(channel_id, in_digest=bool(value))


def add_to_group(channel_id: str, group: str) -> bool:
    """Tag a channel with a group (idempotent, space-insensitive). Stores the
    canonical space-stripped key so it matches the seed style ('latenight')."""
    if channel_id not in get_all():
        return False
    g = _gnorm(group)
    if not g:
        return False
    cur = (get_channel(channel_id) or {}).get("groups") or []
    if any(_gnorm(x) == g for x in cur):
        return True
    return upsert_channel(channel_id, groups=list(cur) + [g])


def remove_from_group(channel_id: str, group: str) -> bool:
    if channel_id not in get_all():
        return False
    g = _gnorm(group)
    cur = (get_channel(channel_id) or {}).get("groups") or []
    new = [x for x in cur if _gnorm(x) != g]
    if len(new) == len(cur):
        return True  # wasn't tagged; nothing to do
    return upsert_channel(channel_id, groups=new)


def remove_channel(channel_id: str) -> bool:
    with _lock:
        data = dict(_load_locked())
        if channel_id not in data:
            return False
        data.pop(channel_id, None)
        _save_locked(data)
        _invalidate()
    return True


# ---------------------------------------------------------------------------
# Handle / URL -> channel id (one-time add helper; scrapes the channel page)
# ---------------------------------------------------------------------------

def resolve_handle_to_id(handle_or_url: str) -> Optional[str]:
    """Best-effort: turn an @handle or channel URL into a UC... channel id."""
    s = (handle_or_url or "").strip()
    if not s:
        return None
    if _CHANNEL_ID_RE.fullmatch(s):
        return s
    if s.startswith("http"):
        url = s
    elif s.startswith("@"):
        url = f"https://www.youtube.com/{s}"
    else:
        url = f"https://www.youtube.com/@{s}"
    try:
        import requests
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        html = resp.text
        # Prefer the page's OWN canonical id. "channelId" appears many times on a
        # channel page (recommended channels, video owners), so it is the last
        # resort — the canonical link / og:url / externalId identify *this* channel.
        for pat in (
            r'<link[^>]+rel="canonical"[^>]+href="https://www\.youtube\.com/channel/(UC[0-9A-Za-z_-]{22})"',
            r'<meta[^>]+property="og:url"[^>]+content="https://www\.youtube\.com/channel/(UC[0-9A-Za-z_-]{22})"',
            r'"externalId"\s*:\s*"(UC[0-9A-Za-z_-]{22})"',
            r'channel/(UC[0-9A-Za-z_-]{22})',
        ):
            m = re.search(pat, html)
            if m:
                return m.group(1)
        return None
    except Exception as e:
        log.warning("youtube_channels: handle resolve failed for %r: %s", s, e)
        return None
