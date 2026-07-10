"""Wakeword-only resampling and WebRTC noise processing helpers."""

from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np


class WakewordFrontend:
    """Convert device PCM to clean 16 kHz chunks for OpenWakeWord."""

    def __init__(
        self,
        input_sample_rate: int,
        *,
        output_sample_rate: int = 16000,
        output_chunk_samples: int = 1280,
        noise_suppression_level: int = 0,
        auto_gain_dbfs: int = 0,
        volume_multiplier: float = 1.0,
        logger=None,
    ):
        self.input_sample_rate = int(input_sample_rate)
        self.output_sample_rate = int(output_sample_rate)
        self.output_chunk_samples = int(output_chunk_samples)
        self.noise_suppression_level = max(0, min(4, int(noise_suppression_level)))
        self.auto_gain_dbfs = max(0, min(31, int(auto_gain_dbfs)))
        self.volume_multiplier = max(0.05, min(8.0, float(volume_multiplier)))
        self.log = logger or logging.getLogger(__name__)
        self._pending = np.empty(0, dtype=np.int16)
        self._clean_pending = np.empty(0, dtype=np.int16)
        self._resampler = None
        self._processor = None

        if self.input_sample_rate != self.output_sample_rate:
            try:
                import soxr

                self._resampler = soxr.ResampleStream(
                    self.input_sample_rate,
                    self.output_sample_rate,
                    1,
                    dtype="int16",
                    quality="LQ",
                )
            except Exception:
                self.log.exception("WAKEWORD_FRONTEND_SOXR_UNAVAILABLE")

        if self.noise_suppression_level or self.auto_gain_dbfs:
            try:
                from webrtc_noise_gain import AudioProcessor

                self._processor = AudioProcessor(self.auto_gain_dbfs, self.noise_suppression_level)
            except Exception:
                self.log.exception("WAKEWORD_FRONTEND_WEBRTC_UNAVAILABLE")

        self.log.info(
            "WAKEWORD_FRONTEND_READY input_sr=%s output_sr=%s soxr=%s ns_level=%s auto_gain_dbfs=%s volume_multiplier=%.3f",
            self.input_sample_rate,
            self.output_sample_rate,
            bool(self._resampler),
            self.noise_suppression_level,
            self.auto_gain_dbfs,
            self.volume_multiplier,
        )

    def reset(self) -> None:
        self._pending = np.empty(0, dtype=np.int16)
        self._clean_pending = np.empty(0, dtype=np.int16)
        if self._resampler is not None:
            try:
                self._resampler.clear()
            except Exception:
                pass
        if self.noise_suppression_level or self.auto_gain_dbfs:
            try:
                from webrtc_noise_gain import AudioProcessor

                self._processor = AudioProcessor(self.auto_gain_dbfs, self.noise_suppression_level)
            except Exception:
                self._processor = None

    def _resample(self, pcm: np.ndarray) -> np.ndarray:
        pcm = np.asarray(pcm, dtype=np.int16).reshape(-1)
        if self.input_sample_rate == self.output_sample_rate:
            return pcm
        if self._resampler is not None:
            return np.asarray(self._resampler.resample_chunk(pcm, last=False), dtype=np.int16)

        from scipy.signal import resample_poly
        import math

        divisor = math.gcd(self.input_sample_rate, self.output_sample_rate)
        out = resample_poly(
            pcm.astype(np.float32, copy=False),
            up=self.output_sample_rate // divisor,
            down=self.input_sample_rate // divisor,
        )
        return np.clip(np.rint(out), -32768, 32767).astype(np.int16)

    def _clean_10ms(self, pcm: np.ndarray) -> np.ndarray:
        pcm = np.asarray(pcm, dtype=np.int16).reshape(-1)
        if self.volume_multiplier != 1.0:
            scaled = pcm.astype(np.float32) * self.volume_multiplier
            pcm = np.clip(np.rint(scaled), -32768, 32767).astype(np.int16)
        if self._processor is None:
            return pcm

        if pcm.size:
            self._clean_pending = np.concatenate((self._clean_pending, pcm))
        cleaned = []
        frame_samples = self.output_sample_rate // 100
        complete = self._clean_pending.size // frame_samples
        for index in range(complete):
            frame = self._clean_pending[
                index * frame_samples : (index + 1) * frame_samples
            ]
            result = self._processor.Process10ms(frame.tobytes())
            cleaned.append(np.frombuffer(result.audio, dtype=np.int16).copy())
        self._clean_pending = self._clean_pending[complete * frame_samples :].copy()
        return np.concatenate(cleaned) if cleaned else np.empty(0, dtype=np.int16)

    def push(self, pcm: np.ndarray) -> List[np.ndarray]:
        resampled = self._clean_10ms(self._resample(pcm))
        if resampled.size:
            self._pending = np.concatenate((self._pending, resampled))
        chunks = []
        while self._pending.size >= self.output_chunk_samples:
            chunks.append(self._pending[: self.output_chunk_samples].copy())
            self._pending = self._pending[self.output_chunk_samples :]
        return chunks


def clean_command_audio_16k(
    pcm: np.ndarray,
    *,
    noise_suppression_level: int = 0,
    auto_gain_dbfs: int = 0,
    volume_multiplier: float = 1.0,
    logger=None,
) -> np.ndarray:
    """Process a complete 16 kHz mono command without changing its length."""
    frontend = WakewordFrontend(
        16000,
        output_sample_rate=16000,
        output_chunk_samples=160,
        noise_suppression_level=noise_suppression_level,
        auto_gain_dbfs=auto_gain_dbfs,
        volume_multiplier=volume_multiplier,
        logger=logger,
    )
    pcm = np.asarray(pcm, dtype=np.int16).reshape(-1)
    output = []
    for start in range(0, pcm.size, 160):
        frame = pcm[start : start + 160]
        if frame.size < 160:
            output.append(frame.copy())
            break
        output.extend(frontend.push(frame))
    if not output:
        return pcm
    cleaned = np.concatenate(output).astype(np.int16, copy=False)
    if cleaned.size < pcm.size:
        cleaned = np.concatenate((cleaned, pcm[cleaned.size :]))
    return cleaned[: pcm.size]
