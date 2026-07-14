"""Source-scoped structured continuity for deterministic and AI interactions.

HomeSuite uses a single referent registry for short follow-ups, but it does not
give the word ``it`` one global meaning. Producers remember typed objects with
stable keys and capabilities; handlers resolve only objects compatible with the
intent they already recognized, then revalidate the key against live state.

State is divided into context bubbles. Sources may opt into a shared
``continuity_group`` in ``app_config.SOURCES``; otherwise a source gets its own
bubble. The registry is process-local and bounded by per-entry TTLs. It is not
an execution engine and never turns model-generated prose into an action.
"""

from __future__ import annotations

import copy
import logging
import re
import threading
import time
from typing import TYPE_CHECKING, Any, Iterable, Optional

from home_registry import get_source
from request_context import get_current_request_context


_LOCK = threading.RLock()
_REFERENTS_BY_SCOPE: dict[str, dict[str, dict[str, Any]]] = {}
_PROCESS_SCOPE = "process"
_INTENT_FRAME_KIND = "intent_frame"

if TYPE_CHECKING:
    from conversational_nl import IntentFrame


def _clean_token(value: Any, fallback: str = "") -> str:
    cleaned = re.sub(r"[^a-z0-9_.:-]+", "_", str(value or "").strip().lower())
    return cleaned.strip("_") or fallback


def current_scope_id() -> str:
    """Return the configured continuity bubble for the active request."""
    ctx = get_current_request_context()
    source_id = str(getattr(ctx, "source_id", None) or "").strip()
    if not source_id:
        return _PROCESS_SCOPE

    source = get_source(source_id) or {}
    group = source.get("continuity_group") or source.get("device_group")
    if group:
        return f"group:{_clean_token(group, 'default')}"
    return f"source:{_clean_token(source_id, 'unknown')}"


def _default_ttl_seconds() -> float:
    try:
        import app_config

        return max(1.0, float(getattr(app_config, "DIALOGUE_REFERENT_TTL_SECONDS", 120)))
    except Exception:
        return 120.0


def _intent_ttl_seconds() -> float:
    try:
        import app_config

        return max(
            1.0,
            float(
                getattr(
                    app_config,
                    "DIALOGUE_INTENT_FRAME_TTL_SECONDS",
                    getattr(app_config, "DIALOGUE_REFERENT_TTL_SECONDS", 120),
                )
            ),
        )
    except Exception:
        return 120.0


def _scope(value: Optional[str]) -> str:
    return str(value or current_scope_id()).strip() or _PROCESS_SCOPE


def _copy_entry(entry: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    return copy.deepcopy(entry) if entry else None


def _purge_expired_locked(scope_id: str, now_ts: float) -> None:
    refs = _REFERENTS_BY_SCOPE.get(scope_id)
    if not refs:
        return
    for kind, entry in list(refs.items()):
        try:
            expired = now_ts - float(entry.get("ts") or 0.0) > float(entry.get("ttl_seconds") or 0.0)
        except (TypeError, ValueError):
            expired = True
        if expired:
            logging.info(
                "DIALOGUE_REFERENT_EXPIRED scope=%s kind=%s key=%s",
                scope_id,
                kind,
                entry.get("key"),
            )
            refs.pop(kind, None)
    if not refs:
        _REFERENTS_BY_SCOPE.pop(scope_id, None)


def remember_referent(
    kind: str,
    key: str,
    *,
    label: str = "",
    capabilities: Iterable[str] = (),
    data: Optional[dict[str, Any]] = None,
    confidence: float = 1.0,
    ttl_seconds: Optional[float] = None,
    scope_id: Optional[str] = None,
    source: str = "deterministic",
) -> Optional[dict[str, Any]]:
    """Remember the latest referent of one type in a context bubble."""
    kind_n = _clean_token(kind)
    key_n = str(key or "").strip()
    if not kind_n or not key_n:
        return None

    scope_n = _scope(scope_id)
    ttl = _default_ttl_seconds() if ttl_seconds is None else max(1.0, float(ttl_seconds))
    now_ts = time.time()
    entry = {
        "scope_id": scope_n,
        "kind": kind_n,
        "key": key_n,
        "label": str(label or "").strip(),
        "capabilities": sorted({_clean_token(value) for value in capabilities if _clean_token(value)}),
        "data": copy.deepcopy(data or {}),
        "confidence": max(0.0, min(1.0, float(confidence))),
        "source": str(source or "").strip() or "deterministic",
        "ts": now_ts,
        "ttl_seconds": ttl,
    }
    with _LOCK:
        _purge_expired_locked(scope_n, now_ts)
        _REFERENTS_BY_SCOPE.setdefault(scope_n, {})[kind_n] = entry
    logging.info(
        "DIALOGUE_REFERENT_REMEMBER scope=%s kind=%s key=%s capabilities=%s",
        scope_n,
        kind_n,
        key_n,
        entry["capabilities"],
    )
    return _copy_entry(entry)


def resolve_referent(
    *,
    kinds: Optional[Iterable[str]] = None,
    capability: Optional[str] = None,
    max_age_seconds: Optional[float] = None,
    scope_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Return the newest compatible referent in the current context bubble."""
    scope_n = _scope(scope_id)
    kinds_n = {_clean_token(value) for value in kinds or () if _clean_token(value)}
    capability_n = _clean_token(capability)
    now_ts = time.time()
    with _LOCK:
        _purge_expired_locked(scope_n, now_ts)
        candidates = list((_REFERENTS_BY_SCOPE.get(scope_n) or {}).values())

    if kinds_n:
        candidates = [entry for entry in candidates if entry.get("kind") in kinds_n]
    if capability_n:
        candidates = [
            entry for entry in candidates
            if capability_n in set(entry.get("capabilities") or ())
        ]
    if max_age_seconds is not None:
        max_age = max(0.0, float(max_age_seconds))
        candidates = [
            entry for entry in candidates
            if now_ts - float(entry.get("ts") or 0.0) <= max_age
        ]
    if not candidates:
        return None
    candidates.sort(key=lambda entry: float(entry.get("ts") or 0.0), reverse=True)
    return _copy_entry(candidates[0])


def remember_intent_frame(
    frame: "IntentFrame",
    *,
    scope_id: Optional[str] = None,
    source: str = "deterministic",
) -> Optional[dict[str, Any]]:
    """Remember one typed successful-turn frame in the current context bubble."""
    try:
        data = frame.to_data()
        domain = str(frame.domain or "").strip().lower()
        intent = str(frame.intent or "").strip().lower()
        followups = set(frame.followups or ())
    except Exception:
        logging.exception("DIALOGUE_INTENT_FRAME_INVALID frame=%r", frame)
        return None
    if not domain or not intent:
        return None
    return remember_referent(
        _INTENT_FRAME_KIND,
        f"{domain}:{intent}",
        label=str(frame.canonical_command or "").strip(),
        capabilities={"intent_followup", *followups},
        data=data,
        ttl_seconds=_intent_ttl_seconds(),
        scope_id=scope_id,
        source=source,
    )


def resolve_intent_frame(
    *,
    required_followup: Optional[str] = None,
    scope_id: Optional[str] = None,
) -> Optional["IntentFrame"]:
    """Return the active typed intent frame, if it supports the requested turn."""
    entry = resolve_referent(
        kinds={_INTENT_FRAME_KIND},
        capability=required_followup or "intent_followup",
        scope_id=scope_id,
    )
    if not entry:
        return None
    try:
        from conversational_nl import IntentFrame

        return IntentFrame.from_data(entry.get("data") or {})
    except Exception:
        logging.exception(
            "DIALOGUE_INTENT_FRAME_DECODE_FAIL scope=%s key=%s",
            entry.get("scope_id"),
            entry.get("key"),
        )
        forget_referent(
            _INTENT_FRAME_KIND,
            key=str(entry.get("key") or ""),
            scope_id=scope_id,
        )
        return None


def forget_intent_frame(*, scope_id: Optional[str] = None) -> None:
    entry = resolve_referent(kinds={_INTENT_FRAME_KIND}, scope_id=scope_id)
    if entry:
        forget_referent(
            _INTENT_FRAME_KIND,
            key=str(entry.get("key") or ""),
            scope_id=scope_id,
        )


def forget_referent(
    kind: str,
    *,
    key: Optional[str] = None,
    scope_id: Optional[str] = None,
) -> None:
    scope_n = _scope(scope_id)
    kind_n = _clean_token(kind)
    with _LOCK:
        refs = _REFERENTS_BY_SCOPE.get(scope_n)
        entry = refs.get(kind_n) if refs else None
        if not entry or (key is not None and str(entry.get("key")) != str(key)):
            return
        refs.pop(kind_n, None)
        if not refs:
            _REFERENTS_BY_SCOPE.pop(scope_n, None)
    logging.info(
        "DIALOGUE_REFERENT_FORGET scope=%s kind=%s key=%s",
        scope_n,
        kind_n,
        entry.get("key"),
    )


def forget_referents(
    *,
    capability: Optional[str] = None,
    scope_id: Optional[str] = None,
) -> int:
    """Forget every referent matching a capability in one context bubble."""
    scope_n = _scope(scope_id)
    capability_n = _clean_token(capability)
    removed = []
    with _LOCK:
        _purge_expired_locked(scope_n, time.time())
        refs = _REFERENTS_BY_SCOPE.get(scope_n)
        if not refs:
            return 0
        for kind, entry in list(refs.items()):
            if capability_n and capability_n not in set(entry.get("capabilities") or ()):
                continue
            removed.append((kind, entry.get("key")))
            refs.pop(kind, None)
        if not refs:
            _REFERENTS_BY_SCOPE.pop(scope_n, None)

    for kind, key in removed:
        logging.info(
            "DIALOGUE_REFERENT_FORGET scope=%s kind=%s key=%s capability=%s",
            scope_n,
            kind,
            key,
            capability_n or "*",
        )
    return len(removed)


def snapshot_scope(scope_id: Optional[str] = None) -> dict[str, dict[str, Any]]:
    scope_n = _scope(scope_id)
    with _LOCK:
        _purge_expired_locked(scope_n, time.time())
        return copy.deepcopy(_REFERENTS_BY_SCOPE.get(scope_n) or {})


def restore_scope(snapshot: dict[str, dict[str, Any]], scope_id: Optional[str] = None) -> None:
    scope_n = _scope(scope_id)
    with _LOCK:
        if snapshot:
            _REFERENTS_BY_SCOPE[scope_n] = copy.deepcopy(snapshot)
        else:
            _REFERENTS_BY_SCOPE.pop(scope_n, None)


def reset_dialogue_state(*, scope_id: Optional[str] = None, all_scopes: bool = False) -> None:
    with _LOCK:
        if all_scopes:
            _REFERENTS_BY_SCOPE.clear()
        else:
            _REFERENTS_BY_SCOPE.pop(_scope(scope_id), None)


def snapshot_all() -> dict[str, dict[str, dict[str, Any]]]:
    with _LOCK:
        for scope_id in list(_REFERENTS_BY_SCOPE):
            _purge_expired_locked(scope_id, time.time())
        return copy.deepcopy(_REFERENTS_BY_SCOPE)
