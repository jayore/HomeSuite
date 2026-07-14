"""Apply bounded light changes and safely restore the prior state.

Temporary actions are intentionally narrower than general scheduling. A
request such as "set the stair light to red for ten minutes" resolves through
the normal command brain, snapshots the real light, applies the requested
write, and persists a conditional restore. At expiry, restoration happens only
when the light still matches the temporary state. A manual change or later
permanent command therefore wins instead of being overwritten unexpectedly.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from schedule_controls import parse_duration_seconds


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_STATE_PATH = BASE_DIR / "state" / "temporary_actions.json"

_DURATION_SUFFIX_RE = re.compile(
    r"^(?P<command>.+?)\s+for\s+(?:the\s+next\s+)?"
    r"(?P<num>\d{1,4}|[a-z]+(?:[\s-]+[a-z]+)?)\s+"
    r"(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?)\s*$",
    re.IGNORECASE,
)
_ACTION_START_RE = re.compile(
    r"^(?:please\s+)?(?:turn|switch|set|make|change|dim|brighten|toggle|"
    r"open|close|play|pause|start|stop|lock|unlock|run)\b",
    re.IGNORECASE,
)
_RESTORABLE_ATTRS = (
    "brightness",
    "color_mode",
    "color_temp",
    "color_temp_kelvin",
    "hs_color",
    "xy_color",
    "rgb_color",
    "rgbw_color",
    "rgbww_color",
    "effect",
)


@dataclass(frozen=True)
class TemporaryActionRequest:
    command: str
    duration_seconds: float


def parse_temporary_action(text: str) -> Optional[TemporaryActionRequest]:
    """Parse a command-final ``for <duration>`` temporary action request."""
    normalized = re.sub(r"[?!.]+$", "", str(text or "").strip())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    match = _DURATION_SUFFIX_RE.match(normalized)
    if not match:
        return None
    command = match.group("command").strip(" ,;:-")
    if not command or not _ACTION_START_RE.match(command):
        return None
    duration = parse_duration_seconds(match.group("num"), match.group("unit"))
    if duration is None:
        return None
    return TemporaryActionRequest(command=command, duration_seconds=duration)


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def snapshot_light_state(state: dict) -> Optional[dict]:
    """Keep only the state fields needed for comparison and restoration."""
    if not isinstance(state, dict):
        return None
    state_value = str(state.get("state") or "").strip().lower()
    if not state_value:
        return None
    attrs = state.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {}
    kept = {
        key: _json_value(attrs.get(key))
        for key in _RESTORABLE_ATTRS
        if attrs.get(key) is not None
    }
    return {"state": state_value, "attributes": kept}


def light_state_signature(state: dict) -> Optional[dict]:
    """Return a stable comparison signature for one light state."""
    snapshot = snapshot_light_state(state)
    if not snapshot:
        return None
    attrs = snapshot["attributes"]
    signature = {
        "state": snapshot["state"],
        "brightness": attrs.get("brightness"),
        "color_mode": attrs.get("color_mode"),
        "effect": attrs.get("effect"),
    }
    mode = str(attrs.get("color_mode") or "").lower()
    mode_fields = {
        "color_temp": ("color_temp_kelvin", "color_temp"),
        "hs": ("hs_color",),
        "xy": ("xy_color",),
        "rgb": ("rgb_color",),
        "rgbw": ("rgbw_color",),
        "rgbww": ("rgbww_color",),
    }
    fields = mode_fields.get(mode, ())
    if not fields:
        fields = (
            "color_temp_kelvin",
            "color_temp",
            "hs_color",
            "xy_color",
            "rgb_color",
            "rgbw_color",
            "rgbww_color",
        )
    for key in fields:
        if attrs.get(key) is not None:
            signature[key] = attrs.get(key)
            break
    return signature


def restore_call_for_snapshot(entity_id: str, snapshot: dict) -> Optional[Tuple[str, dict]]:
    """Build the least-ambiguous HA light service call for a saved snapshot."""
    if not snapshot:
        return None
    if snapshot.get("state") == "off":
        return "light/turn_off", {"entity_id": entity_id}
    if snapshot.get("state") != "on":
        return None

    attrs = snapshot.get("attributes") or {}
    payload = {"entity_id": entity_id}
    if attrs.get("brightness") is not None:
        payload["brightness"] = attrs["brightness"]

    mode = str(attrs.get("color_mode") or "").lower()
    mode_fields = {
        "color_temp": ("color_temp_kelvin", "color_temp"),
        "hs": ("hs_color",),
        "xy": ("xy_color",),
        "rgb": ("rgb_color",),
        "rgbw": ("rgbw_color",),
        "rgbww": ("rgbww_color",),
    }
    for key in mode_fields.get(mode, ()):
        if attrs.get(key) is not None:
            payload[key] = attrs[key]
            break
    if attrs.get("effect") not in (None, "", "none"):
        payload["effect"] = attrs["effect"]
    return "light/turn_on", payload


class TemporaryActionStore:
    """Persistent conditional restores with injectable HA and clock boundaries."""

    def __init__(
        self,
        path: Path = DEFAULT_STATE_PATH,
        *,
        get_state: Optional[Callable[[str], Optional[dict]]] = None,
        call_service: Optional[Callable[[str, dict], bool]] = None,
        now_fn: Callable[[], float] = time.time,
    ):
        self.path = Path(path)
        self.get_state = get_state
        self.call_service = call_service
        self.now_fn = now_fn
        self._lock = threading.RLock()

    def configure(
        self,
        *,
        get_state: Callable[[str], Optional[dict]],
        call_service: Callable[[str, dict], bool],
    ) -> None:
        self.get_state = get_state
        self.call_service = call_service

    def _load(self) -> List[dict]:
        if not self.path.exists():
            return []
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
            rows = body.get("overrides", []) if isinstance(body, dict) else body
            return [row for row in rows if isinstance(row, dict)]
        except Exception:
            logging.exception("TEMP_ACTION_LOAD_FAIL path=%s", self.path)
            return []

    def _save(self, rows: List[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = Path(str(self.path) + ".tmp")
        temp_path.write_text(
            json.dumps({"version": 1, "overrides": rows}, indent=2),
            encoding="utf-8",
        )
        os.replace(str(temp_path), str(self.path))

    def list_overrides(self) -> List[dict]:
        with self._lock:
            return [dict(row) for row in self._load()]

    def begin(
        self,
        *,
        entity_id: str,
        label: str,
        original_state: dict,
        duration_seconds: float,
        observe_delay_seconds: float,
        observe_timeout_seconds: float = 5.0,
    ) -> dict:
        now_ts = float(self.now_fn())
        pre_apply_signature = light_state_signature(original_state)
        original = snapshot_light_state(original_state)
        if not original or not pre_apply_signature:
            raise ValueError("original light state is unavailable")

        with self._lock:
            rows = self._load()
            retained = []
            for row in rows:
                if row.get("entity_id") != entity_id:
                    retained.append(row)
                    continue
                try:
                    active = float(row.get("expires_at") or 0.0) > now_ts
                except (TypeError, ValueError):
                    active = False
                if active and row.get("original"):
                    original = row["original"]
                    logging.info(
                        "TEMP_ACTION_SUPERSEDE entity=%s previous=%s",
                        entity_id,
                        row.get("id"),
                    )

            row = {
                "id": str(uuid.uuid4())[:8],
                "entity_id": entity_id,
                "label": label,
                "created_at": now_ts,
                "expires_at": now_ts + float(duration_seconds),
                "observe_after": now_ts + max(0.0, float(observe_delay_seconds)),
                "observe_deadline": now_ts + max(
                    float(observe_delay_seconds),
                    float(observe_timeout_seconds),
                ),
                "original": original,
                "pre_apply_signature": pre_apply_signature,
                "applied_signature": None,
            }
            retained.append(row)
            self._save(retained)
        logging.info(
            "TEMP_ACTION_ADD id=%s entity=%s duration=%.1f",
            row["id"],
            entity_id,
            duration_seconds,
        )
        return dict(row)

    def cancel(self, row_id: str) -> None:
        with self._lock:
            rows = self._load()
            kept = [row for row in rows if str(row.get("id")) != str(row_id)]
            if len(kept) != len(rows):
                self._save(kept)

    def tick(self, *, now_ts: Optional[float] = None) -> None:
        if not callable(self.get_state) or not callable(self.call_service):
            return
        now_value = float(self.now_fn() if now_ts is None else now_ts)

        with self._lock:
            rows = self._load()
            changed = False
            retained = []
            for row in rows:
                entity_id = str(row.get("entity_id") or "")
                if not entity_id.startswith("light."):
                    changed = True
                    continue

                try:
                    observe_after = float(row.get("observe_after") or 0.0)
                    observe_deadline = float(row.get("observe_deadline") or observe_after)
                    expires_at = float(row.get("expires_at") or 0.0)
                except (TypeError, ValueError):
                    changed = True
                    continue

                current = None
                if row.get("applied_signature") is None and now_value >= observe_after:
                    current = self.get_state(entity_id)
                    signature = light_state_signature(current) if current else None
                    if signature is not None:
                        if (
                            signature == row.get("pre_apply_signature")
                            and now_value < observe_deadline
                        ):
                            retained.append(row)
                            continue
                        row["applied_signature"] = signature
                        row["observed_at"] = now_value
                        changed = True
                        logging.info(
                            "TEMP_ACTION_ARM id=%s entity=%s",
                            row.get("id"),
                            entity_id,
                        )

                if now_value < expires_at:
                    retained.append(row)
                    continue

                if row.get("applied_signature") is None:
                    retained.append(row)
                    continue

                if current is None:
                    current = self.get_state(entity_id)
                current_signature = light_state_signature(current) if current else None
                if current_signature is None:
                    retained.append(row)
                    continue

                if current_signature != row.get("applied_signature"):
                    logging.info(
                        "TEMP_ACTION_RESTORE_SKIP_CHANGED id=%s entity=%s",
                        row.get("id"),
                        entity_id,
                    )
                    changed = True
                    continue

                restore = restore_call_for_snapshot(entity_id, row.get("original") or {})
                if restore is None:
                    logging.warning(
                        "TEMP_ACTION_RESTORE_SKIP_INVALID id=%s entity=%s",
                        row.get("id"),
                        entity_id,
                    )
                    changed = True
                    continue
                service, payload = restore
                if self.call_service(service, payload):
                    logging.info(
                        "TEMP_ACTION_RESTORE_OK id=%s entity=%s",
                        row.get("id"),
                        entity_id,
                    )
                    changed = True
                    continue
                retained.append(row)

            if changed or len(retained) != len(rows):
                self._save(retained)


_STORE = TemporaryActionStore()


def configure_runtime(
    *,
    get_state: Callable[[str], Optional[dict]],
    call_service: Callable[[str, dict], bool],
) -> None:
    _STORE.configure(get_state=get_state, call_service=call_service)


def tick() -> None:
    _STORE.tick()


def _duration_phrase(seconds: float) -> str:
    total = int(round(seconds))
    if total % 3600 == 0:
        count, unit = total // 3600, "hour"
    elif total % 60 == 0:
        count, unit = total // 60, "minute"
    else:
        count, unit = total, "second"
    return f"{count} {unit}" + ("" if count == 1 else "s")


def _captured_light_write(metadata: dict) -> Optional[Tuple[str, dict, str]]:
    writes = metadata.get("writes") if isinstance(metadata, dict) else None
    if not isinstance(writes, list) or len(writes) != 1:
        return None
    write = writes[0] if isinstance(writes[0], dict) else {}
    service = str(write.get("service") or "")
    payload = dict(write.get("data") or {}) if isinstance(write.get("data"), dict) else {}
    entity_id = payload.get("entity_id")
    if service not in {"light/turn_on", "light/turn_off"}:
        return None
    if not isinstance(entity_id, str) or not entity_id.startswith("light."):
        return None
    return service, payload, entity_id


def handle_temporary_action(
    *,
    tl: str,
    preview_command: Callable[[str], Tuple[bool, str, dict]],
    get_state: Callable[[str], Optional[dict]],
    call_service: Callable[[str, dict], bool],
    mark_action: Callable[[], None],
    remember_light: Callable[[str], None],
    effects_are_live: bool,
) -> Optional[str]:
    """Claim, execute, and register one temporary light command."""
    request = parse_temporary_action(tl)
    if request is None:
        return None

    try:
        import app_config

        enabled = bool(getattr(app_config, "TEMPORARY_ACTIONS_ENABLED", True))
        max_seconds = float(getattr(app_config, "TEMPORARY_ACTION_MAX_SECONDS", 86400))
        observe_delay = float(
            getattr(app_config, "TEMPORARY_ACTION_OBSERVE_DELAY_SECONDS", 1.0)
        )
        observe_timeout = float(
            getattr(app_config, "TEMPORARY_ACTION_OBSERVE_TIMEOUT_SECONDS", 5.0)
        )
    except Exception:
        enabled, max_seconds, observe_delay, observe_timeout = True, 86400.0, 1.0, 5.0

    if not enabled:
        return "Temporary actions are disabled."
    if request.duration_seconds > max_seconds:
        return f"Temporary light changes can last up to {_duration_phrase(max_seconds)}."

    ok, reason, metadata = preview_command(request.command)
    captured = _captured_light_write(metadata)
    if not ok or captured is None:
        logging.info(
            "TEMP_ACTION_REJECT command=%r reason=%s writes=%r",
            request.command,
            reason,
            (metadata or {}).get("writes") if isinstance(metadata, dict) else None,
        )
        return "Temporary actions currently work with one light at a time."

    service, payload, entity_id = captured
    original_state = get_state(entity_id) if callable(get_state) else None
    original = snapshot_light_state(original_state) if original_state else None
    if original is None:
        return "I couldn't read that light's current state, so I left it unchanged."

    label = str((original_state.get("attributes") or {}).get("friendly_name") or "").strip()
    if not label:
        label = entity_id.split(".", 1)[-1].replace("_", " ")

    row = None
    if effects_are_live:
        row = _STORE.begin(
            entity_id=entity_id,
            label=label,
            original_state=original_state,
            duration_seconds=request.duration_seconds,
            observe_delay_seconds=observe_delay,
            observe_timeout_seconds=observe_timeout,
        )

    if not call_service(service, payload):
        if row is not None:
            _STORE.cancel(row["id"])
        return f"I couldn't change the {label}."

    mark_action()
    remember_light(entity_id)
    return f"Okay. I'll restore the {label} in {_duration_phrase(request.duration_seconds)}."
