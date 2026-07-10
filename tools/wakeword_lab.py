#!/usr/bin/env python3
"""Capture labeled wakeword samples and replay them through the live frontend."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


POSITIVE_MODES = {"wake_only", "one_breath", "paused"}
ALL_MODES = sorted(POSITIVE_MODES | {"negative", "ambient"})


def _service_is_active() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", "homesuite.service"],
            check=False,
            timeout=2.0,
        )
        return result.returncode == 0
    except Exception:
        return False


def _write_wav(path: Path, pcm: np.ndarray, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(int(sample_rate))
        wav_file.writeframes(np.asarray(pcm, dtype=np.int16).reshape(-1).tobytes())


def _read_wav(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as wav_file:
        if wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2:
            raise ValueError("only mono 16-bit PCM WAV files are supported")
        sample_rate = wav_file.getframerate()
        pcm = np.frombuffer(wav_file.readframes(wav_file.getnframes()), dtype=np.int16).copy()
    return int(sample_rate), pcm


def _level_metrics(pcm: np.ndarray) -> dict:
    pcm = np.asarray(pcm, dtype=np.int16).reshape(-1)
    if not pcm.size:
        return {"peak_dbfs": -120.0, "rms_dbfs": -120.0, "clip_pct": 0.0}
    absolute = np.abs(pcm.astype(np.int32))
    peak = int(absolute.max(initial=0))
    rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))

    def dbfs(value: float) -> float:
        return 20.0 * math.log10(max(1.0, value) / 32768.0)

    return {
        "peak_dbfs": round(dbfs(peak), 2),
        "rms_dbfs": round(dbfs(rms), 2),
        "clip_pct": round(100.0 * np.count_nonzero(absolute >= 32760) / pcm.size, 5),
    }


def _capture(args) -> int:
    if _service_is_active() and not args.allow_active_service:
        print("homesuite.service is active and owns the microphone.", file=sys.stderr)
        print("Run `sudo systemctl stop homesuite.service` first.", file=sys.stderr)
        return 2

    import sounddevice as sd

    from audio_input_profile import (
        enforce_capture_settings,
        get_audio_input_profile,
        pick_sounddevice_input_index,
        profile_for_log,
    )

    profile = get_audio_input_profile()
    if args.device is not None:
        try:
            profile["device_index"] = int(args.device)
            profile["device_match"] = ""
        except ValueError:
            profile["device_index"] = None
            profile["device_match"] = args.device
    if args.sample_rate:
        profile["sample_rate"] = int(args.sample_rate)

    enforce_capture_settings(profile, reason="wakeword_lab", force=True)
    device_index = pick_sounddevice_input_index(sd, profile)
    sample_rate = int(profile.get("sample_rate") or 48000)
    device_info = sd.query_devices(device_index if device_index >= 0 else None, kind="input")
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Profile: {profile_for_log(profile)}")
    print(f"Input:   {device_index} ({device_info['name']}) at {sample_rate} Hz")
    print(f"Mode:    {args.mode}")
    if args.phrase:
        print(f"Phrase:  {args.phrase}")

    frame_count = int(round(float(args.seconds) * sample_rate))
    for index in range(1, int(args.count) + 1):
        input(f"\n[{index}/{args.count}] Press Enter when ready...")
        print("Recording now.", flush=True)
        recording = sd.rec(
            frame_count,
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            device=(device_index if device_index >= 0 else None),
        )
        sd.wait()
        pcm = np.asarray(recording, dtype=np.int16).reshape(-1)

        stamp = time.strftime("%Y%m%d_%H%M%S")
        stem = f"{stamp}_{args.mode}_{index:02d}"
        wav_path = output_dir / f"{stem}.wav"
        metadata_path = output_dir / f"{stem}.json"
        metrics = _level_metrics(pcm)
        metadata = {
            "mode": args.mode,
            "positive": args.mode in POSITIVE_MODES,
            "phrase": args.phrase or "",
            "sample_rate": sample_rate,
            "duration_sec": round(pcm.size / sample_rate, 3),
            "device_name": str(device_info.get("name") or ""),
            "profile": profile_for_log(profile),
            **metrics,
        }
        _write_wav(wav_path, pcm, sample_rate)
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(
            f"Saved {wav_path.name}: peak={metrics['peak_dbfs']:.1f} dBFS "
            f"rms={metrics['rms_dbfs']:.1f} dBFS clip={metrics['clip_pct']:.4f}%"
        )

    print(f"\nCaptured {args.count} labeled sample(s) in {output_dir}")
    return 0


def _reset_model(model, frontend) -> None:
    try:
        model.reset()
    except Exception:
        pass
    try:
        vad = getattr(model, "vad", None)
        if vad is not None:
            vad.reset_states()
            vad.prediction_buffer.clear()
    except Exception:
        pass
    frontend.reset()


def _score_file(model, path: Path, profile: dict, selected_label: str) -> dict:
    from wakeword_frontend import WakewordFrontend

    sample_rate, pcm = _read_wav(path)
    frontend = WakewordFrontend(
        sample_rate,
        output_sample_rate=16000,
        output_chunk_samples=1280,
        noise_suppression_level=int(profile.get("noise_suppression_level") or 0),
        auto_gain_dbfs=int(profile.get("auto_gain_dbfs") or 0),
        volume_multiplier=float(profile.get("volume_multiplier") or 1.0),
    )
    _reset_model(model, frontend)

    frame_samples = max(1, int(round(sample_rate / 100.0)))
    padded = np.concatenate((pcm, np.zeros(sample_rate // 2, dtype=np.int16)))
    max_score = 0.0
    score_count = 0
    labels = set()
    for start in range(0, padded.size, frame_samples):
        frame = padded[start : start + frame_samples]
        if frame.size < frame_samples:
            frame = np.pad(frame, (0, frame_samples - frame.size))
        for chunk in frontend.push(frame):
            scores = model.predict(chunk)
            if not isinstance(scores, dict):
                continue
            for label, value in scores.items():
                label = str(label)
                labels.add(label)
                if selected_label and label != selected_label:
                    continue
                max_score = max(max_score, float(value))
                score_count += 1
    return {
        "max_score": max_score,
        "score_count": score_count,
        "labels": sorted(labels),
    }


def _recommend_threshold(results: list[dict]) -> None:
    labeled = [item for item in results if item.get("positive") is not None]
    positives = [item for item in labeled if item["positive"]]
    negatives = [item for item in labeled if not item["positive"]]
    if not positives or not negatives:
        print("\nThreshold recommendation needs both positive and negative samples.")
        return

    candidates = sorted(
        {round(value / 100.0, 2) for value in range(5, 96)}
        | {round(float(item["max_score"]), 3) for item in labeled}
    )
    choices = []
    for threshold in candidates:
        true_positive_rate = sum(item["max_score"] >= threshold for item in positives) / len(positives)
        false_positive_rate = sum(item["max_score"] >= threshold for item in negatives) / len(negatives)
        # Prefer no observed false accepts, then maximize recall and margin.
        rank = (false_positive_rate == 0.0, true_positive_rate, -false_positive_rate, threshold)
        choices.append((rank, threshold, true_positive_rate, false_positive_rate))
    _, threshold, tpr, fpr = max(choices, key=lambda item: item[0])
    print(
        f"\nCandidate threshold: {threshold:.3f} "
        f"(positive recall {tpr:.0%}, observed false accepts {fpr:.0%})"
    )
    print("Treat this as a starting point; collect samples across distance, noise, and speakers.")


def _replay(args) -> int:
    from openwakeword.model import Model

    import app_config
    from audio_input_profile import get_audio_input_profile

    directory = Path(args.input_dir).expanduser().resolve()
    wav_paths = sorted(directory.glob("*.wav"))
    if not wav_paths:
        print(f"No WAV files found in {directory}", file=sys.stderr)
        return 2

    model_paths = [str(path) for path in (getattr(app_config, "WAKEWORD_MODEL_PATHS", []) or [])]
    selected_label = str(args.label or getattr(app_config, "WAKEWORD_MODEL", "") or "").strip()
    vad_threshold = float(getattr(app_config, "WAKEWORD_VAD_THRESHOLD", 0.5))
    live_threshold = float(getattr(app_config, "WAKEWORD_THRESHOLD", 0.5))
    kwargs = {"vad_threshold": vad_threshold}
    if model_paths:
        kwargs["wakeword_model_paths"] = model_paths
    model = Model(**kwargs)
    profile = get_audio_input_profile()

    print(
        f"Replaying {len(wav_paths)} file(s): label={selected_label!r} "
        f"live_threshold={live_threshold:.3f} vad_threshold={vad_threshold:.3f}"
    )
    results = []
    for path in wav_paths:
        metadata_path = path.with_suffix(".json")
        metadata = {}
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        scored = _score_file(model, path, profile, selected_label)
        result = {
            "path": str(path),
            "mode": metadata.get("mode", "unlabeled"),
            "positive": metadata.get("positive"),
            **scored,
        }
        results.append(result)
        expected = "positive" if result["positive"] is True else "negative" if result["positive"] is False else "?"
        outcome = "HIT" if result["max_score"] >= live_threshold else "miss"
        print(
            f"{path.name:45s} mode={result['mode']:11s} expected={expected:8s} "
            f"max={result['max_score']:.3f} {outcome}"
        )

    _recommend_threshold(results)
    if args.json_out:
        output = Path(args.json_out).expanduser().resolve()
        output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a repeatable labeled dataset for Home Suite wakeword tuning.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture", help="Record labeled microphone samples.")
    capture.add_argument("--mode", required=True, choices=ALL_MODES)
    capture.add_argument("--phrase", default="")
    capture.add_argument("--count", type=int, default=5)
    capture.add_argument("--seconds", type=float, default=3.0)
    capture.add_argument("--output-dir", default="~/wakeword_lab")
    capture.add_argument("--device", help="Temporary device index or name match override.")
    capture.add_argument("--sample-rate", type=int)
    capture.add_argument("--allow-active-service", action="store_true", help=argparse.SUPPRESS)
    capture.set_defaults(func=_capture)

    replay = subparsers.add_parser("replay", help="Replay a labeled directory through OpenWakeWord.")
    replay.add_argument("--input-dir", default="~/wakeword_lab")
    replay.add_argument("--label", help="Loaded model label to score; defaults to WAKEWORD_MODEL.")
    replay.add_argument("--json-out")
    replay.set_defaults(func=_replay)

    args = parser.parse_args()
    if getattr(args, "count", 1) < 1 or getattr(args, "seconds", 1.0) <= 0:
        parser.error("--count and --seconds must be positive")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
