"""source_room_state.py — persistent sticky room focus for portable sources.

Portable frontends (menubar app, Raycast, Telegram) can set a sticky room with
an "I'm in the <room>" command. That focus is remembered here, keyed by the
source's room key (its `device_group` when present, else its source id — see
`home_registry.get_source_room_key`), and persists across restarts.

The store is intentionally tiny and defensive: nothing here may raise into the
command path. On any I/O or parse error we fall back to an empty mapping.

File: state/source_rooms.json  ->  { "<room_key>": "<room_id>", ... }
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Dict, Optional

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE_DIR = os.path.join(_BASE_DIR, "state")
_DB_PATH = os.path.join(_STATE_DIR, "source_rooms.json")

_lock = threading.Lock()
_cache: Optional[Dict[str, str]] = None


def _load_locked() -> Dict[str, str]:
    """Load the mapping into the in-memory cache. Caller holds _lock."""
    global _cache
    if _cache is not None:
        return _cache

    data: Dict[str, str] = {}
    try:
        with open(_DB_PATH, "r") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for k, v in raw.items():
                ks = str(k).strip()
                vs = str(v).strip() if v is not None else ""
                if ks and vs:
                    data[ks] = vs
    except FileNotFoundError:
        pass
    except Exception as e:
        logging.warning("source_room_state: failed to load %s: %s", _DB_PATH, e)
        data = {}

    _cache = data
    return _cache


def _save_locked(data: Dict[str, str]) -> None:
    """Atomically persist the mapping. Caller holds _lock."""
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        tmp = _DB_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _DB_PATH)
    except Exception as e:
        logging.warning("source_room_state: failed to save %s: %s", _DB_PATH, e)


def get_current_room(room_key: Optional[str]) -> Optional[str]:
    """Return the remembered room id for this key, or None."""
    key = (str(room_key).strip() if room_key else "")
    if not key:
        return None
    try:
        with _lock:
            return _load_locked().get(key)
    except Exception:
        return None


def set_current_room(room_key: Optional[str], room_id: Optional[str]) -> bool:
    """Persist a sticky room for this key. Returns True on success."""
    key = (str(room_key).strip() if room_key else "")
    rid = (str(room_id).strip() if room_id else "")
    if not key or not rid:
        return False
    try:
        with _lock:
            data = dict(_load_locked())
            data[key] = rid
            _save_locked(data)
            global _cache
            _cache = data
        return True
    except Exception as e:
        logging.warning("source_room_state: set_current_room failed: %s", e)
        return False


def clear_current_room(room_key: Optional[str]) -> bool:
    """Forget the sticky room for this key. Returns True if something was removed."""
    key = (str(room_key).strip() if room_key else "")
    if not key:
        return False
    try:
        with _lock:
            data = dict(_load_locked())
            if key not in data:
                return False
            data.pop(key, None)
            _save_locked(data)
            global _cache
            _cache = data
        return True
    except Exception as e:
        logging.warning("source_room_state: clear_current_room failed: %s", e)
        return False
