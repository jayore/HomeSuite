"""Validated audio-profile editing for the Home Suite management console."""

from __future__ import annotations

import ast
import copy
import hmac
import importlib
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from audio_hardware import discover_audio_hardware
from config_editor import (
    ConfigEditError,
    atomic_write_config,
    parse_config_source,
    rewrite_config_assignments,
    source_revision,
)


LOCAL_PREFS_FILE = "local_prefs.py"
PROFILE_KEY = "AUDIO_INPUT_PROFILE"
OUTPUT_KEY = "HOMESUITE_ALSA_DEVICE"
MANAGED_MARKER = "# Home Suite Console managed audio settings"
PROFILE_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


def _literal_assignment(assignments: dict, key: str) -> tuple[bool, Any]:
    nodes = assignments.get(key) or []
    if not nodes:
        return False, None
    try:
        return True, ast.literal_eval(nodes[-1][1])
    except Exception as exc:
        raise ConfigEditError(
            f"{key} in {LOCAL_PREFS_FILE} must be a literal value before the console can edit it.",
            status=409,
        ) from exc


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    raise ConfigEditError("Audio configuration contains a value the console cannot safely edit.", status=409)


def _text(value: Any, label: str, *, required: bool = False, maximum: int = 160) -> Optional[str]:
    if value is None or value == "":
        if required:
            raise ConfigEditError(f"{label} is required.")
        return None
    if not isinstance(value, str):
        raise ConfigEditError(f"{label} must be text.")
    normalized = value.strip()
    if not normalized:
        if required:
            raise ConfigEditError(f"{label} is required.")
        return None
    if len(normalized) > maximum or any(ord(char) < 32 for char in normalized):
        raise ConfigEditError(f"{label} must be {maximum} printable characters or fewer.")
    return normalized


def _integer(value: Any, label: str, minimum: int, maximum: int, *, optional: bool = False) -> Optional[int]:
    if optional and (value is None or value == ""):
        return None
    if isinstance(value, bool):
        raise ConfigEditError(f"{label} must be a whole number.")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigEditError(f"{label} must be a whole number.") from exc
    if normalized < minimum or normalized > maximum:
        raise ConfigEditError(f"{label} must be between {minimum} and {maximum}.")
    return normalized


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ConfigEditError(f"{label} must be a number.")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigEditError(f"{label} must be a number.") from exc
    if normalized < minimum or normalized > maximum:
        raise ConfigEditError(f"{label} must be between {minimum:g} and {maximum:g}.")
    return normalized


def normalize_audio_profile(value: Any) -> dict[str, Any]:
    """Validate the complete profile while retaining future literal fields."""
    if not isinstance(value, dict):
        raise ConfigEditError("The microphone profile must be an object.")
    profile = copy.deepcopy(_json_safe(value))

    name = _text(profile.get("name"), "Profile name", required=True, maximum=64)
    if not PROFILE_NAME_RE.fullmatch(str(name)):
        raise ConfigEditError("Profile name must begin with a letter and use only letters, numbers, dashes, or underscores.")
    profile["name"] = name
    profile["device_match"] = _text(profile.get("device_match"), "Microphone name", maximum=160) or ""
    profile["device_index"] = _integer(
        profile.get("device_index"), "PortAudio device index", 0, 4096, optional=True
    )
    if not profile["device_match"] and profile["device_index"] is None:
        raise ConfigEditError("Choose a microphone name or an explicit PortAudio device index.")

    profile["sample_rate"] = _integer(profile.get("sample_rate"), "Sample rate", 8000, 192000)
    profile["channels"] = _integer(profile.get("channels"), "Input channels", 1, 8)
    latency = profile.get("stream_latency", "low")
    if isinstance(latency, str) and latency.strip().lower() in {"low", "high"}:
        profile["stream_latency"] = latency.strip().lower()
    else:
        profile["stream_latency"] = _number(latency, "Stream latency", 0.001, 5.0)
    profile["strict_device_match"] = bool(profile.get("strict_device_match"))

    profile["alsa_card"] = _text(profile.get("alsa_card"), "ALSA card", maximum=80)
    profile["mixer_control"] = _text(profile.get("mixer_control"), "Mixer control", maximum=80)
    profile["mixer_value"] = _integer(
        profile.get("mixer_value"), "Hardware capture gain", 0, 65535, optional=True
    )
    if profile["mixer_value"] is not None and (not profile["alsa_card"] or not profile["mixer_control"]):
        raise ConfigEditError("Hardware capture gain needs both an ALSA card and mixer control.")
    profile["verify_interval_sec"] = _number(
        profile.get("verify_interval_sec", 0), "Gain verification interval", 0, 3600
    )

    for key, label in (
        ("noise_suppression_level", "Wake-word noise suppression"),
        ("command_noise_suppression_level", "Command noise suppression"),
    ):
        profile[key] = _integer(profile.get(key, 0), label, 0, 4)
    for key, label in (
        ("auto_gain_dbfs", "Wake-word automatic gain target"),
        ("command_auto_gain_dbfs", "Command automatic gain target"),
    ):
        profile[key] = _integer(profile.get(key, 0), label, 0, 31)
    for key, label, maximum in (
        ("volume_multiplier", "Wake-word software gain", 8.0),
        ("command_volume_multiplier", "Command software gain", 8.0),
        ("ptt_volume_multiplier", "PTT software gain", 4.0),
    ):
        profile[key] = _number(profile.get(key, 1.0), label, 0.05, maximum)

    aec_mode = str(profile.get("aec_mode") or "none").strip().lower()
    if aec_mode not in {"none", "hardware"}:
        raise ConfigEditError("Echo cancellation must be None or Hardware.")
    profile["aec_mode"] = aec_mode
    return profile


def normalize_output_override(value: Any) -> Optional[str]:
    return _text(value, "Local playback device", maximum=200)


class AudioConfigEditor:
    """Read, validate, review, and atomically update node-local audio settings."""

    def __init__(
        self,
        *,
        root: Path,
        app_config=None,
        backup_root: Optional[Path] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.app_config = app_config or importlib.import_module("app_config")
        self.backup_root = Path(backup_root or (self.root / "backups" / "console"))
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self.root / LOCAL_PREFS_FILE

    def _source(self) -> tuple[str, bool, dict]:
        exists = self.path.exists()
        source = self.path.read_text(encoding="utf-8") if exists else ""
        assignments = parse_config_source(LOCAL_PREFS_FILE, source)[1]
        return source, exists, assignments

    def _current(self, assignments: dict) -> tuple[dict[str, Any], Optional[str], str]:
        from audio_input_profile import complete_audio_input_profile

        configured = getattr(self.app_config, PROFILE_KEY, None)
        complete_profile = complete_audio_input_profile(
            configured if isinstance(configured, dict) else None
        )
        has_profile, raw_profile = _literal_assignment(assignments, PROFILE_KEY)
        if has_profile:
            if not isinstance(raw_profile, dict):
                raise ConfigEditError(f"{PROFILE_KEY} in {LOCAL_PREFS_FILE} must be a dictionary.")
            complete_profile.update(raw_profile)
            profile = normalize_audio_profile(complete_profile)
            profile_source = "local_prefs"
        else:
            profile = normalize_audio_profile(complete_profile)
            profile_source = "effective_default"

        has_output, raw_output = _literal_assignment(assignments, OUTPUT_KEY)
        output_override = normalize_output_override(raw_output) if has_output else None
        return profile, output_override, profile_source

    def _effective_output(self, output_override: Optional[str]) -> tuple[str, str]:
        if output_override:
            return output_override, "local_prefs"
        configured = str(getattr(self.app_config, OUTPUT_KEY, "") or "").strip()
        if configured:
            return configured, "app_config"
        environment = str(
            os.environ.get("HOMESUITE_ALSA_DEVICE")
            or os.environ.get("PIPHONE_ALSA_DEVICE")
            or ""
        ).strip()
        if environment:
            return environment, "service_environment"
        try:
            result = subprocess.run(
                ["systemctl", "show", "homesuite.service", "-p", "Environment", "--value"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2.0,
                check=False,
            )
            if result.returncode == 0:
                for assignment in shlex.split(str(result.stdout or "")):
                    key, separator, value = assignment.partition("=")
                    if separator and key in {"HOMESUITE_ALSA_DEVICE", "PIPHONE_ALSA_DEVICE"} and value.strip():
                        return value.strip(), "service_environment"
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        return "default", "system_default"

    def public_state(self) -> dict[str, Any]:
        with self._lock:
            source, _exists, assignments = self._source()
            profile, output_override, profile_source = self._current(assignments)
            output_effective, output_source = self._effective_output(output_override)
            return {
                "schema_version": 1,
                "profile": profile,
                "profile_source": profile_source,
                "output_override": output_override,
                "output_effective": output_effective,
                "output_source": output_source,
                "assistant_output_mode": str(
                    getattr(self.app_config, "ASSISTANT_AUDIO_OUTPUT_MODE", "local") or "local"
                ),
                "wakeword_enabled": bool(getattr(self.app_config, "WAKEWORD_ENABLED", False)),
                "ptt_enabled": bool(getattr(self.app_config, "PTT_ENABLED", False)),
                "hardware": discover_audio_hardware(),
                "revision": source_revision(source),
            }

    def _prepare(self, profile: Any, output_override: Any) -> tuple[str, bool, dict, Optional[str], dict, Optional[str], list[dict]]:
        source, exists, assignments = self._source()
        current_profile, current_output, _profile_source = self._current(assignments)
        proposed_profile = normalize_audio_profile(profile)
        proposed_output = normalize_output_override(output_override)
        changes: list[dict[str, Any]] = []
        if current_profile != proposed_profile:
            changed = [key for key in dict.fromkeys([*current_profile.keys(), *proposed_profile.keys()]) if current_profile.get(key) != proposed_profile.get(key)]
            changes.append(
                {
                    "key": PROFILE_KEY,
                    "label": "Microphone profile",
                    "details": ", ".join(key.replace("_", " ") for key in changed[:8])
                    + (f", and {len(changed) - 8} more" if len(changed) > 8 else ""),
                }
            )
        if current_output != proposed_output:
            changes.append(
                {
                    "key": OUTPUT_KEY,
                    "label": "Local playback device",
                    "before": current_output or "Service or system default",
                    "after": proposed_output or "Service or system default",
                }
            )
        return source, exists, current_profile, current_output, proposed_profile, proposed_output, changes

    def preview(self, profile: Any, output_override: Any) -> dict[str, Any]:
        with self._lock:
            source, _exists, _current_profile, _current_output, proposed_profile, proposed_output, changes = self._prepare(profile, output_override)
            return {
                "profile": proposed_profile,
                "output_override": proposed_output,
                "changes": changes,
                "change_count": len(changes),
                "revision": source_revision(source),
                "restart_services": ["homesuite.service"] if changes else [],
            }

    def apply(self, profile: Any, output_override: Any, revision: Any) -> dict[str, Any]:
        with self._lock:
            source, exists, current_profile, current_output, proposed_profile, proposed_output, changes = self._prepare(profile, output_override)
            if not hmac.compare_digest(str(revision or ""), source_revision(source)):
                raise ConfigEditError(
                    f"{LOCAL_PREFS_FILE} changed after this review. Reload Audio and review again.",
                    status=409,
                )
            if not changes:
                return {
                    "applied": False,
                    "changes": [],
                    "change_count": 0,
                    "written_files": [],
                    "restart_services": [],
                    "backup_dir": None,
                }

            updates: dict[str, Any] = {}
            removals: list[str] = []
            if current_profile != proposed_profile:
                updates[PROFILE_KEY] = proposed_profile
            if current_output != proposed_output:
                if proposed_output:
                    updates[OUTPUT_KEY] = proposed_output
                else:
                    removals.append(OUTPUT_KEY)

            base_source = source or '"""Device-local Home Suite preferences."""\n'
            rewritten = rewrite_config_assignments(
                LOCAL_PREFS_FILE,
                base_source,
                updates=updates,
                removals=removals,
                marker=MANAGED_MARKER,
                sort_dicts=False,
            )

            stamp = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
            backup_dir = self.backup_root / stamp
            try:
                self.backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(self.backup_root, 0o700)
                backup_dir.mkdir(mode=0o700, exist_ok=False)
                if exists:
                    shutil.copy2(self.path.resolve(strict=True), backup_dir / LOCAL_PREFS_FILE)
                else:
                    (backup_dir / f"{LOCAL_PREFS_FILE}.absent").write_text(
                        "The file did not exist before this console update.\n", encoding="utf-8"
                    )
            except Exception as exc:
                raise ConfigEditError(
                    "Home Suite could not create a private configuration backup, so nothing was written.",
                    status=500,
                ) from exc

            target = self.path.resolve(strict=True) if exists else self.path
            try:
                atomic_write_config(target, rewritten)
            except Exception as exc:
                try:
                    if exists:
                        atomic_write_config(target, source)
                    else:
                        target.unlink(missing_ok=True)
                except Exception:
                    pass
                raise ConfigEditError(
                    "The audio configuration write failed and was rolled back. Check file permissions and logs.",
                    status=500,
                ) from exc

            return {
                "applied": True,
                "profile": proposed_profile,
                "output_override": proposed_output,
                "changes": changes,
                "change_count": len(changes),
                "written_files": [LOCAL_PREFS_FILE],
                "restart_services": ["homesuite.service"],
                "backup_dir": str(backup_dir),
            }
