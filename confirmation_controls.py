"""Source-scoped approval gates for deterministic commands.

Confirmations are typed dialogue state, not general conversational context.
An exact affirmative or negative reply is intercepted before normal routing;
unrelated speech supersedes the pending request. Command confirmations replay
the original utterance with a short-lived, one-use authorization so the normal
handler resolves and validates live state again before performing any effect.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional

from dialogue_state import (
    forget_referent,
    remember_referent,
    resolve_referent,
)
from request_context import get_current_request_context


_CONFIRMATION_KIND = "command_confirmation"
_CONFIRMATION_CAPABILITY = "confirm_action"
_PENDING_CAPABILITY = "pending_interaction"

_AFFIRMATIVE = {
    "yes",
    "yeah",
    "yep",
    "yes please",
    "sure",
    "confirm",
    "do it",
    "go ahead",
    "please do",
    "okay",
    "ok",
}
_NEGATIVE = {
    "no",
    "nope",
    "no thanks",
    "don't",
    "do not",
    "cancel that",
    "never mind",
    "nevermind",
}
_REVISION_NEGATIVE = {"no", "nope"}

_AUTH_LOCK = threading.RLock()
_AUTHORIZATIONS: Dict[tuple[str, str, str], float] = {}


def _norm(value: str) -> str:
    text = str(value or "").strip().lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9'\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _confirmation_scope_id() -> str:
    """Use an exact request source, never a shared continuity group."""
    ctx = get_current_request_context()
    source_id = str(getattr(ctx, "source_id", None) or "").strip()
    if not source_id:
        return "confirmation:process"
    return f"confirmation:source:{_norm(source_id).replace(' ', '_')}"


def _confirmation_ttl_seconds() -> float:
    try:
        import app_config

        return max(5.0, float(getattr(app_config, "COMMAND_CONFIRMATION_TTL_SECONDS", 45)))
    except Exception:
        return 45.0


def get_confirmation_policy(name: str) -> dict[str, Any]:
    """Return one confirmation policy with deployment overrides applied."""
    try:
        import app_config

        policies = getattr(app_config, "COMMAND_CONFIRMATION_POLICIES", {}) or {}
        overrides = getattr(app_config, "COMMAND_CONFIRMATION_POLICY_OVERRIDES", {}) or {}
    except Exception:
        policies, overrides = {}, {}

    policy = dict(policies.get(name) or {}) if isinstance(policies, dict) else {}
    if isinstance(overrides, dict) and isinstance(overrides.get(name), dict):
        policy.update(overrides[name])
    return policy


def policy_requires_confirmation(name: str, *, value: Optional[float] = None) -> bool:
    policy = get_confirmation_policy(name)
    if not bool(policy.get("enabled", False)):
        return False

    def _configured_values(key: str) -> set[str]:
        raw = policy.get(key) or ()
        if isinstance(raw, str):
            raw = (raw,)
        return {
            str(item or "").strip()
            for item in raw
            if str(item or "").strip()
        }

    ctx = get_current_request_context()
    source_id = str(getattr(ctx, "source_id", None) or "").strip()
    origin = str(getattr(ctx, "origin", None) or "").strip()
    allowed_sources = _configured_values("confirm_source_ids")
    excluded_sources = _configured_values("skip_source_ids")
    allowed_origins = _configured_values("confirm_origins")
    if allowed_sources and source_id not in allowed_sources:
        return False
    if source_id and source_id in excluded_sources:
        return False
    if allowed_origins and origin not in allowed_origins:
        return False
    threshold = policy.get("threshold_seconds")
    if threshold is None or value is None:
        return True
    try:
        return float(value) > float(threshold)
    except (TypeError, ValueError):
        return True


def _authorization_key(policy: str, command: str) -> tuple[str, str, str]:
    return _confirmation_scope_id(), _norm(policy), _norm(command)


def _purge_authorizations_locked(now_ts: float) -> None:
    for key, expires_at in list(_AUTHORIZATIONS.items()):
        if float(expires_at) <= now_ts:
            _AUTHORIZATIONS.pop(key, None)


def _grant_command_authorization(policy: str, command: str) -> None:
    now_ts = time.time()
    with _AUTH_LOCK:
        _purge_authorizations_locked(now_ts)
        _AUTHORIZATIONS[_authorization_key(policy, command)] = now_ts + 10.0


def consume_command_authorization(policy: str, command: str) -> bool:
    """Consume the one-use approval created by an affirmative reply."""
    now_ts = time.time()
    key = _authorization_key(policy, command)
    with _AUTH_LOCK:
        _purge_authorizations_locked(now_ts)
        expires_at = _AUTHORIZATIONS.pop(key, None)
    return expires_at is not None and float(expires_at) > now_ts


def _revoke_command_authorization(policy: str, command: str) -> None:
    with _AUTH_LOCK:
        _AUTHORIZATIONS.pop(_authorization_key(policy, command), None)


def request_command_confirmation(
    *,
    policy: str,
    command: str,
    prompt: str,
    cancel_response: str = "Okay, I left it unchanged.",
    metadata: Optional[dict[str, Any]] = None,
) -> str:
    """Store a pending request that will replay a deterministic command."""
    confirmation_id = str(uuid.uuid4())[:8]
    remember_referent(
        _CONFIRMATION_KIND,
        confirmation_id,
        label=prompt,
        capabilities={_CONFIRMATION_CAPABILITY, _PENDING_CAPABILITY},
        data={
            "id": confirmation_id,
            "mode": "command",
            "policy": str(policy or "").strip(),
            "command": str(command or "").strip(),
            "prompt": str(prompt or "").strip(),
            "cancel_response": str(cancel_response or "").strip(),
            "metadata": dict(metadata or {}),
        },
        ttl_seconds=_confirmation_ttl_seconds(),
        scope_id=_confirmation_scope_id(),
        source="confirmation",
    )
    logging.info(
        "COMMAND_CONFIRMATION_REQUEST id=%s policy=%s command=%r",
        confirmation_id,
        policy,
        command,
    )
    return prompt


def request_typed_confirmation(
    *,
    policy: str,
    action_type: str,
    payload: dict[str, Any],
    prompt: str,
    cancel_response: str = "Okay, I left it unchanged.",
) -> str:
    """Store a pending typed action whose executor is supplied by dispatch."""
    confirmation_id = str(uuid.uuid4())[:8]
    remember_referent(
        _CONFIRMATION_KIND,
        confirmation_id,
        label=prompt,
        capabilities={_CONFIRMATION_CAPABILITY, _PENDING_CAPABILITY},
        data={
            "id": confirmation_id,
            "mode": "typed",
            "policy": str(policy or "").strip(),
            "action_type": str(action_type or "").strip(),
            "payload": dict(payload or {}),
            "prompt": str(prompt or "").strip(),
            "cancel_response": str(cancel_response or "").strip(),
        },
        ttl_seconds=_confirmation_ttl_seconds(),
        scope_id=_confirmation_scope_id(),
        source="confirmation",
    )
    logging.info(
        "TYPED_CONFIRMATION_REQUEST id=%s policy=%s action_type=%s",
        confirmation_id,
        policy,
        action_type,
    )
    return prompt


def pending_confirmation() -> Optional[dict[str, Any]]:
    return resolve_referent(
        kinds={_CONFIRMATION_KIND},
        capability=_CONFIRMATION_CAPABILITY,
        scope_id=_confirmation_scope_id(),
    )


def cancel_pending_confirmation() -> bool:
    """Clear the pending confirmation for the exact current request source."""
    entry = pending_confirmation()
    if not entry:
        return False
    forget_referent(
        _CONFIRMATION_KIND,
        key=str(entry.get("key") or ""),
        scope_id=_confirmation_scope_id(),
    )
    return True


def handle_confirmation_controls(
    *,
    tl: str,
    execute_command: Callable[[str], Optional[str]],
    typed_executors: Optional[Dict[str, Callable[[dict[str, Any]], Optional[str]]]] = None,
    typed_rejectors: Optional[Dict[str, Callable[[dict[str, Any]], Optional[str]]]] = None,
    typed_revision_handlers: Optional[
        Dict[str, Callable[[dict[str, Any], str], Optional[str]]]
    ] = None,
) -> Optional[str]:
    """Intercept replies to a pending confirmation before ordinary routing."""
    entry = pending_confirmation()
    if not entry:
        return None

    reply = _norm(tl)
    data = dict(entry.get("data") or {})
    entry_id = str(entry.get("key") or "")

    if reply in _NEGATIVE:
        forget_referent(
            _CONFIRMATION_KIND,
            key=entry_id,
            scope_id=_confirmation_scope_id(),
        )
        mode = str(data.get("mode") or "")
        action_type = str(data.get("action_type") or "")
        rejector = (typed_rejectors or {}).get(action_type)
        if reply in _REVISION_NEGATIVE and mode == "typed" and callable(rejector):
            logging.info(
                "TYPED_CONFIRMATION_REJECT id=%s action_type=%s revision=True",
                entry_id,
                action_type,
            )
            try:
                response = rejector(dict(data.get("payload") or {}))
            except Exception:
                logging.exception("TYPED_CONFIRMATION_REJECT_FAIL id=%s", entry_id)
                return "I couldn't revise that action."
            return response if response is not None else str(
                data.get("cancel_response") or "Okay, I left it unchanged."
            )
        logging.info("COMMAND_CONFIRMATION_REJECT id=%s", entry_id)
        return str(data.get("cancel_response") or "Okay, I left it unchanged.")

    if reply not in _AFFIRMATIVE:
        forget_referent(
            _CONFIRMATION_KIND,
            key=entry_id,
            scope_id=_confirmation_scope_id(),
        )
        mode = str(data.get("mode") or "")
        action_type = str(data.get("action_type") or "")
        reviser = (typed_revision_handlers or {}).get(action_type)
        if mode == "typed" and callable(reviser):
            try:
                response = reviser(dict(data.get("payload") or {}), str(tl or ""))
            except Exception:
                logging.exception("TYPED_CONFIRMATION_REVISION_FAIL id=%s", entry_id)
                return "I couldn't revise that action."
            if response is not None:
                logging.info(
                    "TYPED_CONFIRMATION_REVISE id=%s action_type=%s reply=%r",
                    entry_id,
                    action_type,
                    tl,
                )
                return response
        logging.info(
            "COMMAND_CONFIRMATION_SUPERSEDE id=%s replacement=%r",
            entry_id,
            tl,
        )
        return None

    forget_referent(
        _CONFIRMATION_KIND,
        key=entry_id,
        scope_id=_confirmation_scope_id(),
    )
    mode = str(data.get("mode") or "")
    if mode == "command":
        policy = str(data.get("policy") or "")
        command = str(data.get("command") or "").strip()
        if not policy or not command:
            return "I couldn't verify that action, so I left it unchanged."
        _grant_command_authorization(policy, command)
        logging.info(
            "COMMAND_CONFIRMATION_ACCEPT id=%s policy=%s command=%r",
            entry_id,
            policy,
            command,
        )
        try:
            response = execute_command(command)
        except Exception:
            logging.exception("COMMAND_CONFIRMATION_REPLAY_FAIL id=%s", entry_id)
            return "I couldn't complete that action."
        finally:
            _revoke_command_authorization(policy, command)
        return response if response is not None else "I couldn't complete that action."

    if mode == "typed":
        action_type = str(data.get("action_type") or "")
        executor = (typed_executors or {}).get(action_type)
        if not callable(executor):
            logging.error(
                "TYPED_CONFIRMATION_EXECUTOR_MISSING id=%s action_type=%s",
                entry_id,
                action_type,
            )
            return "I couldn't verify that action, so I left it unchanged."
        logging.info(
            "TYPED_CONFIRMATION_ACCEPT id=%s action_type=%s",
            entry_id,
            action_type,
        )
        try:
            response = executor(dict(data.get("payload") or {}))
        except Exception:
            logging.exception("TYPED_CONFIRMATION_EXECUTE_FAIL id=%s", entry_id)
            return "I couldn't complete that action."
        return response if response is not None else "I couldn't complete that action."

    return "I couldn't verify that action, so I left it unchanged."


def reset_confirmation_state() -> None:
    """Clear transient authorizations; primarily useful for tests and resets."""
    with _AUTH_LOCK:
        _AUTHORIZATIONS.clear()
