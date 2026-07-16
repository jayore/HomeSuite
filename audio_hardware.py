"""Discover stable ALSA audio hardware for the management console."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any


_DEVICE_RE = re.compile(
    r"^card\s+(?P<card>\d+):\s+(?P<card_id>\S+)\s+\[(?P<card_name>[^\]]+)\],\s+"
    r"device\s+(?P<device>\d+):\s+(?P<device_name>.*?)\s+\[(?P<pcm_name>[^\]]+)\]$"
)
_SUBDEVICES_RE = re.compile(r"^\s*Subdevices:\s+(?P<available>\d+)/(?P<total>\d+)\s*$")
_HW_RE = re.compile(r"\(hw:(\d+),(\d+)\)")
_CONTROL_RE = re.compile(r"^Simple mixer control '([^']+)'", re.MULTILINE)


def _run(*args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=3.0,
            check=False,
        )
        return int(result.returncode), str(result.stdout or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


def _parse_devices(text: str, *, direction: str) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        match = _DEVICE_RE.match(line.strip())
        if match:
            raw = match.groupdict()
            card_id = raw["card_id"].strip()
            card_index = int(raw["card"])
            device_index = int(raw["device"])
            current = {
                "direction": direction,
                "card_index": card_index,
                "card_id": card_id,
                "card_name": raw["card_name"].strip(),
                "device_index": device_index,
                "device_name": raw["device_name"].strip(),
                "pcm_name": raw["pcm_name"].strip(),
                "hw": f"hw:CARD={card_id},DEV={device_index}",
                "plughw": f"plughw:CARD={card_id},DEV={device_index}",
                "available_subdevices": None,
                "total_subdevices": None,
                "busy": None,
            }
            devices.append(current)
            continue
        subdevices = _SUBDEVICES_RE.match(line)
        if current is not None and subdevices:
            available = int(subdevices.group("available"))
            total = int(subdevices.group("total"))
            current["available_subdevices"] = available
            current["total_subdevices"] = total
            current["busy"] = total > 0 and available == 0
    return devices


def _sounddevice_catalog() -> list[dict[str, Any]]:
    try:
        import sounddevice as sd

        rows = []
        for index, raw in enumerate(sd.query_devices()):
            info = dict(raw)
            name = str(info.get("name") or "")
            hw = _HW_RE.search(name)
            rows.append(
                {
                    "index": index,
                    "name": name,
                    "card_index": int(hw.group(1)) if hw else None,
                    "device_index": int(hw.group(2)) if hw else None,
                    "input_channels": int(info.get("max_input_channels") or 0),
                    "output_channels": int(info.get("max_output_channels") or 0),
                    "default_sample_rate": int(round(float(info.get("default_samplerate") or 0))),
                    "low_input_latency": float(info.get("default_low_input_latency") or 0),
                    "high_input_latency": float(info.get("default_high_input_latency") or 0),
                }
            )
        return rows
    except Exception:
        return []


def _mixer_controls(card_id: str) -> list[str]:
    code, output = _run("amixer", "-c", str(card_id), "scontrols")
    if code != 0:
        return []
    return list(dict.fromkeys(_CONTROL_RE.findall(output)))


def discover_audio_hardware() -> dict[str, Any]:
    """Return capture/playback devices with stable ALSA identifiers."""
    capture_code, capture_output = _run("arecord", "-l")
    playback_code, playback_output = _run("aplay", "-l")
    inputs = _parse_devices(capture_output, direction="input") if capture_code == 0 else []
    outputs = _parse_devices(playback_output, direction="output") if playback_code == 0 else []
    portaudio = _sounddevice_catalog()

    by_hw = {
        (row["card_index"], row["device_index"]): row
        for row in portaudio
        if row.get("card_index") is not None and row.get("device_index") is not None
    }
    for device in [*inputs, *outputs]:
        row = by_hw.get((device["card_index"], device["device_index"]))
        if row:
            device["portaudio"] = row
            if device["direction"] == "input" and row.get("input_channels"):
                device["channels"] = row["input_channels"]
                device["default_sample_rate"] = row["default_sample_rate"]
            elif device["direction"] == "output" and row.get("output_channels"):
                device["channels"] = row["output_channels"]
                device["default_sample_rate"] = row["default_sample_rate"]
        if device["direction"] == "input":
            device["mixer_controls"] = _mixer_controls(device["card_id"])

    return {
        "available": bool(inputs or outputs),
        "inputs": inputs,
        "outputs": outputs,
        "portaudio": portaudio,
        "errors": {
            "capture": None if capture_code == 0 else capture_output.strip() or "arecord failed",
            "playback": None if playback_code == 0 else playback_output.strip() or "aplay failed",
        },
        "proc_asound_cards": Path("/proc/asound/cards").is_file(),
    }
