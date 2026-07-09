#!/usr/bin/env python3
"""First-run configuration checks for Home Suite.

The default mode checks files and required config values without contacting
external services. Pass --live for safe reachability checks against services
that are already configured.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class Check:
    group: str
    status: str
    label: str
    detail: str = ""
    required: bool = False


class Doctor:
    def __init__(self, *, live: bool = False, timeout: float = 5.0) -> None:
        self.live = live
        self.timeout = timeout
        self.checks: list[Check] = []
        self.private_config = self._load_module("private_config")
        self.local_prefs = self._load_module("local_prefs")

    def _load_module(self, name: str):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:
                required = name == "private_config"
                self.add("Config files", "FAIL" if required else "WARN", f"{name}.py imports", str(exc), required=required)
            return None
        except Exception as exc:
            required = name == "private_config"
            self.add("Config files", "FAIL" if required else "WARN", f"{name}.py imports", str(exc), required=required)
            return None

    def add(self, group: str, status: str, label: str, detail: str = "", *, required: bool = False) -> None:
        self.checks.append(Check(group=group, status=status, label=label, detail=detail, required=required))

    def value(self, name: str, default=""):
        if self.private_config is None:
            return default
        return getattr(self.private_config, name, default)

    @staticmethod
    def has_value(value) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple, set, dict)):
            return bool(value)
        return True

    def missing(self, names: Iterable[str]) -> list[str]:
        return [name for name in names if not self.has_value(self.value(name, ""))]

    def configured(self, names: Iterable[str]) -> bool:
        return not self.missing(names)

    def run(self) -> int:
        self.check_local_files()
        self.check_core_config()
        self.check_optional_config()
        if self.live:
            self.check_live_services()
        else:
            self.add("Live checks", "SKIP", "network reachability", "Run homesuite-doctor --live to test configured services.")
        self.print_report()
        return 1 if any(c.required and c.status == "FAIL" for c in self.checks) else 0

    def check_local_files(self) -> None:
        private_path = ROOT / "private_config.py"
        prefs_path = ROOT / "local_prefs.py"
        self.add(
            "Config files",
            "OK" if private_path.exists() else "FAIL",
            "private_config.py",
            "found" if private_path.exists() else "missing; copy private_config.example.py",
            required=True,
        )
        self.add(
            "Config files",
            "OK" if prefs_path.exists() else "WARN",
            "local_prefs.py",
            "found" if prefs_path.exists() else "missing; copy local_prefs.example.py for device-specific settings",
        )

    def check_core_config(self) -> None:
        core = {
            "OpenAI API key": ["OPENAI_API_KEY"],
            "Home Assistant": ["HA_URL", "HA_TOKEN"],
        }
        for label, names in core.items():
            missing = self.missing(names)
            self.add(
                "Core",
                "FAIL" if missing else "OK",
                label,
                "missing: " + ", ".join(missing) if missing else "configured",
                required=True,
            )

        api_key = self.value("HOMESUITE_HTTP_API_KEY") or self.value("PIPHONE_HTTP_API_KEY")
        self.add(
            "Core",
            "OK" if self.has_value(api_key) else "WARN",
            "Home Suite HTTP API key",
            "configured" if self.has_value(api_key) else "missing; needed for HTTP/WebSocket clients",
        )

    def optional_group(self, label: str, names: list[str], *, warn_detail: Optional[str] = None) -> None:
        missing = self.missing(names)
        if missing:
            detail = "not configured; missing " + ", ".join(missing)
            if warn_detail:
                detail += f"; {warn_detail}"
            self.add("Optional integrations", "SKIP", label, detail)
        else:
            self.add("Optional integrations", "OK", label, "configured")

    def check_optional_config(self) -> None:
        self.optional_group("Plex", ["PLEX_URL", "PLEX_TOKEN"])
        self.optional_group("Spotify Web API", ["SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN"])
        self.optional_group("YouTube OAuth", ["YOUTUBE_OAUTH_CLIENT_ID", "YOUTUBE_OAUTH_CLIENT_SECRET", "YOUTUBE_OAUTH_REFRESH_TOKEN"])
        self.optional_group("Uptime Kuma status page", ["UPTIME_KUMA_URL", "UPTIME_KUMA_STATUS_PAGE_SLUG"])
        self.optional_group("qBittorrent", ["QBITTORRENT_URL", "QBITTORRENT_USERNAME", "QBITTORRENT_PASSWORD"])
        self.optional_group("Seerr", ["SEERR_URL", "SEERR_API_KEY"])
        self.optional_group("Radarr", ["RADARR_URL", "RADARR_API_KEY"])
        self.optional_group("Sonarr", ["SONARR_URL", "SONARR_API_KEY"])
        self.optional_group("Lidarr", ["LIDARR_URL", "LIDARR_API_KEY"])
        self.optional_group("Porcupine wake word", ["PVPORCUPINE_ACCESS_KEY"])

        telegram_missing = self.missing(["TELEGRAM_BOT_TOKEN"])
        if telegram_missing:
            self.add("Optional integrations", "SKIP", "Telegram", "not configured; missing TELEGRAM_BOT_TOKEN")
        else:
            allowlists = self.has_value(self.value("TELEGRAM_ALLOWED_USER_IDS")) or self.has_value(self.value("TELEGRAM_ALLOWED_CHAT_IDS"))
            self.add(
                "Optional integrations",
                "OK" if allowlists else "WARN",
                "Telegram",
                "configured" if allowlists else "token configured, but allowlists are empty",
            )

    @staticmethod
    def clean_url(url: str) -> str:
        return (url or "").strip().rstrip("/") + "/"

    def get_url(self, url: str, *, headers: Optional[dict] = None) -> tuple[int, str]:
        req = Request(url, headers=headers or {})
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                body = resp.read(2048).decode("utf-8", "replace")
                return int(resp.status), body
        except HTTPError as exc:
            body = exc.read(2048).decode("utf-8", "replace")
            return int(exc.code), body
        except URLError as exc:
            raise RuntimeError(str(exc.reason)) from exc

    def check_live_services(self) -> None:
        self.check_home_assistant_live()
        self.check_uptime_kuma_live()
        self.check_plex_live()
        self.check_telegram_live()

    def check_home_assistant_live(self) -> None:
        if not self.configured(["HA_URL", "HA_TOKEN"]):
            self.add("Live checks", "SKIP", "Home Assistant reachable", "missing HA_URL or HA_TOKEN")
            return
        url = urljoin(self.clean_url(str(self.value("HA_URL"))), "api/")
        try:
            status, _body = self.get_url(url, headers={"Authorization": f"Bearer {self.value('HA_TOKEN')}"})
            self.add(
                "Live checks",
                "OK" if status == 200 else "FAIL",
                "Home Assistant reachable",
                f"HTTP {status}",
                required=True,
            )
        except Exception as exc:
            self.add("Live checks", "FAIL", "Home Assistant reachable", str(exc), required=True)

    def check_uptime_kuma_live(self) -> None:
        if not self.configured(["UPTIME_KUMA_URL", "UPTIME_KUMA_STATUS_PAGE_SLUG"]):
            self.add("Live checks", "SKIP", "Uptime Kuma status page", "not configured")
            return
        slug = quote(str(self.value("UPTIME_KUMA_STATUS_PAGE_SLUG")).strip("/"))
        url = urljoin(self.clean_url(str(self.value("UPTIME_KUMA_URL"))), f"status/{slug}")
        try:
            status, _body = self.get_url(url)
            self.add("Live checks", "OK" if status == 200 else "WARN", "Uptime Kuma status page", f"HTTP {status}")
        except Exception as exc:
            self.add("Live checks", "WARN", "Uptime Kuma status page", str(exc))

    def check_plex_live(self) -> None:
        if not self.configured(["PLEX_URL", "PLEX_TOKEN"]):
            self.add("Live checks", "SKIP", "Plex reachable", "not configured")
            return
        url = urljoin(self.clean_url(str(self.value("PLEX_URL"))), "identity")
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}X-Plex-Token={quote(str(self.value('PLEX_TOKEN')))}"
        try:
            status, _body = self.get_url(url)
            self.add("Live checks", "OK" if status == 200 else "WARN", "Plex reachable", f"HTTP {status}")
        except Exception as exc:
            self.add("Live checks", "WARN", "Plex reachable", str(exc))

    def check_telegram_live(self) -> None:
        if not self.configured(["TELEGRAM_BOT_TOKEN"]):
            self.add("Live checks", "SKIP", "Telegram bot", "not configured")
            return
        url = f"https://api.telegram.org/bot{quote(str(self.value('TELEGRAM_BOT_TOKEN')))}/getMe"
        try:
            status, body = self.get_url(url)
            ok = False
            try:
                ok = bool(json.loads(body).get("ok"))
            except Exception:
                ok = False
            self.add("Live checks", "OK" if status == 200 and ok else "WARN", "Telegram bot", f"HTTP {status}")
        except Exception as exc:
            self.add("Live checks", "WARN", "Telegram bot", str(exc))

    def print_report(self) -> None:
        print("Home Suite doctor")
        print("================")
        current_group = None
        for check in self.checks:
            if check.group != current_group:
                current_group = check.group
                print()
                print(current_group)
            detail = f" - {check.detail}" if check.detail else ""
            print(f"[{check.status}] {check.label}{detail}")

        required_failures = [c for c in self.checks if c.required and c.status == "FAIL"]
        warnings = [c for c in self.checks if c.status == "WARN"]
        print()
        if required_failures:
            print(f"Result: FAIL ({len(required_failures)} required check(s) failed, {len(warnings)} warning(s)).")
        elif warnings:
            print(f"Result: OK with warnings ({len(warnings)} warning(s)).")
        else:
            print("Result: OK.")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check Home Suite first-run configuration.")
    parser.add_argument("--live", action="store_true", help="also test reachability for configured services")
    parser.add_argument("--timeout", type=float, default=5.0, help="per-request timeout for --live checks")
    args = parser.parse_args(argv)
    return Doctor(live=args.live, timeout=args.timeout).run()


if __name__ == "__main__":
    raise SystemExit(main())
