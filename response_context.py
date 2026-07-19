"""Carry structured deterministic-response metadata across one request.

Command handlers still return plain text for compatibility with the voice,
HTTP, Telegram, and console surfaces.  This request-local channel lets a
handler attach richer facts for continuity without encoding them into the
spoken response or relying on process-global "last result" state.
"""

from __future__ import annotations

import copy
from contextvars import ContextVar
from typing import Any, Optional


_CURRENT_RESPONSE_CONTEXT: ContextVar[Optional[dict[str, Any]]] = ContextVar(
    "homesuite_response_context",
    default=None,
)


def clear_response_context() -> None:
    """Clear metadata left by an earlier command in the current request task."""
    _CURRENT_RESPONSE_CONTEXT.set(None)


def set_response_context(kind: str, data: Optional[dict[str, Any]] = None) -> None:
    """Attach typed metadata to the deterministic response being built."""
    kind = str(kind or "").strip().lower()
    if not kind:
        clear_response_context()
        return
    _CURRENT_RESPONSE_CONTEXT.set(
        {
            "kind": kind,
            "data": copy.deepcopy(data or {}),
        }
    )


def consume_response_context() -> Optional[dict[str, Any]]:
    """Return and clear metadata for the current deterministic response."""
    value = _CURRENT_RESPONSE_CONTEXT.get()
    _CURRENT_RESPONSE_CONTEXT.set(None)
    return copy.deepcopy(value) if value else None
