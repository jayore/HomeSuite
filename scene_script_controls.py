import logging
from typing import Dict, Optional


_runnable_cache: Dict[str, str] = {}
_runnable_cache_ts: float = 0.0


def refresh_runnable_cache(
    *,
    ha_get_states,
    normalize_scene_phrase,
    ttl_seconds: int,
    force: bool = False,
) -> None:
    global _runnable_cache, _runnable_cache_ts

    import time

    now = time.time()
    if not force and _runnable_cache and (now - _runnable_cache_ts) < ttl_seconds:
        return

    states = ha_get_states()
    if not states:
        _runnable_cache = {}
        _runnable_cache_ts = now
        logging.info("Refreshed runnables: 0 discovered")
        return

    new_cache: Dict[str, str] = {}
    count = 0
    for st in states:
        eid = st.get("entity_id", "")
        if not isinstance(eid, str):
            continue
        if not (eid.startswith("scene.") or eid.startswith("script.")):
            continue

        attrs = st.get("attributes", {}) or {}
        name = attrs.get("friendly_name") or eid.split(".", 1)[1]
        if not isinstance(name, str):
            continue

        key = normalize_scene_phrase(name)
        if key:
            new_cache[key] = eid
            count += 1

        eid_key = normalize_scene_phrase(eid.split(".", 1)[1].replace("_", " "))
        if eid_key and eid_key not in new_cache:
            new_cache[eid_key] = eid

    # -----------------------------
    # User-defined trigger aliases (scenes/scripts)
    # -----------------------------
    try:
        from app_config import HA_TRIGGER_ALIASES
    except Exception:
        HA_TRIGGER_ALIASES = {}

    try:
        for ent_id, phrases in (HA_TRIGGER_ALIASES or {}).items():
            if not isinstance(ent_id, str) or not (ent_id.startswith("scene.") or ent_id.startswith("script.")):
                continue
            if not isinstance(phrases, (list, tuple, set)):
                continue
            for phr in phrases:
                if not isinstance(phr, str):
                    continue
                k = normalize_scene_phrase(phr)
                if not k:
                    continue
                prev = new_cache.get(k)
                if prev and prev != ent_id:
                    logging.debug(f"Trigger alias override: {k!r} {prev} -> {ent_id}")
                new_cache[k] = ent_id
    except Exception as e:
        logging.error(f"Trigger alias ingest error: {e}")

    _runnable_cache = new_cache
    _runnable_cache_ts = now
    logging.info(f"Refreshed runnables: {count} discovered")


def try_run_runnable_from_text(
    text: str,
    *,
    ha_get_states,
    normalize_scene_phrase,
    call_ha_service,
    speak_action_confirmations: bool,
    ttl_seconds: int,
    refresh_cache: bool = True,
) -> Optional[str]:
    """
    If the user says something that matches a known scene or script phrase, run it.
    Returns a string to speak (or "" if confirmations are disabled), or None if no match.
    """
    if refresh_cache:
        refresh_runnable_cache(
            ha_get_states=ha_get_states,
            normalize_scene_phrase=normalize_scene_phrase,
            ttl_seconds=ttl_seconds,
            force=False,
        )
    else:
        import time

        if not _runnable_cache or (time.time() - _runnable_cache_ts) >= ttl_seconds:
            return None

    spoken = normalize_scene_phrase(text)
    if not spoken:
        return None

    target = _runnable_cache.get(spoken)
    if target:
        if target.startswith("scene."):
            ok = call_ha_service("scene/turn_on", {"entity_id": target})
        else:
            ok = call_ha_service("script/turn_on", {"entity_id": target})
        if ok:
            return spoken.title() if speak_action_confirmations else ""

    import re
    spoken2 = re.sub(r"^(set|make|do)\s+", "", spoken).strip()
    target = _runnable_cache.get(spoken2)
    if target:
        if target.startswith("scene."):
            ok = call_ha_service("scene/turn_on", {"entity_id": target})
        else:
            ok = call_ha_service("script/turn_on", {"entity_id": target})
        if ok:
            return spoken2.title() if speak_action_confirmations else ""

    return None


def get_runnable_cache_size() -> int:
    return len(_runnable_cache)
