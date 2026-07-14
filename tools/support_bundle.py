#!/usr/bin/env python3
"""Create a compact, redacted Home Suite diagnostic bundle for support."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _command_output(args: list[str], *, cwd: Path = ROOT) -> str:
    try:
        result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False, timeout=5)
        return (result.stdout or result.stderr or "").strip()
    except Exception as exc:
        return f"unavailable: {exc}"


def _version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _file_sizes(paths: Iterable[Path]) -> dict[str, int]:
    sizes = {}
    for path in paths:
        try:
            if path.is_file():
                sizes[path.name] = path.stat().st_size
        except OSError:
            continue
    return sizes


def _git_metadata(root: Path = ROOT) -> dict[str, str | bool | None]:
    """Return concise Git metadata, tolerating source archives without a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
        if result.returncode != 0 or result.stdout.strip() != "true":
            return {"commit": None, "branch": None, "worktree_dirty": None}
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
        return {
            "commit": _command_output(["git", "rev-parse", "HEAD"], cwd=root),
            "branch": _command_output(["git", "branch", "--show-current"], cwd=root),
            "worktree_dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
        }
    except Exception:
        return {"commit": None, "branch": None, "worktree_dirty": None}


def build_summary() -> dict:
    """Return useful facts without copying tokens, raw logs, or utterance text."""
    from tools.doctor import Doctor

    doctor = Doctor(live=False)
    roles = doctor.active_roles()
    rooms = doctor.pref("ROOMS", {}) or {}
    integration_keys = {
        "plex": ("PLEX_URL", "PLEX_TOKEN"),
        "spotify": ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN"),
        "telegram": ("TELEGRAM_BOT_TOKEN",),
        "alpaca": ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"),
        "youtube": ("YOUTUBE_OAUTH_CLIENT_ID", "YOUTUBE_OAUTH_CLIENT_SECRET", "YOUTUBE_OAUTH_REFRESH_TOKEN"),
        "uptime_kuma": ("UPTIME_KUMA_URL", "UPTIME_KUMA_STATUS_PAGE_SLUG"),
    }
    integrations = {
        label: all(doctor.has_value(doctor.value(key)) for key in keys)
        for label, keys in integration_keys.items()
    }
    logs_dir = ROOT / "logs"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": "Home Suite",
        "python": sys.version,
        "platform": platform.platform(),
        "git": _git_metadata(),
        "roles": roles,
        "config_files": {
            name: (ROOT / name).exists()
            for name in ("private_config.py", "deployment_config.py", "local_prefs.py")
        },
        "room_count": len(rooms) if isinstance(rooms, dict) else 0,
        "integrations_configured": integrations,
        "package_versions": {
            name: _version(name)
            for name in ("aiohttp", "openai", "numpy", "requests", "sounddevice", "openwakeword", "onnxruntime")
        },
        "log_sizes_bytes": _file_sizes([ROOT / "homesuite.log", logs_dir / "events.jsonl"]),
        "service_state": _command_output(["systemctl", "is-active", "homesuite.service"]),
        "privacy": {
            "event_log_enabled": bool(doctor.pref("COMMAND_EVENT_LOG_ENABLED", True)),
            "event_log_stores_text": bool(doctor.pref("COMMAND_EVENT_LOG_STORE_TEXT", False)),
        },
    }


def write_bundle(output: Path, *, live: bool) -> Path:
    from tools.doctor import Doctor

    output.parent.mkdir(parents=True, exist_ok=True)
    doctor = Doctor(live=live, json_output=True)
    doctor.run(report=False)
    payload = {
        "ok": not doctor.required_failures(),
        "roles": doctor.role_summary(),
        "checks": [check.__dict__ for check in doctor.relevant_checks()],
    }

    with tempfile.TemporaryDirectory(prefix="homesuite-support-") as tmp:
        staging = Path(tmp)
        (staging / "summary.json").write_text(json.dumps(build_summary(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (staging / "doctor.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (staging / "README.txt").write_text(
            "This bundle intentionally excludes private_config.py values, local config values, raw logs, and command text.\n",
            encoding="utf-8",
        )
        with tarfile.open(output, "w:gz") as archive:
            for path in sorted(staging.iterdir()):
                archive.add(path, arcname=path.name)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a redacted Home Suite support bundle.")
    parser.add_argument("--live", action="store_true", help="include Doctor's bounded live reachability checks")
    parser.add_argument("--output", type=Path, help="output .tar.gz path (defaults to backups/support-<timestamp>.tar.gz)")
    args = parser.parse_args(argv)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = args.output or ROOT / "backups" / f"homesuite-support-{timestamp}.tar.gz"
    output = output.expanduser().resolve()
    if output.suffixes[-2:] != [".tar", ".gz"]:
        parser.error("--output must end in .tar.gz")
    write_bundle(output, live=args.live)
    print(f"Created redacted support bundle: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
