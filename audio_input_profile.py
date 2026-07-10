"""Resolve microphone profiles and keep hardware capture settings stable.

Profiles allow each HomeSuite device to select a microphone by index or name,
declare its native sample rate/channel count, and optionally enforce an ALSA
capture control. Values come from device-local preferences with environment
overrides, then fall back to conservative defaults. Secrets do not belong here.

``CaptureSettingsGuardian`` periodically rechecks mutable mixer state so a
reboot, USB reconnect, or external mixer change does not silently undo a
calibrated level. Both wakeword and PTT callers may use profile selection, but
each capture path decides independently which preprocessing options to enable.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
from typing import Any, Dict, Optional

try:
    from env_compat import env_get, install_homesuite_env_aliases

    install_homesuite_env_aliases()
except Exception:
    env_get = lambda name, default=None: os.environ.get(name, default)  # type: ignore


_DEFAULT_PROFILE: Dict[str, Any] = {
    "name": "default",
    "device_match": "USB",
    "device_index": None,
    "sample_rate": 48000,
    "channels": 1,
    "strict_device_match": False,
    "alsa_card": None,
    "mixer_control": None,
    "mixer_value": None,
    "verify_interval_sec": 0.0,
    "noise_suppression_level": 0,
    "auto_gain_dbfs": 0,
    "volume_multiplier": 1.0,
    "command_noise_suppression_level": 0,
    "command_auto_gain_dbfs": 0,
    "command_volume_multiplier": 1.0,
    "aec_mode": "none",
}


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_value(name: str) -> Optional[str]:
    try:
        value = env_get(name, None)
    except Exception:
        value = os.environ.get(name)
    if value is None:
        return None
    value = str(value).strip()
    return value if value else None


def get_audio_input_profile() -> Dict[str, Any]:
    """Return the effective profile after local preference and env overrides."""
    """Return the active profile with environment overrides applied."""
    profile = dict(_DEFAULT_PROFILE)
    try:
        import app_config

        configured = getattr(app_config, "AUDIO_INPUT_PROFILE", None)
        if isinstance(configured, dict):
            profile.update(configured)
    except Exception:
        pass

    # Legacy sounddevice hints remain the fallback for devices that have not
    # adopted a named profile. They must not silently pin a named profile to an
    # old microphone after a hardware swap.
    legacy_env_map = {
        "device_match": "PIPHONE_SD_INPUT_MATCH",
        "device_index": "PIPHONE_SD_INPUT_INDEX",
        "sample_rate": "PIPHONE_SD_SAMPLERATE",
    }
    if str(profile.get("name") or "default").strip().lower() == "default":
        for key, env_name in legacy_env_map.items():
            value = _env_value(env_name)
            if value is not None:
                profile[key] = value

    profile_env_map = {
        "device_match": "PIPHONE_MIC_DEVICE_MATCH",
        "device_index": "PIPHONE_MIC_DEVICE_INDEX",
        "sample_rate": "PIPHONE_MIC_SAMPLE_RATE",
        "channels": "PIPHONE_MIC_CHANNELS",
        "alsa_card": "PIPHONE_MIC_ALSA_CARD",
        "mixer_control": "PIPHONE_MIC_MIXER_CONTROL",
        "mixer_value": "PIPHONE_MIC_MIXER_VALUE",
        "verify_interval_sec": "PIPHONE_MIC_VERIFY_INTERVAL_SEC",
    }
    for key, env_name in profile_env_map.items():
        value = _env_value(env_name)
        if value is not None:
            profile[key] = value

    strict = _env_value("PIPHONE_MIC_STRICT_DEVICE_MATCH")
    if strict is not None:
        profile["strict_device_match"] = _as_bool(strict)

    for key in ("device_index", "sample_rate", "channels", "mixer_value"):
        value = profile.get(key)
        if value is None or value == "":
            profile[key] = None if key in ("device_index", "mixer_value") else _DEFAULT_PROFILE[key]
            continue
        try:
            profile[key] = int(value)
        except (TypeError, ValueError):
            pass

    for key in ("verify_interval_sec", "volume_multiplier", "command_volume_multiplier"):
        try:
            profile[key] = float(profile.get(key, _DEFAULT_PROFILE[key]))
        except (TypeError, ValueError):
            profile[key] = _DEFAULT_PROFILE[key]

    for key in (
        "noise_suppression_level",
        "auto_gain_dbfs",
        "command_noise_suppression_level",
        "command_auto_gain_dbfs",
    ):
        try:
            profile[key] = int(profile.get(key, _DEFAULT_PROFILE[key]))
        except (TypeError, ValueError):
            profile[key] = _DEFAULT_PROFILE[key]

    profile["strict_device_match"] = _as_bool(profile.get("strict_device_match"))
    profile["device_match"] = str(profile.get("device_match") or "").strip()
    profile["aec_mode"] = str(profile.get("aec_mode") or "none").strip().lower()
    return profile


def profile_for_log(profile: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        "name",
        "device_match",
        "device_index",
        "sample_rate",
        "channels",
        "strict_device_match",
        "alsa_card",
        "mixer_control",
        "mixer_value",
        "verify_interval_sec",
        "noise_suppression_level",
        "auto_gain_dbfs",
        "volume_multiplier",
        "command_noise_suppression_level",
        "command_auto_gain_dbfs",
        "command_volume_multiplier",
        "aec_mode",
    )
    return {key: profile.get(key) for key in keys}


def pick_sounddevice_input_index(sd_module, profile: Optional[Dict[str, Any]] = None) -> int:
    """Resolve a stable PortAudio input index from an explicit index or name."""
    profile = profile or get_audio_input_profile()
    explicit = profile.get("device_index")
    if explicit is not None and str(explicit).strip() != "":
        index = int(explicit)
        info = sd_module.query_devices(index)
        if int(info.get("max_input_channels", 0) or 0) <= 0:
            raise RuntimeError(f"Configured audio input index {index} has no input channels")
        return index

    match = str(profile.get("device_match") or "").strip().lower()
    if match:
        for index, info in enumerate(sd_module.query_devices()):
            try:
                if int(info.get("max_input_channels", 0) or 0) <= 0:
                    continue
                if match in str(info.get("name") or "").lower():
                    return int(index)
            except Exception:
                continue

    if profile.get("strict_device_match"):
        raise RuntimeError(f"Configured audio input was not found: {profile.get('device_match')!r}")
    return -1


_CAPTURE_VALUE_RE = re.compile(r"(?:Mono|Front Left|Left):\s+Capture\s+(\d+)", re.IGNORECASE)


def _run_amixer(profile: Dict[str, Any], *args: str) -> subprocess.CompletedProcess:
    card = str(profile.get("alsa_card") or "").strip()
    if not card:
        raise RuntimeError("No ALSA card configured")
    return subprocess.run(
        ["amixer", "-c", card, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=2.0,
        check=False,
    )


def read_capture_mixer_value(profile: Optional[Dict[str, Any]] = None) -> Optional[int]:
    profile = profile or get_audio_input_profile()
    control = str(profile.get("mixer_control") or "").strip()
    if not profile.get("alsa_card") or not control:
        return None
    result = _run_amixer(profile, "sget", control)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "amixer sget failed").strip())
    match = _CAPTURE_VALUE_RE.search(result.stdout or "")
    if not match:
        return None
    return int(match.group(1))


def enforce_capture_settings(
    profile: Optional[Dict[str, Any]] = None,
    *,
    logger=None,
    reason: str = "runtime",
    force: bool = False,
) -> bool:
    """Apply and verify the profile's ALSA mixer value when one is configured."""
    """Apply and verify the configured hardware capture level when present."""
    profile = profile or get_audio_input_profile()
    log = logger or logging.getLogger(__name__)
    control = str(profile.get("mixer_control") or "").strip()
    target = profile.get("mixer_value")
    if not profile.get("alsa_card") or not control or target is None:
        return True

    try:
        target_int = int(target)
        current = read_capture_mixer_value(profile)
        if not force and current == target_int:
            return True

        result = _run_amixer(profile, "sset", control, str(target_int))
        if result.returncode != 0:
            log.error(
                "MIC_PROFILE_APPLY_FAIL profile=%r card=%r control=%r target=%r reason=%r err=%r",
                profile.get("name"), profile.get("alsa_card"), control, target_int,
                reason, (result.stderr or result.stdout or "").strip(),
            )
            return False

        verified = read_capture_mixer_value(profile)
        ok = verified == target_int
        log.info(
            "MIC_PROFILE_APPLY profile=%r card=%r control=%r previous=%r target=%r verified=%r ok=%s reason=%r",
            profile.get("name"), profile.get("alsa_card"), control, current,
            target_int, verified, ok, reason,
        )
        return ok
    except Exception as exc:
        log.exception(
            "MIC_PROFILE_APPLY_ERROR profile=%r card=%r control=%r reason=%r err=%r",
            profile.get("name"), profile.get("alsa_card"), control, reason, exc,
        )
        return False


class CaptureSettingsGuardian:
    """Periodically restore a configured mixer value in a daemon thread."""
    """Reassert a hardware capture level if another audio manager changes it."""

    def __init__(self, profile: Optional[Dict[str, Any]] = None, *, logger=None):
        self.profile = profile or get_audio_input_profile()
        self.log = logger or logging.getLogger(__name__)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        interval = max(0.0, float(self.profile.get("verify_interval_sec") or 0.0))
        if interval <= 0 or self.profile.get("mixer_value") is None:
            return False
        enforce_capture_settings(self.profile, logger=self.log, reason="guardian_start")
        self._thread = threading.Thread(
            target=self._run,
            name="mic_profile_guardian",
            daemon=True,
        )
        self._thread.start()
        self.log.info("MIC_PROFILE_GUARDIAN_START interval_sec=%.1f", interval)
        return True

    def _run(self) -> None:
        interval = max(1.0, float(self.profile.get("verify_interval_sec") or 10.0))
        while not self._stop_event.wait(interval):
            enforce_capture_settings(self.profile, logger=self.log, reason="guardian_check")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        self._thread = None
