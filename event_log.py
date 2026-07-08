from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

SCHEMA_VERSION = 1
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "events.jsonl")
_lock = threading.Lock()


def log_command_event(text: str, request_ctx, result, duration_ms: int) -> None:
    """Append one JSON line to the event log. Never raises."""
    try:
        entry = {
            "schema_version": SCHEMA_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "text": text,
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
        line = json.dumps(entry, ensure_ascii=False)
        os.makedirs(_LOG_DIR, exist_ok=True)
        with _lock:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass
