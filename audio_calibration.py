"""Reusable microphone measurements for CLI and management-console calibration.

The capture function deliberately does not own service coordination. Callers
must first make sure wake-word and PTT capture have released the microphone.
Keeping measurement math here gives the command-line tool and browser wizard
the same thresholds, overflow accounting, and recommendations.
"""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np


INT16_MAX = 32768.0


def dbfs(value: float) -> float:
    value = max(float(value or 0.0), 1.0)
    return 20.0 * math.log10(value / INT16_MAX)


def block_rms(samples: np.ndarray, sample_rate: int, block_ms: int) -> np.ndarray:
    block = max(1, int(round(sample_rate * block_ms / 1000.0)))
    if len(samples) < block:
        return np.array([], dtype=np.float64)
    usable = samples[: len(samples) - (len(samples) % block)]
    if usable.size == 0:
        return np.array([], dtype=np.float64)
    shaped = usable.astype(np.float64).reshape(-1, block)
    return np.sqrt(np.mean(shaped * shaped, axis=1))


def audio_metrics(samples: np.ndarray, sample_rate: int, block_ms: int = 100) -> dict[str, Any]:
    """Return JSON-safe level and clipping measurements for mono int16 audio."""
    pcm = np.asarray(samples, dtype=np.int16).reshape(-1)
    if pcm.size == 0:
        return {
            "duration": 0.0,
            "peak": 0,
            "peak_dbfs": -120.0,
            "rms": 0.0,
            "rms_dbfs": -120.0,
            "clip_count": 0,
            "clip_pct": 0.0,
            "p20_dbfs": -120.0,
            "p90_dbfs": -120.0,
        }

    abs_samples = np.abs(pcm.astype(np.int32))
    peak = int(abs_samples.max(initial=0))
    rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))
    clipped = int(np.count_nonzero(abs_samples >= 32760))
    blocks = block_rms(pcm, sample_rate, block_ms)
    if blocks.size:
        p20 = float(np.percentile(blocks, 20))
        p90 = float(np.percentile(blocks, 90))
    else:
        p20 = rms
        p90 = rms
    return {
        "duration": float(pcm.size) / float(sample_rate),
        "peak": peak,
        "peak_dbfs": dbfs(peak),
        "rms": rms,
        "rms_dbfs": dbfs(rms),
        "clip_count": clipped,
        "clip_pct": 100.0 * clipped / max(1, int(pcm.size)),
        "p20_dbfs": dbfs(p20),
        "p90_dbfs": dbfs(p90),
    }


def calibration_recommendations(
    noise: Optional[dict[str, Any]],
    speech: dict[str, Any],
    *,
    overflow_count: int = 0,
) -> list[str]:
    """Explain level, noise-floor, and capture-health results in plain language."""
    recommendations: list[str] = []
    peak = float(speech.get("peak_dbfs", -120.0))
    p90 = float(speech.get("p90_dbfs", -120.0))
    clip_pct = float(speech.get("clip_pct", 0.0))
    noise_p20 = float(noise.get("p20_dbfs", -120.0)) if noise else None

    if overflow_count:
        recommendations.append(
            f"The input dropped {int(overflow_count)} audio block"
            f"{'s' if int(overflow_count) != 1 else ''}. Try High stream latency before changing gain."
        )

    if clip_pct > 0.001 or peak > -1.0:
        recommendations.append("Lower capture gain: speech is clipping or too close to 0 dBFS.")
    elif peak < -18.0 or p90 < -32.0:
        recommendations.append("Raise capture gain or move the mic closer: speech is arriving quiet.")
    elif -12.0 <= peak <= -3.0 and p90 > -30.0:
        recommendations.append("Speech level looks healthy for wake-word detection and transcription.")
    else:
        recommendations.append("Speech is usable; aim for loud-speech peaks between -12 and -3 dBFS.")

    if noise_p20 is not None:
        separation = p90 - noise_p20
        if noise_p20 > -38.0:
            recommendations.append("The noise floor is high. Reduce room noise or lower gain if speech remains strong.")
        elif noise_p20 < -58.0 and peak < -12.0:
            recommendations.append("The room is quiet and speech is modest; a small gain increase may be safe.")
        if separation < 10.0:
            recommendations.append("Speech is not far above the room noise. Mic placement will help more than software gain.")

    return recommendations


def calibration_adjustments(
    noise: Optional[dict[str, Any]],
    speech: dict[str, Any],
    profile: Optional[dict[str, Any]] = None,
    *,
    overflow_count: int = 0,
    wakeword_enabled: bool = False,
    ptt_enabled: bool = False,
) -> list[dict[str, Any]]:
    """Turn measurements into concrete, non-automatic setup next steps."""
    active_profile = dict(profile or {})
    adjustments: list[dict[str, Any]] = []

    def add(
        code: str,
        title: str,
        detail: str,
        *,
        tone: str,
        setting: Optional[str] = None,
        setting_label: Optional[str] = None,
        current_value: Any = None,
        suggested_value: Any = None,
    ) -> None:
        adjustments.append(
            {
                "code": code,
                "title": title,
                "detail": detail,
                "tone": tone,
                "setting": setting,
                "setting_label": setting_label,
                "current_value": current_value,
                "suggested_value": suggested_value,
            }
        )

    peak = float(speech.get("peak_dbfs", -120.0))
    p90 = float(speech.get("p90_dbfs", -120.0))
    clip_pct = float(speech.get("clip_pct", 0.0))
    latency = str(active_profile.get("stream_latency") or "low").strip().lower()
    mixer_value = active_profile.get("mixer_value")
    managed_hardware_gain = bool(
        active_profile.get("alsa_card")
        and active_profile.get("mixer_control")
        and mixer_value is not None
    )

    if overflow_count:
        if wakeword_enabled and latency != "high":
            add(
                "use_high_latency",
                "Audio blocks were dropped",
                f"The test lost {int(overflow_count)} audio block"
                f"{'s' if int(overflow_count) != 1 else ''}. Set wake-word stream latency to High so the Pi has more scheduling headroom.",
                tone="change",
                setting="stream_latency",
                setting_label="Wake-word stream latency",
                current_value=latency,
                suggested_value="high",
            )
        elif ptt_enabled and not wakeword_enabled:
            add(
                "ptt_overflow_followup",
                "Check for competing microphone capture or high Pi load",
                "PTT calibration already uses High latency. Close other audio processes and inspect CPU load before changing gain.",
                tone="attention",
            )
        else:
            add(
                "overflow_followup",
                "Keep High latency and check competing audio work",
                "The stream already has extra buffering, so another capture process or sustained Pi load is the more likely cause.",
                tone="attention",
            )

    clipped = clip_pct > 0.001 or peak > -1.0
    quiet = peak < -18.0 or p90 < -32.0
    healthy = -12.0 <= peak <= -3.0 and p90 > -30.0 and not clipped
    target_peak = "-12 to -3 dBFS"

    if clipped:
        if clip_pct > 0.001:
            finding = (
                f"Speech peaked at {peak:.1f} dBFS and {clip_pct:.4f}% of samples clipped. "
                f"Loud speech should peak around {target_peak}."
            )
        else:
            finding = (
                f"Speech peaked at {peak:.1f} dBFS, too close to the 0 dBFS clipping limit. "
                f"Loud speech should peak around {target_peak}."
            )
        if managed_hardware_gain:
            suggested_gain = max(0, int(mixer_value) - 1)
            remedy = (
                f" Reduce hardware capture gain from {mixer_value} to {suggested_gain}, "
                "then restart Home Suite and rerun calibration."
                if suggested_gain != int(mixer_value)
                else " The managed hardware gain is already at its minimum; inspect the microphone's own gain controls."
            )
            add(
                "lower_hardware_gain",
                "Speech input is too loud",
                finding + remedy,
                tone="change",
                setting="mixer_value",
                setting_label="Hardware capture gain",
                current_value=mixer_value,
                suggested_value=suggested_gain if suggested_gain != int(mixer_value) else None,
            )
        else:
            add(
                "configure_lower_hardware_gain",
                "Speech input is too loud",
                finding
                + " Clipping happens before software processing. Lower the microphone's hardware control, or configure its ALSA card and mixer control under Capture gain.",
                tone="change",
                setting="alsa_card",
                setting_label="ALSA capture card",
            )
    elif quiet:
        finding = (
            f"Speech peaked at {peak:.1f} dBFS and its normal loud level was {p90:.1f} dBFS. "
            f"That is below the useful range; loud speech should peak around {target_peak}."
        )
        if managed_hardware_gain:
            suggested_gain = min(65535, int(mixer_value) + 1)
            remedy = (
                f" Increase hardware capture gain from {mixer_value} to {suggested_gain}, "
                "then restart Home Suite and rerun calibration."
                if suggested_gain != int(mixer_value)
                else " The managed hardware gain is already at its maximum; move or aim the microphone closer."
            )
            add(
                "raise_hardware_gain",
                "Speech input is too quiet",
                finding + remedy,
                tone="change",
                setting="mixer_value",
                setting_label="Hardware capture gain",
                current_value=mixer_value,
                suggested_value=suggested_gain if suggested_gain != int(mixer_value) else None,
            )
        else:
            add(
                "improve_quiet_input",
                "Speech input is too quiet",
                finding
                + " Move or aim the microphone closer. If it exposes hardware gain, configure that under Capture gain before adding software gain.",
                tone="attention",
                setting="alsa_card",
                setting_label="ALSA capture card",
            )
    elif healthy:
        gain_detail = (
            f"Keep hardware capture gain at {mixer_value}. "
            if managed_hardware_gain
            else "Keep the current input gain. "
        )
        add(
            "keep_capture_gain",
            "Keep the current capture gain",
            gain_detail
            + f"Speech peaked at {peak:.1f} dBFS, within the {target_peak} target, with useful headroom.",
            tone="good",
            current_value=mixer_value if managed_hardware_gain else None,
        )
    elif peak > -3.0:
        suggested_gain = max(0, int(mixer_value) - 1) if managed_hardware_gain else None
        add(
            "fine_tune_gain_down",
            "Speech input is slightly high",
            f"Speech peaked at {peak:.1f} dBFS, above the {target_peak} target. "
            "A one-step hardware-gain reduction adds headroom for louder commands.",
            tone="attention",
            setting="mixer_value" if managed_hardware_gain else "alsa_card",
            setting_label="Hardware capture gain" if managed_hardware_gain else "ALSA capture card",
            current_value=mixer_value if managed_hardware_gain else None,
            suggested_value=suggested_gain if suggested_gain != mixer_value else None,
        )
    else:
        suggested_gain = min(65535, int(mixer_value) + 1) if managed_hardware_gain else None
        add(
            "fine_tune_gain_up",
            "Speech input is slightly low",
            f"Speech peaked at {peak:.1f} dBFS, below the {target_peak} target. "
            "A one-step hardware-gain increase can make normal commands easier to understand.",
            tone="attention",
            setting="mixer_value" if managed_hardware_gain else "alsa_card",
            setting_label="Hardware capture gain" if managed_hardware_gain else "ALSA capture card",
            current_value=mixer_value if managed_hardware_gain else None,
            suggested_value=suggested_gain if suggested_gain != mixer_value else None,
        )

    if noise:
        noise_p20 = float(noise.get("p20_dbfs", -120.0))
        separation = p90 - noise_p20
        if noise_p20 > -38.0:
            add(
                "reduce_noise_floor",
                "Room noise is high",
                f"The measured noise floor was {noise_p20:.1f} dBFS. Aim the microphone toward the speaker, move it away from fans or speakers, or reduce the noise source before raising gain.",
                tone="attention",
            )
        elif separation < 10.0:
            add(
                "improve_speech_separation",
                "Increase speech-to-noise separation",
                "Move or aim the microphone closer. Software gain would amplify the room and speech together, so it will not solve this result.",
                tone="attention",
            )

    return adjustments


def calibration_status(
    noise: Optional[dict[str, Any]],
    speech: dict[str, Any],
    *,
    overflow_count: int = 0,
) -> str:
    """Return healthy, review, or poor for compact UI presentation."""
    peak = float(speech.get("peak_dbfs", -120.0))
    p90 = float(speech.get("p90_dbfs", -120.0))
    clip_pct = float(speech.get("clip_pct", 0.0))
    if overflow_count or clip_pct > 0.001 or peak > -1.0 or peak < -24.0 or p90 < -38.0:
        return "poor"
    if noise and (float(noise.get("p20_dbfs", -120.0)) > -38.0 or p90 - float(noise.get("p20_dbfs", -120.0)) < 10.0):
        return "review"
    if -18.0 <= peak <= -3.0 and p90 > -32.0:
        return "healthy"
    return "review"


def read_audio_stream_segment(
    stream,
    *,
    sample_rate: int,
    seconds: float,
    block_ms: int,
) -> tuple[np.ndarray, int]:
    """Read an exact-duration mono segment from an open sounddevice stream."""
    duration = max(0.25, min(15.0, float(seconds)))
    sample_rate = max(8000, min(192000, int(sample_rate)))
    block_ms = max(10, min(500, int(block_ms)))
    block_samples = max(1, int(round(sample_rate * block_ms / 1000.0)))
    target_samples = max(1, int(round(sample_rate * duration)))
    frames: list[np.ndarray] = []
    captured_samples = 0
    overflow_count = 0

    while captured_samples < target_samples:
        data, overflowed = stream.read(block_samples)
        if overflowed:
            overflow_count += 1
        array = np.asarray(data)
        if array.ndim > 1:
            array = array[:, 0]
        array = array.reshape(-1)
        if array.size == 0:
            raise RuntimeError("The audio input returned an empty block.")
        frames.append(array.copy())
        captured_samples += int(array.size)

    samples = np.concatenate(frames).astype(np.int16, copy=False)
    return samples[:target_samples], overflow_count


def capture_audio_segment(
    profile: dict[str, Any],
    *,
    seconds: float,
    block_ms: int = 100,
    sd_module=None,
) -> tuple[np.ndarray, int, dict[str, Any]]:
    """Capture one mono segment and return samples, overflows, and device info."""
    if sd_module is None:
        import sounddevice as sd_module  # type: ignore

    from audio_input_profile import pick_sounddevice_input_index

    duration = max(0.25, min(15.0, float(seconds)))
    sample_rate = max(8000, min(192000, int(profile.get("sample_rate") or 48000)))
    channels = max(1, min(8, int(profile.get("channels") or 1)))
    block_ms = max(10, min(500, int(block_ms)))
    block_samples = max(1, int(round(sample_rate * block_ms / 1000.0)))
    selected = pick_sounddevice_input_index(sd_module, profile)
    device = selected if selected >= 0 else None
    device_info = dict(
        sd_module.query_devices(device) if device is not None else sd_module.query_devices(kind="input")
    )

    with sd_module.InputStream(
        samplerate=sample_rate,
        device=device,
        channels=channels,
        dtype="int16",
        blocksize=block_samples,
        latency=profile.get("stream_latency", "low"),
    ) as stream:
        samples, overflow_count = read_audio_stream_segment(
            stream,
            sample_rate=sample_rate,
            seconds=duration,
            block_ms=block_ms,
        )
    return samples, overflow_count, {
        "index": device,
        "name": str(device_info.get("name") or "Default input"),
        "sample_rate": sample_rate,
        "channels": channels,
    }
