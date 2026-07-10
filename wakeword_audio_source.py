"""Continuously drained microphone source with independent frame cursors."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

import numpy as np


class AudioFrameCursor:
    def __init__(self, source: "ContinuousAudioSource", next_sequence: int):
        self._source = source
        self.next_sequence = int(next_sequence)
        self.dropped_frames = 0

    def read_frame(self, timeout: float = 1.0):
        return self._source._read_for_cursor(self, timeout=timeout)

    def seek_live(self) -> int:
        self.next_sequence = self._source.next_live_sequence()
        return self.next_sequence


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
        logger=None,
    ):
        self.sd = sd_module
        self.device = device
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.frame_ms = int(frame_ms)
        self.frame_samples = max(1, int(round(self.sample_rate * self.frame_ms / 1000.0)))
        self.ring_frames = max(20, int(round(ring_ms / float(self.frame_ms))))
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
            latency="low",
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
        del time_info
        try:
            data = indata[:, 0] if getattr(indata, "ndim", 1) > 1 else indata
            data = np.asarray(data, dtype=np.int16).copy()
            if self._partial.size:
                data = np.concatenate((self._partial, data))

            complete = int(data.size // self.frame_samples)
            remainder_start = complete * self.frame_samples
            self._partial = data[remainder_start:].copy()

            now = time.monotonic()
            with self._condition:
                if status:
                    self._status_count += 1
                    self._last_status = str(status)
                for index in range(complete):
                    start = index * self.frame_samples
                    frame = data[start : start + self.frame_samples].copy()
                    self._ring.append((self._sequence, frame))
                    self._sequence += 1
                if complete:
                    self._last_frame_monotonic = now
                    self._condition.notify_all()
        except Exception:
            # PortAudio callbacks must not raise into the audio thread.
            self._status_count += 1
            self._last_status = "callback_error"

    def next_live_sequence(self) -> int:
        with self._condition:
            return int(self._sequence)

    def create_cursor(self, *, next_sequence: Optional[int] = None, live: bool = True) -> AudioFrameCursor:
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
                        sequence, frame = self._ring[offset]
                        cursor.next_sequence = int(sequence) + 1
                        return frame.copy()

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)
        return None

    def snapshot(self, *, end_sequence: Optional[int] = None, frame_count: Optional[int] = None):
        with self._condition:
            items = list(self._ring)
        if end_sequence is not None:
            items = [item for item in items if int(item[0]) <= int(end_sequence)]
        if frame_count is not None and frame_count >= 0:
            items = items[-int(frame_count):]
        return [frame.copy() for _, frame in items]

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
