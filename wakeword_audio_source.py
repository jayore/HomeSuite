"""Continuously drain one microphone stream into a bounded frame ring.

Wakeword scoring and post-detection command capture need to observe the same
physical stream at different positions. ``ContinuousAudioSource`` therefore
assigns every fixed-duration frame a sequence number and lets each consumer use
an independent ``AudioFrameCursor``. A slow cursor advances to the oldest
retained frame and records the number it missed instead of blocking PortAudio.

The callback performs only framing and ring insertion; model inference and
command processing stay off the real-time audio thread.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

import numpy as np


class AudioFrameCursor:
    """Track one consumer's next sequence within a ``ContinuousAudioSource``."""
    def __init__(self, source: "ContinuousAudioSource", next_sequence: int):
        self._source = source
        self.next_sequence = int(next_sequence)
        self.dropped_frames = 0
        self.last_frame_sequence: Optional[int] = None
        self.last_frame_end_monotonic_ns: Optional[int] = None
        self.last_frame_end_unix_ms: Optional[int] = None

    def read_frame(self, timeout: float = 1.0):
        return self._source._read_for_cursor(self, timeout=timeout)

    def seek_live(self) -> int:
        """Discard buffered history and resume at the next frame produced."""
        self.next_sequence = self._source.next_live_sequence()
        return self.next_sequence

    def last_frame_timing(self):
        """Return acquisition timing for the frame most recently read."""
        if self.last_frame_sequence is None:
            return None
        return {
            "sequence": int(self.last_frame_sequence),
            "end_monotonic_ns": int(self.last_frame_end_monotonic_ns),
            "end_unix_ms": int(self.last_frame_end_unix_ms),
        }


class ContinuousAudioSource:
    """Own one PortAudio stream and retain a bounded sequence-addressed ring."""

    def __init__(
        self,
        sd_module,
        *,
        device: Optional[int],
        sample_rate: int,
        channels: int = 1,
        frame_ms: int = 10,
        ring_ms: int = 4000,
        stream_latency="low",
        logger=None,
    ):
        self.sd = sd_module
        self.device = device
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.frame_ms = int(frame_ms)
        self.frame_samples = max(1, int(round(self.sample_rate * self.frame_ms / 1000.0)))
        self.ring_frames = max(20, int(round(ring_ms / float(self.frame_ms))))
        self.stream_latency = stream_latency
        self.log = logger

        self._ring = deque(maxlen=self.ring_frames)
        self._condition = threading.Condition()
        self._sequence = 0
        self._stream = None
        self._partial = np.empty(0, dtype=np.int16)
        self._running = False
        self._status_count = 0
        self._last_status = ""
        self._last_frame_monotonic = 0.0

    @property
    def last_frame_monotonic(self) -> float:
        return float(self._last_frame_monotonic)

    def start(self) -> None:
        if self._running:
            return
        self._stream = self.sd.InputStream(
            device=self.device,
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            blocksize=self.frame_samples,
            latency=self.stream_latency,
            callback=self._callback,
        )
        self._stream.start()
        self._running = True

    def stop(self) -> None:
        self._running = False
        with self._condition:
            self._condition.notify_all()
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

    def _callback(self, indata, frames, time_info, status) -> None:
        try:
            data = indata[:, 0] if getattr(indata, "ndim", 1) > 1 else indata
            data = np.asarray(data, dtype=np.int16).copy()
            if self._partial.size:
                data = np.concatenate((self._partial, data))

            complete = int(data.size // self.frame_samples)
            remainder_start = complete * self.frame_samples
            self._partial = data[remainder_start:].copy()

            now_monotonic_ns = time.monotonic_ns()
            now_unix_ns = time.time_ns()
            frame_duration_ns = max(
                1,
                int(round(self.frame_samples * 1_000_000_000 / self.sample_rate)),
            )

            # PortAudio exposes the ADC start and callback clock. When present,
            # use them to remove callback scheduling delay from frame times.
            # The fallback still timestamps at callback delivery, which is
            # normally within one hardware block for this fixed-size stream.
            callback_audio_end_offset_ns = 0
            try:
                adc_start = float(
                    getattr(time_info, "inputBufferAdcTime", None)
                    if not isinstance(time_info, dict)
                    else time_info.get("inputBufferAdcTime")
                )
                current_time = float(
                    getattr(time_info, "currentTime", None)
                    if not isinstance(time_info, dict)
                    else time_info.get("currentTime")
                )
                adc_end = adc_start + (float(frames) / float(self.sample_rate))
                offset_seconds = adc_end - current_time
                if abs(offset_seconds) <= 2.0:
                    callback_audio_end_offset_ns = int(round(offset_seconds * 1_000_000_000))
            except (TypeError, ValueError, AttributeError):
                pass

            callback_audio_end_monotonic_ns = (
                now_monotonic_ns + callback_audio_end_offset_ns
            )
            callback_audio_end_unix_ns = now_unix_ns + callback_audio_end_offset_ns
            with self._condition:
                if status:
                    self._status_count += 1
                    self._last_status = str(status)
                for index in range(complete):
                    start = index * self.frame_samples
                    frame = data[start : start + self.frame_samples].copy()
                    frames_after = complete - index - 1
                    frame_end_monotonic_ns = (
                        callback_audio_end_monotonic_ns
                        - (frames_after * frame_duration_ns)
                    )
                    frame_end_unix_ns = (
                        callback_audio_end_unix_ns
                        - (frames_after * frame_duration_ns)
                    )
                    self._ring.append(
                        (
                            self._sequence,
                            frame,
                            frame_end_monotonic_ns,
                            frame_end_unix_ns // 1_000_000,
                        )
                    )
                    self._sequence += 1
                if complete:
                    self._last_frame_monotonic = now_monotonic_ns / 1_000_000_000.0
                    self._condition.notify_all()
        except Exception:
            # PortAudio callbacks must not raise into the audio thread.
            self._status_count += 1
            self._last_status = "callback_error"

    def next_live_sequence(self) -> int:
        with self._condition:
            return int(self._sequence)

    def create_cursor(self, *, next_sequence: Optional[int] = None, live: bool = True) -> AudioFrameCursor:
        """Create a cursor at an explicit sequence, the live edge, or ring start."""
        with self._condition:
            if next_sequence is None:
                if live or not self._ring:
                    next_sequence = self._sequence
                else:
                    next_sequence = self._ring[0][0]
            return AudioFrameCursor(self, int(next_sequence))

    def _read_for_cursor(self, cursor: AudioFrameCursor, *, timeout: float = 1.0):
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while self._running:
                if self._ring:
                    oldest = int(self._ring[0][0])
                    newest = int(self._ring[-1][0])
                    if cursor.next_sequence < oldest:
                        cursor.dropped_frames += oldest - cursor.next_sequence
                        cursor.next_sequence = oldest
                    if cursor.next_sequence <= newest:
                        offset = cursor.next_sequence - oldest
                        sequence, frame, end_monotonic_ns, end_unix_ms = self._ring[offset]
                        cursor.next_sequence = int(sequence) + 1
                        cursor.last_frame_sequence = int(sequence)
                        cursor.last_frame_end_monotonic_ns = int(end_monotonic_ns)
                        cursor.last_frame_end_unix_ms = int(end_unix_ms)
                        return frame.copy()

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)
        return None

    def snapshot(self, *, end_sequence: Optional[int] = None, frame_count: Optional[int] = None):
        """Copy retained frames for pre-trigger handoff or diagnostics."""
        with self._condition:
            items = list(self._ring)
        if end_sequence is not None:
            items = [item for item in items if int(item[0]) <= int(end_sequence)]
        if frame_count is not None and frame_count >= 0:
            items = items[-int(frame_count):]
        return [frame.copy() for _, frame, _, _ in items]

    def timing_for_sequence(self, sequence: int):
        """Return acquisition timing for a retained frame sequence."""
        target = int(sequence)
        with self._condition:
            for item_sequence, _, end_monotonic_ns, end_unix_ms in self._ring:
                if int(item_sequence) == target:
                    return {
                        "sequence": target,
                        "end_monotonic_ns": int(end_monotonic_ns),
                        "end_unix_ms": int(end_unix_ms),
                    }
        return None

    def stats(self):
        with self._condition:
            return {
                "running": bool(self._running),
                "next_sequence": int(self._sequence),
                "ring_frames": len(self._ring),
                "status_count": int(self._status_count),
                "last_status": self._last_status,
                "last_frame_age_sec": (
                    max(0.0, time.monotonic() - self._last_frame_monotonic)
                    if self._last_frame_monotonic else None
                ),
            }
