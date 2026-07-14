from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

SCHEMA_VERSION = 1
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "events.jsonl")
_lock = threading.Lock()


def _pref(name: str, default):
    try:
        import app_config

        return getattr(app_config, name, default)
    except Exception:
        return default


def _enabled(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _nonnegative_int(value, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _rotate_if_needed(path: str, incoming_bytes: int, *, max_bytes: int, backup_count: int) -> None:
    """Rotate ``path`` before appending, retaining a bounded numbered history."""
    if max_bytes <= 0:
        return
    try:
        if os.path.getsize(path) + incoming_bytes <= max_bytes:
            return
    except FileNotFoundError:
        return

    if backup_count <= 0:
        with open(path, "w", encoding="utf-8"):
            pass
        return

    oldest = f"{path}.{backup_count}"
    if os.path.exists(oldest):
        os.remove(oldest)
    for index in range(backup_count - 1, 0, -1):
        source = f"{path}.{index}"
        if os.path.exists(source):
            os.replace(source, f"{path}.{index + 1}")
    os.replace(path, f"{path}.1")


def log_command_event(text: str, request_ctx, result, duration_ms: int) -> None:
    """Append bounded, privacy-aware command metadata. Never raises."""
    try:
        if not _enabled(_pref("COMMAND_EVENT_LOG_ENABLED", True)):
            return
        store_text = _enabled(_pref("COMMAND_EVENT_LOG_STORE_TEXT", False))
        entry = {
            "schema_version": SCHEMA_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "text_recorded": store_text,
            "text_length": len(text or ""),
            "source_id": getattr(request_ctx, "source_id", None),
            "source_type": getattr(request_ctx, "source_type", None),
            "origin": getattr(request_ctx, "origin", None),
            "source_room": getattr(request_ctx, "source_room", None),
            "effective_target_room": getattr(request_ctx, "effective_target_room", None),
            "handled": getattr(result, "handled", None),
            "action_occurred": getattr(result, "action_occurred", None),
            "response_source": getattr(result, "source", None),
            "duration_ms": duration_ms,
        }
        if store_text:
            entry["text"] = text
        line = json.dumps(entry, ensure_ascii=False)
        line_bytes = len((line + "\n").encode("utf-8"))
        max_bytes = _nonnegative_int(
            _pref("COMMAND_EVENT_LOG_MAX_BYTES", 2 * 1024 * 1024),
            2 * 1024 * 1024,
        )
        backup_count = _nonnegative_int(_pref("COMMAND_EVENT_LOG_BACKUP_COUNT", 3), 3)
        os.makedirs(_LOG_DIR, exist_ok=True)
        with _lock:
            _rotate_if_needed(
                _LOG_FILE,
                line_bytes,
                max_bytes=max_bytes,
                backup_count=backup_count,
            )
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass
