"""Coordinate one wake-word interaction across multiple voice frontends.

Satellites announce a wake hit before playing their acknowledgement cue. The
brain groups overlapping hits, grants one short-lived execution lease, and
silently suppresses the other candidates. Command execution remains outside
this module; the lease API only decides whether a submitted command may enter
the shared router and makes retries idempotent.
"""

from __future__ import annotations

import asyncio
import copy
import hmac
import math
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Optional


PROTOCOL_VERSION = 1
_AUDIO_QUALITY_DEPS = None


def _float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def warm_wake_audio_quality_metrics() -> bool:
    """Load quality-measurement dependencies before the first wake hit."""
    global _AUDIO_QUALITY_DEPS
    if _AUDIO_QUALITY_DEPS is not None:
        return True
    try:
        import numpy as np
        from audio_calibration import audio_metrics
    except Exception:
        return False
    _AUDIO_QUALITY_DEPS = (np, audio_metrics)
    return True


def measure_wake_audio_quality(frames, sample_rate: int) -> dict[str, float]:
    """Return compact, privacy-preserving quality metrics for wake audio.

    Only aggregate levels cross the network. The p90-to-p20 separation is a
    useful speech-versus-background proxy without assuming that the beginning
    of the retained wake buffer is silent.
    """
    try:
        if not warm_wake_audio_quality_metrics():
            return {}
        np, audio_metrics = _AUDIO_QUALITY_DEPS

        arrays = [np.asarray(frame, dtype=np.int16).reshape(-1) for frame in (frames or [])]
        arrays = [array for array in arrays if array.size]
        if not arrays:
            return {}
        pcm = np.concatenate(arrays)
        metrics = audio_metrics(pcm, max(1, int(sample_rate or 16000)), block_ms=50)
        p20 = _float(metrics.get("p20_dbfs"), -120.0)
        p90 = _float(metrics.get("p90_dbfs"), -120.0)
        return {
            "peak_dbfs": round(_float(metrics.get("peak_dbfs"), -120.0), 2),
            "rms_dbfs": round(_float(metrics.get("rms_dbfs"), -120.0), 2),
            "p20_dbfs": round(p20, 2),
            "p90_dbfs": round(p90, 2),
            "separation_db": round(max(0.0, p90 - p20), 2),
            "clip_pct": round(max(0.0, _float(metrics.get("clip_pct"), 0.0)), 5),
        }
    except Exception:
        return {}


def score_candidate(candidate: Mapping[str, Any]) -> dict[str, float]:
    """Score how clearly a candidate heard the wake phrase.

    Raw microphone level is deliberately only one component. Wake-model margin
    and signal separation are more stable across devices with different gain.
    These initial weights are observable and intentionally easy to tune once a
    second physical frontend is available.
    """
    wake_score = _clamp(_float(candidate.get("wakeword_score"), 0.0))
    threshold = _clamp(_float(candidate.get("wakeword_threshold"), 0.5), 0.0, 0.99)
    confidence = _clamp((wake_score - threshold) / max(0.05, 1.0 - threshold))

    quality = candidate.get("audio_quality")
    quality = quality if isinstance(quality, Mapping) else {}
    has_quality = bool(quality)
    separation_db = _float(quality.get("separation_db"), 0.0)
    p90_dbfs = _float(quality.get("p90_dbfs"), -120.0)
    clip_pct = max(0.0, _float(quality.get("clip_pct"), 0.0))

    clarity = _clamp((separation_db - 4.0) / 20.0) if has_quality else 0.5
    loudness = _clamp((p90_dbfs + 45.0) / 35.0) if has_quality else 0.5
    clipping_penalty = _clamp(clip_pct / 0.10) if has_quality else 0.0

    total = _clamp(
        (0.45 * confidence)
        + (0.35 * clarity)
        + (0.20 * loudness)
        - (0.30 * clipping_penalty)
    )
    return {
        "total": round(total, 6),
        "confidence": round(confidence, 6),
        "clarity": round(clarity, 6),
        "loudness": round(loudness, 6),
        "clipping_penalty": round(clipping_penalty, 6),
    }


@dataclass
class _Candidate:
    candidate_id: str
    source_id: str
    source_room: str
    wakeword_label: str
    event_at_ms: int
    event_clock_usable: bool
    received_at_ms: int
    received_monotonic: float
    payload: dict[str, Any]
    score: dict[str, float]


@dataclass
class _Cohort:
    cohort_id: str
    anchor_event_at_ms: int
    anchor_received_monotonic: float
    event_clock_usable: bool
    wakeword_label: str
    created_monotonic: float
    candidates: dict[str, _Candidate] = field(default_factory=dict)
    finalize_task: Optional[asyncio.Task] = None


@dataclass
class _ClosedCohort:
    anchor_event_at_ms: int
    anchor_received_monotonic: float
    event_clock_usable: bool
    wakeword_label: str
    winner_source_id: str
    interaction_id: str
    expires_monotonic: float


@dataclass
class _Interaction:
    interaction_id: str
    winner_source_id: str
    winner_token: str
    lease_expires_monotonic: float
    created_monotonic: float
    state: str = "granted"
    execution_future: Optional[asyncio.Future] = None
    result_payload: Optional[dict[str, Any]] = None
    result_status: int = 200
    completed_monotonic: float = 0.0


DecisionEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


class WakewordArbitrator:
    """Event-loop-owned wake candidate coordinator and execution lease store."""

    def __init__(
        self,
        emit_decision: DecisionEmitter,
        *,
        election_window_ms: int = 180,
        cohort_window_ms: int = 700,
        lease_seconds: float = 30.0,
        closed_cohort_seconds: float = 2.0,
        result_ttl_seconds: float = 90.0,
    ) -> None:
        self._emit_decision = emit_decision
        self.election_window_ms = max(0, int(election_window_ms))
        self.cohort_window_ms = max(100, int(cohort_window_ms))
        self.lease_seconds = max(2.0, float(lease_seconds))
        self.closed_cohort_seconds = max(0.5, float(closed_cohort_seconds))
        self.result_ttl_seconds = max(5.0, float(result_ttl_seconds))
        self._sources: dict[str, dict[str, Any]] = {}
        self._cohorts: dict[str, _Cohort] = {}
        self._closed_cohorts: list[_ClosedCohort] = []
        self._interactions: dict[str, _Interaction] = {}
        self._topology_version = 0

    def register_source(
        self,
        source_id: str,
        *,
        source_room: str = "",
        wakeword_capable: bool = True,
        capabilities: Optional[Mapping[str, Any]] = None,
    ) -> None:
        source = str(source_id or "").strip()
        if not source:
            raise ValueError("source_id is required")
        self._sources[source] = {
            "source_id": source,
            "source_room": str(source_room or "").strip(),
            "wakeword_capable": bool(wakeword_capable),
            "capabilities": dict(capabilities or {}),
            "registered_monotonic": time.monotonic(),
        }
        self._topology_version += 1

    def unregister_source(self, source_id: str) -> None:
        source = str(source_id or "").strip()
        if self._sources.pop(source, None) is not None:
            self._topology_version += 1
        for cohort_id, cohort in list(self._cohorts.items()):
            cohort.candidates.pop(source, None)
            if cohort.candidates:
                continue
            if cohort.finalize_task is not None:
                cohort.finalize_task.cancel()
            self._cohorts.pop(cohort_id, None)

    def eligible_source_ids(self) -> list[str]:
        return sorted(
            source_id
            for source_id, metadata in self._sources.items()
            if bool(metadata.get("wakeword_capable"))
        )

    def cluster_state(self) -> dict[str, Any]:
        eligible = self.eligible_source_ids()
        return {
            "type": "cluster_state",
            "protocol_version": PROTOCOL_VERSION,
            "topology_version": self._topology_version,
            "eligible_wakeword_nodes": len(eligible),
            "eligible_source_ids": eligible,
            "election_window_ms": self.election_window_ms,
        }

    @staticmethod
    def _event_fields(payload: Mapping[str, Any]) -> tuple[int, str, bool]:
        timing = payload.get("timing")
        timing = timing if isinstance(timing, Mapping) else {}
        clock = timing.get("clock")
        clock = clock if isinstance(clock, Mapping) else {}
        wakeword = timing.get("wakeword")
        wakeword = wakeword if isinstance(wakeword, Mapping) else {}
        event_at_ms = (
            _int(payload.get("wake_audio_end_at_ms"), 0)
            or _int(wakeword.get("audio_end_at_ms"), 0)
            or _int(wakeword.get("detected_at_ms"), 0)
            or _int(payload.get("detected_at_ms"), 0)
            or int(time.time_ns() // 1_000_000)
        )
        label = str(
            payload.get("wakeword_label")
            or wakeword.get("label")
            or ""
        ).strip().lower()
        return event_at_ms, label, clock.get("ntp_synchronized") is True

    def _cohort_matches(
        self,
        *,
        anchor_event_at_ms: int,
        anchor_received_monotonic: float,
        anchor_clock_usable: bool,
        event_at_ms: int,
        received_monotonic: float,
        event_clock_usable: bool,
    ) -> bool:
        """Match by acoustic time, falling back to brain arrival when needed.

        Model labels are intentionally not identity keys: two nodes may use
        differently named models for the same spoken wake phrase.
        """
        if anchor_clock_usable and event_clock_usable:
            delta_ms = abs(int(anchor_event_at_ms) - int(event_at_ms))
        else:
            delta_ms = abs(
                float(anchor_received_monotonic) - float(received_monotonic)
            ) * 1000.0
        return delta_ms <= self.cohort_window_ms

    def _purge(self) -> None:
        now = time.monotonic()
        self._closed_cohorts = [
            cohort for cohort in self._closed_cohorts
            if cohort.expires_monotonic > now
        ]
        for interaction_id, interaction in list(self._interactions.items()):
            if interaction.state == "executing":
                continue
            if interaction.state == "completed":
                expires = interaction.completed_monotonic + self.result_ttl_seconds
            else:
                expires = interaction.lease_expires_monotonic + self.closed_cohort_seconds
            if expires <= now:
                self._interactions.pop(interaction_id, None)

    async def submit_candidate(self, source_id: str, payload: Mapping[str, Any]) -> None:
        """Add one candidate and emit its decision now or after the hold window."""
        self._purge()
        source = str(source_id or "").strip()
        candidate_id = str(payload.get("candidate_id") or "").strip()
        if not source or not candidate_id:
            raise ValueError("source_id and candidate_id are required")

        received_at_ms = int(time.time_ns() // 1_000_000)
        received_monotonic = time.monotonic()
        event_at_ms, label, event_clock_usable = self._event_fields(payload)
        for closed in self._closed_cohorts:
            if self._cohort_matches(
                anchor_event_at_ms=closed.anchor_event_at_ms,
                anchor_received_monotonic=closed.anchor_received_monotonic,
                anchor_clock_usable=closed.event_clock_usable,
                event_at_ms=event_at_ms,
                received_monotonic=received_monotonic,
                event_clock_usable=event_clock_usable,
            ):
                await self._emit_decision(
                    source,
                    {
                        "type": "wakeword_decision",
                        "protocol_version": PROTOCOL_VERSION,
                        "candidate_id": candidate_id,
                        "disposition": "suppressed",
                        "reason": "late_duplicate",
                        "interaction_id": closed.interaction_id,
                        "winner_source_id": closed.winner_source_id,
                        "eligible_wakeword_nodes": len(self.eligible_source_ids()),
                    },
                )
                return

        cohort = next(
            (
                item for item in self._cohorts.values()
                if self._cohort_matches(
                    anchor_event_at_ms=item.anchor_event_at_ms,
                    anchor_received_monotonic=item.anchor_received_monotonic,
                    anchor_clock_usable=item.event_clock_usable,
                    event_at_ms=event_at_ms,
                    received_monotonic=received_monotonic,
                    event_clock_usable=event_clock_usable,
                )
            ),
            None,
        )
        if cohort is None:
            cohort = _Cohort(
                cohort_id=uuid.uuid4().hex,
                anchor_event_at_ms=event_at_ms,
                anchor_received_monotonic=received_monotonic,
                event_clock_usable=event_clock_usable,
                wakeword_label=label,
                created_monotonic=time.monotonic(),
            )
            self._cohorts[cohort.cohort_id] = cohort

        candidate_payload = dict(payload)
        candidate_payload["source_id"] = source
        score = score_candidate(candidate_payload)
        cohort.candidates[source] = _Candidate(
            candidate_id=candidate_id,
            source_id=source,
            source_room=str(payload.get("source_room") or "").strip(),
            wakeword_label=label,
            event_at_ms=event_at_ms,
            event_clock_usable=event_clock_usable,
            received_at_ms=received_at_ms,
            received_monotonic=received_monotonic,
            payload=candidate_payload,
            score=score,
        )

        if len(self.eligible_source_ids()) <= 1:
            await self._finalize_cohort(cohort.cohort_id, hold_ms=0)
            return
        if cohort.finalize_task is None:
            cohort.finalize_task = asyncio.create_task(
                self._finalize_after_hold(cohort.cohort_id)
            )

    async def _finalize_after_hold(self, cohort_id: str) -> None:
        await asyncio.sleep(self.election_window_ms / 1000.0)
        await self._finalize_cohort(cohort_id, hold_ms=self.election_window_ms)

    async def _finalize_cohort(self, cohort_id: str, *, hold_ms: int) -> None:
        cohort = self._cohorts.pop(cohort_id, None)
        if cohort is None or not cohort.candidates:
            return
        if not all(
            bool(candidate.payload.get("audio_quality"))
            for candidate in cohort.candidates.values()
        ):
            for candidate in cohort.candidates.values():
                confidence_only = dict(candidate.payload)
                confidence_only["audio_quality"] = {}
                candidate.score = score_candidate(confidence_only)
        use_acoustic_time = all(
            candidate.event_clock_usable
            for candidate in cohort.candidates.values()
        )
        candidates = sorted(
            cohort.candidates.values(),
            key=lambda item: (
                -_float(item.score.get("total"), 0.0),
                (
                    float(item.event_at_ms)
                    if use_acoustic_time
                    else item.received_monotonic * 1000.0
                ),
                item.source_id,
            ),
        )
        winner = candidates[0]
        interaction_id = uuid.uuid4().hex
        winner_token = secrets.token_urlsafe(24)
        now = time.monotonic()
        self._interactions[interaction_id] = _Interaction(
            interaction_id=interaction_id,
            winner_source_id=winner.source_id,
            winner_token=winner_token,
            lease_expires_monotonic=now + self.lease_seconds,
            created_monotonic=now,
        )
        self._closed_cohorts.append(
            _ClosedCohort(
                anchor_event_at_ms=cohort.anchor_event_at_ms,
                anchor_received_monotonic=cohort.anchor_received_monotonic,
                event_clock_usable=cohort.event_clock_usable,
                wakeword_label=cohort.wakeword_label,
                winner_source_id=winner.source_id,
                interaction_id=interaction_id,
                expires_monotonic=now + self.closed_cohort_seconds,
            )
        )

        eligible_count = len(self.eligible_source_ids())
        for candidate in candidates:
            granted = candidate.source_id == winner.source_id
            decision = {
                "type": "wakeword_decision",
                "protocol_version": PROTOCOL_VERSION,
                "candidate_id": candidate.candidate_id,
                "disposition": "granted" if granted else "suppressed",
                "reason": "winner" if granted else "better_candidate",
                "interaction_id": interaction_id,
                "winner_source_id": winner.source_id,
                "eligible_wakeword_nodes": eligible_count,
                "election_hold_ms": max(0, int(hold_ms)),
                "candidate_score": candidate.score,
                "winner_score": winner.score,
            }
            if granted:
                decision["winner_token"] = winner_token
                decision["lease_seconds"] = self.lease_seconds
            await self._emit_decision(candidate.source_id, decision)

    def begin_command(
        self,
        *,
        source_id: str,
        interaction_id: str,
        winner_token: str,
    ) -> dict[str, Any]:
        """Authorize one winner command or return its prior in-flight/result state."""
        self._purge()
        interaction = self._interactions.get(str(interaction_id or "").strip())
        if interaction is None:
            return {"state": "rejected", "reason": "unknown_interaction"}
        source = str(source_id or "").strip()
        token = str(winner_token or "").strip()
        if source != interaction.winner_source_id or not hmac.compare_digest(
            token,
            interaction.winner_token,
        ):
            return {
                "state": "suppressed",
                "reason": "not_winner",
                "winner_source_id": interaction.winner_source_id,
            }
        if interaction.state == "completed" and interaction.result_payload is not None:
            return {
                "state": "cached",
                "payload": copy.deepcopy(interaction.result_payload),
                "status": interaction.result_status,
            }
        if interaction.state == "executing" and interaction.execution_future is not None:
            return {"state": "wait", "future": interaction.execution_future}
        if interaction.lease_expires_monotonic <= time.monotonic():
            return {"state": "rejected", "reason": "lease_expired"}

        interaction.state = "executing"
        interaction.execution_future = asyncio.get_running_loop().create_future()
        return {"state": "execute"}

    def finish_command(
        self,
        interaction_id: str,
        payload: Mapping[str, Any],
        *,
        status: int = 200,
    ) -> None:
        interaction = self._interactions.get(str(interaction_id or "").strip())
        if interaction is None:
            return
        result = copy.deepcopy(dict(payload))
        interaction.state = "completed"
        interaction.result_payload = result
        interaction.result_status = int(status)
        interaction.completed_monotonic = time.monotonic()
        future = interaction.execution_future
        if future is not None and not future.done():
            future.set_result((copy.deepcopy(result), int(status)))
