#!/usr/bin/env python3
"""Quick, repeatable microphone gain calibration for PiPhone wakeword boxes.

By default this tool applies the configured AUDIO_INPUT_PROFILE first, then
measures the same named device and sample rate used by the wakeword runtime.
Run it when homesuite is stopped, or the service may already own the microphone.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import wave
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import sounddevice as sd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from audio_calibration import (
    audio_metrics,
    calibration_recommendations,
    read_audio_stream_segment,
)


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


def _record(
    label: str,
    *,
    seconds: float,
    sample_rate: int,
    device: Optional[int],
    block_ms: int,
) -> tuple[np.ndarray, int]:
    block_samples = max(1, int(round(sample_rate * block_ms / 1000.0)))
    print(f"\n{label}: recording {seconds:.1f}s...")
    with sd.InputStream(
        samplerate=sample_rate,
        device=device,
        channels=1,
        dtype="int16",
        blocksize=block_samples,
    ) as stream:
        samples, overflow_count = read_audio_stream_segment(
            stream,
            sample_rate=sample_rate,
            seconds=seconds,
            block_ms=block_ms,
        )
    if overflow_count:
        print(f"WARN: input overflow ({overflow_count} blocks)")
    return samples, overflow_count


def _print_metrics(name: str, metrics: dict) -> None:
    print(f"\n{name}")
    print(f"  duration:   {metrics['duration']:.2f}s")
    print(f"  peak:       {metrics['peak']:5d} ({metrics['peak_dbfs']:6.1f} dBFS)")
    print(f"  rms:        {metrics['rms']:7.1f} ({metrics['rms_dbfs']:6.1f} dBFS)")
    print(f"  block p20:  {metrics['p20_dbfs']:6.1f} dBFS")
    print(f"  block p90:  {metrics['p90_dbfs']:6.1f} dBFS")
    print(f"  clipping:   {metrics['clip_count']} samples ({metrics['clip_pct']:.4f}%)")


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
        silence, silence_overflows = _record("Silence/noise floor", seconds=args.silence_seconds, sample_rate=sample_rate, device=device, block_ms=args.block_ms)
        noise = audio_metrics(silence, sample_rate, args.block_ms)
        _print_metrics("Noise floor", noise)
        input("\nSay several real commands at normal distance, then press Enter...")

    speech, speech_overflows = _record("Speech", seconds=args.speech_seconds, sample_rate=sample_rate, device=device, block_ms=args.block_ms)
    speech_m = audio_metrics(speech, sample_rate, args.block_ms)
    _print_metrics("Speech", speech_m)

    if args.wav_out:
        _write_wav(Path(args.wav_out), speech, sample_rate)
        print(f"\nWrote {args.wav_out}")

    print("\nRecommendation")
    total_overflows = speech_overflows + (silence_overflows if not args.non_interactive else 0)
    for line in calibration_recommendations(noise, speech_m, overflow_count=total_overflows):
        print(f"  - {line}")

    if card is not None:
        print(f"\nMixer profile: ALSA card {card}; inspect with `alsamixer -c {card}`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
