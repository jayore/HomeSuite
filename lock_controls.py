import logging
import re
from typing import Optional


def _norm_target(raw: str) -> str:
    t = (raw or "").strip().lower()
    t = re.sub(r"[^a-z0-9\s]+", " ", t)
    t = re.sub(r"\b(the|a|an)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _remember_entity_safely(remember_entity, entity_id: str, domain: str) -> None:
    if remember_entity is None:
        return
    try:
        remember_entity(entity_id, domain)
    except Exception:
        logging.exception(
            "LOCK_REFERENT_REMEMBER_FAIL entity_id=%r domain=%r",
            entity_id,
            domain,
        )


def handle_lock_controls(
    *,
    tl: str,
    call_ha_service,
    maybe_say,
    resolve_device_entity,
    remember_entity=None,
) -> Optional[str]:
    """
    Handles:
      - "lock <thing>"
      - "unlock <thing>"

    Notes:
      - Uses resolve_device_entity() so HA_DEVICE_ALIASES applies automatically.
      - Only acts if the resolved domain is "lock".
      - Calls HA services: lock/lock and lock/unlock.
    """
    t = (tl or "").strip().lower()
    if not t:
        return None

    # Prefer matching "unlock" before "lock" to avoid "unlock ..." being caught by "lock ..."
    m_unlock = re.search(r"\bunlock\b(?:\s+(?:the\s+)?)?(.+)$", t)
    if m_unlock:
        raw = (m_unlock.group(1) or "").strip()
        raw = _norm_target(raw)
        if not raw:
            return None

        resolved = resolve_device_entity(raw)
        if not resolved:
            return None

        eid, domain = resolved
        if domain != "lock":
            return None

        ok = call_ha_service("lock/unlock", {"entity_id": eid})
        if ok:
            _remember_entity_safely(remember_entity, eid, domain)
        return maybe_say(f"Unlocking {raw}.") if ok else None

    m_lock = re.search(r"\block\b(?:\s+(?:the\s+)?)?(.+)$", t)
    if m_lock:
        raw = (m_lock.group(1) or "").strip()
        raw = _norm_target(raw)
        if not raw:
            return None

        resolved = resolve_device_entity(raw)
        if not resolved:
            return None

        eid, domain = resolved
        if domain != "lock":
            return None

        ok = call_ha_service("lock/lock", {"entity_id": eid})
        if ok:
            _remember_entity_safely(remember_entity, eid, domain)
        return maybe_say(f"Locking {raw}.") if ok else None

    return None
