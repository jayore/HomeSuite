"""Build cross-node timing metadata for one captured voice interaction.

UTC epoch timestamps make events comparable between NTP-synchronized nodes.
Monotonic timestamps stay process-local and are used only to derive durations,
so a wall-clock correction cannot produce a negative utterance length.
"""

from __future__ import annotations

import copy
import os
import re
import time
import uuid
from typing import Any, Mapping, MutableMapping, Optional


SCHEMA_VERSION = 1


def unix_time_ms() -> int:
    """Return current UTC Unix time in integer milliseconds."""
    return time.time_ns() // 1_000_000


def system_clock_synchronized() -> Optional[bool]:
    """Return systemd's cached NTP state when it is cheaply observable."""
    marker = "/run/systemd/timesync/synchronized"
    if os.path.exists(marker):
        return True
    if os.path.isdir("/run/systemd/timesync"):
        return False
    return None


def _optional_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _duration_ms(start_ns, end_ns) -> Optional[float]:
    start = _optional_int(start_ns)
    end = _optional_int(end_ns)
    if start is None or end is None or end < start:
        return None
    return round((end - start) / 1_000_000.0, 1)


def _source_token(source_id: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(source_id or "satellite").strip())
    return token.strip("-") or "satellite"


def new_voice_timing(
    *,
    trigger: str,
    source_id: str,
    wakeword_label: str = "",
    wakeword_score=None,
    wake_detected_at_unix_ms=None,
    wake_detected_monotonic_ns=None,
    wake_audio_end_at_unix_ms=None,
    wake_audio_end_monotonic_ns=None,
) -> dict[str, Any]:
    """Create timing state at the trigger boundary for one utterance."""
    now_unix_ms = unix_time_ms()
    now_monotonic_ns = time.monotonic_ns()
    detected_unix_ms = _optional_int(wake_detected_at_unix_ms) or now_unix_ms
    detected_monotonic_ns = (
        _optional_int(wake_detected_monotonic_ns) or now_monotonic_ns
    )
    audio_end_unix_ms = _optional_int(wake_audio_end_at_unix_ms)
    audio_end_monotonic_ns = _optional_int(wake_audio_end_monotonic_ns)
    event_unix_ms = audio_end_unix_ms or detected_unix_ms
    utterance_id = (
        f"{_source_token(source_id)}-{event_unix_ms}-{uuid.uuid4().hex[:10]}"
    )

    clock_sync = system_clock_synchronized()
    clock = {
        "timestamp_basis": "unix_epoch_utc",
        "timestamp_unit": "milliseconds",
        "durations_use_monotonic_clock": True,
    }
    if clock_sync is not None:
        clock["ntp_synchronized"] = clock_sync

    timing: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "utterance_id": utterance_id,
        "trigger": str(trigger or "voice").strip().lower() or "voice",
        "clock": clock,
        "created_at_ms": now_unix_ms,
        "_local": {
            "wake_detected_monotonic_ns": detected_monotonic_ns,
            "wake_audio_end_monotonic_ns": audio_end_monotonic_ns,
        },
    }

    if timing["trigger"] == "wakeword" or wakeword_label or wakeword_score is not None:
        wakeword = {
            "detected_at_ms": detected_unix_ms,
        }
        label = str(wakeword_label or "").strip()
        score = _optional_float(wakeword_score)
        if label:
            wakeword["label"] = label
        if score is not None:
            wakeword["score"] = round(score, 6)
        if audio_end_unix_ms is not None:
            wakeword["audio_end_at_ms"] = audio_end_unix_ms
        decision_latency = _duration_ms(
            audio_end_monotonic_ns,
            detected_monotonic_ns,
        )
        if decision_latency is not None:
            wakeword["decision_latency_ms"] = decision_latency
        timing["wakeword"] = wakeword

    return timing


def apply_capture_timing(
    timing: Optional[MutableMapping[str, Any]],
    capture: Mapping[str, Any],
) -> None:
    """Attach microphone acquisition and VAD speech boundaries in place."""
    if not isinstance(timing, MutableMapping) or not isinstance(capture, Mapping):
        return

    capture_started_ms = _optional_int(capture.get("capture_started_at_unix_ms"))
    capture_ended_ms = _optional_int(capture.get("capture_ended_at_unix_ms"))
    speech_started_ms = _optional_int(capture.get("speech_started_at_unix_ms"))
    speech_ended_ms = _optional_int(capture.get("speech_ended_at_unix_ms"))

    capture_block = {}
    if capture_started_ms is not None:
        capture_block["started_at_ms"] = capture_started_ms
    if capture_ended_ms is not None:
        capture_block["ended_at_ms"] = capture_ended_ms
    capture_duration = _duration_ms(
        capture.get("capture_started_monotonic_ns"),
        capture.get("capture_ended_monotonic_ns"),
    )
    if capture_duration is not None:
        capture_block["duration_ms"] = capture_duration
    if capture_block:
        timing["capture"] = capture_block

    speech_block = {}
    if speech_started_ms is not None:
        speech_block["started_at_ms"] = speech_started_ms
    if speech_ended_ms is not None:
        speech_block["ended_at_ms"] = speech_ended_ms
    speech_duration = _duration_ms(
        capture.get("speech_started_monotonic_ns"),
        capture.get("speech_ended_monotonic_ns"),
    )
    if speech_duration is not None:
        speech_block["duration_ms"] = speech_duration
    if speech_block:
        timing["speech"] = speech_block

    local = timing.setdefault("_local", {})
    if isinstance(local, MutableMapping):
        for key in (
            "capture_started_monotonic_ns",
            "capture_ended_monotonic_ns",
            "speech_started_monotonic_ns",
            "speech_ended_monotonic_ns",
        ):
            value = _optional_int(capture.get(key))
            if value is not None:
                local[key] = value

        wake_to_speech = _duration_ms(
            local.get("wake_audio_end_monotonic_ns")
            or local.get("wake_detected_monotonic_ns"),
            local.get("speech_started_monotonic_ns"),
        )
        if wake_to_speech is not None:
            timing["wake_to_speech_ms"] = wake_to_speech


def mark_stt_started(timing: Optional[MutableMapping[str, Any]]) -> None:
    if not isinstance(timing, MutableMapping):
        return
    timing["stt_started_at_ms"] = unix_time_ms()
    local = timing.setdefault("_local", {})
    if isinstance(local, MutableMapping):
        local["stt_started_monotonic_ns"] = time.monotonic_ns()


def mark_stt_completed(timing: Optional[MutableMapping[str, Any]]) -> None:
    if not isinstance(timing, MutableMapping):
        return
    timing["stt_completed_at_ms"] = unix_time_ms()
    local = timing.setdefault("_local", {})
    if isinstance(local, MutableMapping):
        completed_ns = time.monotonic_ns()
        local["stt_completed_monotonic_ns"] = completed_ns
        duration = _duration_ms(local.get("stt_started_monotonic_ns"), completed_ns)
        if duration is not None:
            timing["stt_duration_ms"] = duration


def timing_for_transport(timing: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """Copy public timing fields and stamp the HTTP send boundary."""
    if not isinstance(timing, Mapping):
        return None
    public = {
        key: copy.deepcopy(value)
        for key, value in timing.items()
        if not str(key).startswith("_")
    }
    public["satellite_sent_at_ms"] = unix_time_ms()
    return public


def timing_with_brain_receive(
    timing: Optional[Mapping[str, Any]],
    *,
    received_at_ms: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Copy client timing and add the brain's authoritative receive time."""
    if not isinstance(timing, Mapping):
        return None
    public = {
        key: copy.deepcopy(value)
        for key, value in timing.items()
        if not str(key).startswith("_")
    }
    brain_received_at_ms = int(received_at_ms or unix_time_ms())
    public["brain_received_at_ms"] = brain_received_at_ms
    brain_clock_sync = system_clock_synchronized()
    if brain_clock_sync is not None:
        public["brain_clock"] = {"ntp_synchronized": brain_clock_sync}
        source_clock = public.get("clock")
        source_clock_sync = (
            source_clock.get("ntp_synchronized")
            if isinstance(source_clock, Mapping)
            else None
        )
        public["cross_node_clock_usable"] = bool(
            source_clock_sync is True and brain_clock_sync is True
        )
    sent_at_ms = _optional_int(public.get("satellite_sent_at_ms"))
    if sent_at_ms is not None:
        public["apparent_transit_ms"] = brain_received_at_ms - sent_at_ms
    return public


def utterance_id_from_timing(timing: Optional[Mapping[str, Any]]) -> str:
    if not isinstance(timing, Mapping):
        return ""
    return str(timing.get("utterance_id") or "").strip()
