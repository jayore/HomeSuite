"""Forward locally transcribed voice commands to a Home Suite brain.

Satellite nodes retain their microphone, wake-word/PTT, STT, cue, and response
playback paths. This module owns only the authenticated transcript handoff and
normalizes the brain's structured response for the local voice runtime.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from voice_timing import timing_for_transport, utterance_id_from_timing


class SatelliteRuntimeError(RuntimeError):
    """Raised when a satellite command cannot be completed by the brain."""


@dataclass(frozen=True)
class SatelliteCommandResult:
    """Normalized command result returned by the brain API."""

    handled: bool
    action_occurred: bool
    response_text: str
    source: str
    request_id: str
    disposition: str = ""
    context: Optional[dict[str, Any]] = None
    timing: Optional[dict[str, Any]] = None
    arbitration: Optional[dict[str, Any]] = None

    @property
    def cancelled(self) -> bool:
        return self.source == "cancelled"

    @property
    def suppressed(self) -> bool:
        return self.disposition == "suppressed" or self.source == "arbitration_suppressed"


def normalize_command_url(value: str) -> str:
    """Validate a brain URL and append ``/command`` to a bare server URL."""
    url = str(value or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SatelliteRuntimeError(
            "Satellite brain URL must be a complete http:// or https:// URL."
        )
    path = parsed.path.rstrip("/")
    if not path:
        path = "/command"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def build_command_payload(
    text: str,
    *,
    source_id: str,
    source_room: str,
    trigger: str,
    request_id: str = "",
    stt: Optional[Mapping[str, Any]] = None,
    timing: Optional[Mapping[str, Any]] = None,
    interaction_id: str = "",
    winner_token: str = "",
) -> dict[str, Any]:
    """Build the source-aware request envelope accepted by ``POST /command``."""
    source = str(source_id or "").strip()
    room = str(source_room or "").strip()
    trigger_name = str(trigger or "voice").strip().lower() or "voice"
    if not source:
        raise SatelliteRuntimeError("Satellite source ID is empty.")
    if not room:
        raise SatelliteRuntimeError("Satellite source room is empty.")

    public_timing = timing_for_transport(timing)
    command_id = str(request_id or utterance_id_from_timing(public_timing) or "").strip()
    if not command_id:
        command_id = f"{source}-{time.time_ns() // 1_000_000}-{uuid.uuid4().hex[:8]}"

    payload: dict[str, Any] = {
        "text": str(text or "").strip(),
        "source_id": source,
        "source_type": "satellite",
        "origin": f"satellite_{trigger_name}",
        "source_room": room,
        "request_id": command_id,
        "response_mode": "text",
    }
    if stt:
        payload["stt"] = dict(stt)
    if public_timing:
        payload["timing"] = public_timing
    interaction = str(interaction_id or "").strip()
    token = str(winner_token or "").strip()
    if interaction or token:
        if not interaction or not token:
            raise SatelliteRuntimeError(
                "Wake-word arbitration requires both an interaction ID and winner token."
            )
        payload["interaction_id"] = interaction
        payload["winner_token"] = token
    return payload


def forward_command(
    text: str,
    *,
    brain_url: str,
    api_key: str,
    source_id: str,
    source_room: str,
    trigger: str,
    timeout_seconds: float = 20.0,
    request_id: str = "",
    stt: Optional[Mapping[str, Any]] = None,
    timing: Optional[Mapping[str, Any]] = None,
    interaction_id: str = "",
    winner_token: str = "",
) -> SatelliteCommandResult:
    """Send one transcript to the brain and return its normalized outcome."""
    command_text = str(text or "").strip()
    if not command_text:
        raise SatelliteRuntimeError("Satellite command text is empty.")
    key = str(api_key or "").strip()
    if not key:
        raise SatelliteRuntimeError("Satellite brain API key is empty.")

    url = normalize_command_url(brain_url)
    payload = build_command_payload(
        command_text,
        source_id=source_id,
        source_room=source_room,
        trigger=trigger,
        request_id=request_id,
        stt=stt,
        timing=timing,
        interaction_id=interaction_id,
        winner_token=winner_token,
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-Key": key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=max(0.1, float(timeout_seconds)),
        ) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
            detail = str(body.get("error") or "").strip()
        except Exception:
            pass
        suffix = f": {detail}" if detail else ""
        raise SatelliteRuntimeError(f"Brain returned HTTP {exc.code}{suffix}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None) or str(exc)
        raise SatelliteRuntimeError(f"Could not reach the Home Suite brain: {reason}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SatelliteRuntimeError("Brain returned an invalid JSON response.") from exc
    if not isinstance(data, dict):
        raise SatelliteRuntimeError("Brain returned an invalid response object.")
    if not bool(data.get("ok")):
        detail = str(data.get("error") or "request failed").strip()
        raise SatelliteRuntimeError(f"Brain rejected the command: {detail}")

    context = data.get("context")
    response_timing = data.get("timing")
    arbitration = data.get("arbitration")
    return SatelliteCommandResult(
        handled=bool(data.get("handled")),
        action_occurred=bool(data.get("action_occurred")),
        response_text=str(data.get("text") or data.get("response") or "").strip(),
        source=str(data.get("source") or "").strip(),
        request_id=str(data.get("request_id") or payload["request_id"]).strip(),
        disposition=str(data.get("disposition") or "").strip(),
        context=dict(context) if isinstance(context, dict) else None,
        timing=dict(response_timing) if isinstance(response_timing, dict) else None,
        arbitration=dict(arbitration) if isinstance(arbitration, dict) else None,
    )
