#!/usr/bin/env python3
"""Quick, repeatable microphone gain calibration for PiPhone wakeword boxes.

By default this tool applies the configured AUDIO_INPUT_PROFILE first, then
measures the same named device and sample rate used by the wakeword runtime.
Run it when homesuite is stopped, or the service may already own the microphone.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import sounddevice as sd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


INT16_MAX = 32768.0


def _dbfs(value: float) -> float:
    value = max(float(value or 0.0), 1.0)
    return 20.0 * math.log10(value / INT16_MAX)


def _pick_device(device: Optional[str], match: str) -> Optional[int]:
    if device not in (None, ""):
        try:
            return int(str(device).strip())
        except ValueError:
            wanted = str(device).strip().lower()
            for idx, info in enumerate(sd.query_devices()):
                if wanted in str(info.get("name", "")).lower():
                    return idx
            raise SystemExit(f"No input device matching {device!r}")

    wanted = (match or "").strip().lower()
    if wanted:
        for idx, info in enumerate(sd.query_devices()):
            if int(info.get("max_input_channels") or 0) <= 0:
                continue
            if wanted in str(info.get("name", "")).lower():
                return idx
    return None


def _device_card_index(info: dict) -> Optional[int]:
    text = str(info.get("name", ""))
    m = re.search(r"\(hw:(\d+),\d+\)", text)
    if m:
        return int(m.group(1))
    return None


def _record(label: str, *, seconds: float, sample_rate: int, device: Optional[int], block_ms: int) -> np.ndarray:
    frames = []
    block_samples = max(1, int(round(sample_rate * block_ms / 1000.0)))
    deadline = time.monotonic() + float(seconds)
    print(f"\n{label}: recording {seconds:.1f}s...")
    with sd.InputStream(
        samplerate=sample_rate,
        device=device,
        channels=1,
        dtype="int16",
        blocksize=block_samples,
    ) as stream:
        while time.monotonic() < deadline:
            data, overflowed = stream.read(block_samples)
            if overflowed:
                print("WARN: input overflow")
            frames.append(np.asarray(data).reshape(-1).copy())
    if not frames:
        return np.zeros(0, dtype=np.int16)
    return np.concatenate(frames).astype(np.int16, copy=False)


def _block_rms(samples: np.ndarray, sample_rate: int, block_ms: int) -> np.ndarray:
    block = max(1, int(round(sample_rate * block_ms / 1000.0)))
    if len(samples) < block:
        return np.array([], dtype=np.float64)
    usable = samples[: len(samples) - (len(samples) % block)]
    if usable.size == 0:
        return np.array([], dtype=np.float64)
    shaped = usable.astype(np.float64).reshape(-1, block)
    return np.sqrt(np.mean(shaped * shaped, axis=1))


def _metrics(samples: np.ndarray, sample_rate: int, block_ms: int) -> dict:
    if samples.size == 0:
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
    abs_samples = np.abs(samples.astype(np.int32))
    peak = int(abs_samples.max(initial=0))
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    clipped = int(np.count_nonzero(abs_samples >= 32760))
    block_rms = _block_rms(samples, sample_rate, block_ms)
    if block_rms.size:
        p20 = float(np.percentile(block_rms, 20))
        p90 = float(np.percentile(block_rms, 90))
    else:
        p20 = rms
        p90 = rms
    return {
        "duration": float(samples.size) / float(sample_rate),
        "peak": peak,
        "peak_dbfs": _dbfs(peak),
        "rms": rms,
        "rms_dbfs": _dbfs(rms),
        "clip_count": clipped,
        "clip_pct": 100.0 * clipped / max(1, int(samples.size)),
        "p20_dbfs": _dbfs(p20),
        "p90_dbfs": _dbfs(p90),
    }


def _print_metrics(name: str, metrics: dict) -> None:
    print(f"\n{name}")
    print(f"  duration:   {metrics['duration']:.2f}s")
    print(f"  peak:       {metrics['peak']:5d} ({metrics['peak_dbfs']:6.1f} dBFS)")
    print(f"  rms:        {metrics['rms']:7.1f} ({metrics['rms_dbfs']:6.1f} dBFS)")
    print(f"  block p20:  {metrics['p20_dbfs']:6.1f} dBFS")
    print(f"  block p90:  {metrics['p90_dbfs']:6.1f} dBFS")
    print(f"  clipping:   {metrics['clip_count']} samples ({metrics['clip_pct']:.4f}%)")


def _recommend(noise: Optional[dict], speech: dict) -> list[str]:
    out = []
    peak = speech["peak_dbfs"]
    p90 = speech["p90_dbfs"]
    clip_pct = speech["clip_pct"]
    noise_p20 = noise["p20_dbfs"] if noise else None

    if clip_pct > 0.001 or peak > -1.0:
        out.append("Lower capture gain: speech is clipping or too close to 0 dBFS.")
    elif peak < -18.0 or p90 < -32.0:
        out.append("Raise capture gain or move the mic closer: speech is arriving quiet.")
    elif -12.0 <= peak <= -3.0 and p90 > -30.0:
        out.append("Speech level looks healthy for wakeword/STT.")
    else:
        out.append("Speech level is usable, but try to land loud speech peaks around -12 to -3 dBFS.")

    if noise_p20 is not None:
        if noise_p20 > -38.0:
            out.append("Noise floor is high; lowering gain or reducing room noise may help wakeword false hits.")
        elif noise_p20 < -58.0 and peak < -12.0:
            out.append("Room is quiet and speech is modest; a small gain increase may be safe.")

    return out


def _write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(samples.astype(np.int16, copy=False).tobytes())


def _run_amixer(card) -> None:
    if card is None:
        return
    try:
        out = subprocess.check_output(
            ["amixer", "-c", str(card), "scontents"],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=3,
        )
    except Exception as exc:
        print(f"\nALSA mixer: unavailable ({exc})")
        return

    interesting = []
    keep = False
    for line in out.splitlines():
        if line.startswith("Simple mixer control"):
            keep = any(word in line.lower() for word in ("mic", "capture", "input"))
        if keep:
            interesting.append(line)
    if interesting:
        print("\nALSA capture controls")
        print("\n".join(interesting[:80]))


def main(argv: Optional[Iterable[str]] = None) -> int:
    from audio_input_profile import (
        enforce_capture_settings,
        get_audio_input_profile,
        pick_sounddevice_input_index,
        profile_for_log,
    )

    profile = get_audio_input_profile()
    parser = argparse.ArgumentParser(description="Measure microphone gain for PiPhone wakeword tuning.")
    parser.add_argument("--list-devices", action="store_true", help="Print sounddevice devices and exit.")
    parser.add_argument("--device", help="PortAudio input device index or name substring.")
    parser.add_argument("--match", help="Temporary device name match override.")
    parser.add_argument("--samplerate", type=int, help="Temporary sample-rate override.")
    parser.add_argument("--silence-seconds", type=float, default=3.0)
    parser.add_argument("--speech-seconds", type=float, default=5.0)
    parser.add_argument("--block-ms", type=int, default=100)
    parser.add_argument("--non-interactive", action="store_true", help="Record one speech segment only.")
    parser.add_argument("--wav-out", help="Optional path to save the captured speech WAV.")
    parser.add_argument("--show-alsa", action="store_true", help="Print relevant amixer capture controls.")
    parser.add_argument("--no-apply-profile", action="store_true", help="Measure without enforcing the profile mixer setting first.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.list_devices:
        print(sd.query_devices())
        return 0

    if args.match:
        profile["device_match"] = args.match
        profile["device_index"] = None
    if args.samplerate:
        profile["sample_rate"] = int(args.samplerate)
    if not args.no_apply_profile:
        enforce_capture_settings(profile, reason="mic_calibration", force=True)

    if args.device is not None:
        device = _pick_device(args.device, str(profile.get("device_match") or ""))
    else:
        selected = pick_sounddevice_input_index(sd, profile)
        device = selected if selected >= 0 else None
    sample_rate = int(profile.get("sample_rate") or 48000)
    info = sd.query_devices(device) if device is not None else sd.query_devices(kind="input")
    print(f"Profile: {profile_for_log(profile)}")
    print(f"Selected input: {device if device is not None else 'default'} — {info.get('name')}")
    print("Tip: stop homesuite.service first if the microphone is busy.")

    card = profile.get("alsa_card") or _device_card_index(info)
    if args.show_alsa:
        _run_amixer(card)

    noise = None
    if not args.non_interactive:
        input("\nStay quiet near the mic, then press Enter...")
        silence = _record("Silence/noise floor", seconds=args.silence_seconds, sample_rate=sample_rate, device=device, block_ms=args.block_ms)
        noise = _metrics(silence, sample_rate, args.block_ms)
        _print_metrics("Noise floor", noise)
        input("\nSay several real commands at normal distance, then press Enter...")

    speech = _record("Speech", seconds=args.speech_seconds, sample_rate=sample_rate, device=device, block_ms=args.block_ms)
    speech_m = _metrics(speech, sample_rate, args.block_ms)
    _print_metrics("Speech", speech_m)

    if args.wav_out:
        _write_wav(Path(args.wav_out), speech, sample_rate)
        print(f"\nWrote {args.wav_out}")

    print("\nRecommendation")
    for line in _recommend(noise, speech_m):
        print(f"  - {line}")

    if card is not None:
        print(f"\nMixer profile: ALSA card {card}; inspect with `alsamixer -c {card}`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
