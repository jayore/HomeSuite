#!/usr/bin/env python3
"""First-run configuration checks for Home Suite.

The default mode checks files and required config values without contacting
external services. Pass --live for safe reachability checks against services
that are already configured.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import re
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
    roles: tuple[str, ...] = ()


class Doctor:
    """Validate configuration, enabled node roles, and optional live topology."""

    ROLE_ORDER = ("text", "api", "ptt", "wakeword")

    def __init__(
        self,
        *,
        live: bool = False,
        timeout: float = 5.0,
        requested_roles: Optional[Iterable[str]] = None,
        json_output: bool = False,
    ) -> None:
        self.live = live
        self.timeout = timeout
        self.requested_roles = tuple(requested_roles or ())
        self.json_output = json_output
        self.checks: list[Check] = []
        self.private_config = self._load_module("private_config")
        self.app_config = self._load_module("app_config")
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

    def add(
        self,
        group: str,
        status: str,
        label: str,
        detail: str = "",
        *,
        required: bool = False,
        roles: Iterable[str] = (),
    ) -> None:
        self.checks.append(
            Check(
                group=group,
                status=status,
                label=label,
                detail=detail,
                required=required,
                roles=tuple(roles),
            )
        )

    def value(self, name: str, default=""):
        if self.private_config is None:
            return default
        return getattr(self.private_config, name, default)

    def pref(self, name: str, default=None):
        if self.app_config is None:
            return default
        return getattr(self.app_config, name, default)

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

    @staticmethod
    def module_available(name: str) -> bool:
        """Return whether an optional runtime module can be imported safely."""
        try:
            return importlib.util.find_spec(name) is not None
        except (ModuleNotFoundError, ValueError):
            return False

    def active_roles(self) -> tuple[str, ...]:
        """Return node roles requested by the user or implied by preferences."""
        requested = tuple(getattr(self, "requested_roles", ()) or ())
        if requested and "all" not in requested:
            return tuple(role for role in self.ROLE_ORDER if role in requested)

        roles = {"text"}
        if bool(self.pref("UNIFIED_SERVER_ENABLED", True)):
            roles.add("api")
        if bool(self.pref("PTT_ENABLED", False)):
            roles.add("ptt")
        if bool(self.pref("WAKEWORD_ENABLED", False)):
            roles.add("wakeword")
        return tuple(role for role in self.ROLE_ORDER if role in roles)

    def run(self, *, report: bool = True) -> int:
        self._active_roles = self.active_roles()
        self.check_local_files()
        self.check_core_config()
        self.check_room_registry()
        self.check_room_brightness()
        self.check_local_runtime()
        self.check_optional_config()
        if self.live:
            self.check_live_services()
            self.check_live_topology()
            self.check_live_api_listener()
        else:
            self.add(
                "Live checks",
                "SKIP",
                "network reachability",
                "Run homesuite doctor --live to test configured services and configured HA entities.",
                roles=self._active_roles,
            )
        if report:
            self.print_report()
        return 1 if self.required_failures() else 0

    def check_local_files(self) -> None:
        private_path = ROOT / "private_config.py"
        prefs_path = ROOT / "local_prefs.py"
        self.add(
            "Config files",
            "OK" if private_path.exists() else "FAIL",
            "private_config.py",
            "found" if private_path.exists() else "missing; copy private_config.example.py",
            required=True,
            roles=self.ROLE_ORDER,
        )
        self.add(
            "Config files",
            "OK" if prefs_path.exists() else "WARN",
            "local_prefs.py",
            "found" if prefs_path.exists() else "missing; copy local_prefs.example.py for device-specific settings",
        )
        deployment_path = ROOT / "deployment_config.py"
        self.add(
            "Config files",
            "OK" if deployment_path.exists() else "WARN",
            "deployment_config.py",
            (
                "found"
                if deployment_path.exists()
                else "missing; using tracked app_config.py topology (see room migration guide)"
            ),
            roles=self.ROLE_ORDER,
        )

    def check_core_config(self) -> None:
        core = {"Home Assistant": ["HA_URL", "HA_TOKEN"]}
        for label, names in core.items():
            missing = self.missing(names)
            self.add(
                "Core",
                "FAIL" if missing else "OK",
                label,
                "missing: " + ", ".join(missing) if missing else "configured",
                required=True,
                roles=self.ROLE_ORDER,
            )

        openai_key = self.value("OPENAI_API_KEY")
        voice_enabled = bool(
            self.pref("PTT_ENABLED", False) or self.pref("WAKEWORD_ENABLED", False)
        )
        if self.has_value(openai_key):
            self.add("Core", "OK", "OpenAI API key", "configured", roles=("ptt", "wakeword"))
        else:
            self.add(
                "Core",
                "FAIL" if voice_enabled else "WARN",
                "OpenAI API key",
                (
                    "missing; required by the enabled voice transcription path"
                    if voice_enabled
                    else "missing; deterministic text commands work, conversation and OpenAI speech do not"
                ),
                required=voice_enabled,
                roles=("ptt", "wakeword"),
            )

        server_enabled = bool(self.pref("UNIFIED_SERVER_ENABLED", True))
        api_key = self.value("HOMESUITE_HTTP_API_KEY") or self.value("PIPHONE_HTTP_API_KEY")
        if not server_enabled:
            api_requested = "api" in tuple(getattr(self, "_active_roles", self.active_roles()))
            self.add(
                "Core",
                "FAIL" if api_requested and bool(getattr(self, "requested_roles", ())) else "SKIP",
                "Home Suite HTTP/WebSocket API",
                "disabled in preferences",
                required=api_requested and bool(getattr(self, "requested_roles", ())),
                roles=("api",),
            )
        else:
            self.add(
                "Core",
                "OK" if self.has_value(api_key) else "FAIL",
                "Home Suite HTTP/WebSocket API key",
                "configured" if self.has_value(api_key) else "missing; server is enabled and fails closed without it",
                required=True,
                roles=("api",),
            )

    def check_room_registry(self) -> None:
        rooms = self.pref("ROOMS", {}) or {}
        default_room = str(self.pref("DEFAULT_ROOM", "") or "").strip()
        if not isinstance(rooms, dict) or not rooms:
            self.add("Rooms", "FAIL", "room registry", "ROOMS is empty", required=True, roles=self.ROLE_ORDER)
            return
        if not default_room or default_room not in rooms:
            self.add(
                "Rooms",
                "FAIL",
                "default room",
                f"DEFAULT_ROOM {default_room!r} is not present in ROOMS",
                required=True,
                roles=self.ROLE_ORDER,
            )
            return
        self.add("Rooms", "OK", "default room", default_room, roles=self.ROLE_ORDER)

    def check_room_brightness(self) -> None:
        try:
            from home_registry import ROOMS
            from room_brightness import get_room_brightness_target
        except Exception as exc:
            self.add("Rooms", "WARN", "brightness strategies", f"could not load: {exc}")
            return

        for room_id, room in (ROOMS or {}).items():
            defaults = (room or {}).get("defaults") or {}
            explicit_target = defaults.get("brightness_target")
            configured = (
                "brightness_target" in defaults
                or bool(defaults.get("brightness_number"))
                or bool(defaults.get("brightness_light"))
            )
            target = get_room_brightness_target(room_id)
            label = f"{room_id} brightness"
            if not target:
                explicitly_disabled = (
                    "brightness_target" in defaults
                    and explicit_target is None
                    and not defaults.get("brightness_number")
                    and not defaults.get("brightness_light")
                )
                self.add(
                    "Rooms",
                    "SKIP" if explicitly_disabled or not configured else "WARN",
                    label,
                    (
                        "disabled"
                        if explicitly_disabled
                        else "invalid configuration" if configured else "not configured"
                    ),
                    roles=self.ROLE_ORDER,
                )
                continue

            target_type = target["type"]
            if target_type == "area":
                detail = f"HA area {target['area_id']}"
            elif target_type == "entities":
                detail = "lights " + ", ".join(target["entity_ids"])
            else:
                detail = f"entity {target['entity_id']}"
            self.add("Rooms", "OK", label, detail, roles=self.ROLE_ORDER)

    def check_local_runtime(self) -> None:
        roles = tuple(getattr(self, "_active_roles", self.active_roles()))
        supported = sys.version_info >= (3, 9)
        self.add(
            "Runtime readiness",
            "OK" if supported else "FAIL",
            "Python version",
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            required=True,
            roles=roles,
        )

        voice_roles = tuple(role for role in ("ptt", "wakeword") if role in roles)
        if voice_roles:
            self.check_audio_input_profile(voice_roles)
        if "ptt" in roles:
            self.check_ptt_runtime()
        if "wakeword" in roles:
            self.check_wakeword_runtime()

    def check_audio_input_profile(self, roles: tuple[str, ...]) -> None:
        profile = self.pref("AUDIO_INPUT_PROFILE", {}) or {}
        if not isinstance(profile, dict):
            self.add(
                "Runtime readiness",
                "FAIL",
                "audio input profile",
                "AUDIO_INPUT_PROFILE must be a mapping",
                required=True,
                roles=roles,
            )
            return

        try:
            import sounddevice as sd
        except Exception as exc:
            self.add(
                "Runtime readiness",
                "FAIL",
                "sounddevice input support",
                f"unavailable: {exc}",
                required=True,
                roles=roles,
            )
            return

        device_match = str(profile.get("device_match") or "").strip()
        device_index = profile.get("device_index")
        try:
            devices = sd.query_devices()
            selected_index = None
            if device_index is not None:
                candidate = int(device_index)
                if 0 <= candidate < len(devices) and int(devices[candidate]["max_input_channels"] or 0) > 0:
                    selected_index = candidate
            elif device_match:
                needle = device_match.casefold()
                for index, device in enumerate(devices):
                    if int(device["max_input_channels"] or 0) <= 0:
                        continue
                    if needle in str(device["name"] or "").casefold():
                        selected_index = index
                        break

            if selected_index is None:
                target = f"index {device_index}" if device_index is not None else repr(device_match or "default input")
                self.add(
                    "Runtime readiness",
                    "FAIL",
                    "configured audio input",
                    f"no input device matched {target}",
                    required=True,
                    roles=roles,
                )
                return

            sample_rate = int(profile.get("sample_rate") or 0)
            channels = max(1, int(profile.get("channels") or 1))
            try:
                sd.check_input_settings(device=selected_index, samplerate=sample_rate or None, channels=channels)
                detail = f"{devices[selected_index]['name']} (index {selected_index}, {sample_rate or 'default'} Hz)"
                status = "OK"
            except Exception as exc:
                detail = f"{devices[selected_index]['name']} cannot open at {sample_rate or 'default'} Hz: {exc}"
                status = "FAIL"
            self.add(
                "Runtime readiness",
                status,
                "configured audio input",
                detail,
                required=status == "FAIL",
                roles=roles,
            )
        except Exception as exc:
            self.add(
                "Runtime readiness",
                "FAIL",
                "configured audio input",
                str(exc),
                required=True,
                roles=roles,
            )

    def check_ptt_runtime(self) -> None:
        if not bool(self.pref("HANDSET_PRESENT", False)):
            self.add(
                "Runtime readiness",
                "WARN",
                "PTT handset input",
                "PTT is enabled but HANDSET_PRESENT is false; confirm this is an intentional button-only build",
                roles=("ptt",),
            )
            return
        gpio_available = self.module_available("RPi.GPIO")
        hook_pin = self.pref("HANDSET_GPIO_PIN", 11)
        self.add(
            "Runtime readiness",
            "OK" if gpio_available else "FAIL",
            "PTT GPIO support",
            f"BCM GPIO {hook_pin}" if gpio_available else "RPi.GPIO is not installed",
            required=not gpio_available,
            roles=("ptt",),
        )

    def check_wakeword_runtime(self) -> None:
        engine = str(self.pref("WAKEWORD_ENGINE", "openwakeword") or "openwakeword").strip().lower()
        if engine == "openwakeword":
            missing = [
                module
                for module in ("openwakeword", "onnxruntime")
                if not self.module_available(module)
            ]
            self.add(
                "Runtime readiness",
                "OK" if not missing else "FAIL",
                "OpenWakeWord runtime",
                "installed" if not missing else "missing: " + ", ".join(missing) + "; run homesuite install-wakeword",
                required=bool(missing),
                roles=("wakeword",),
            )
            for raw_path in self.pref("WAKEWORD_MODEL_PATHS", []) or []:
                path = Path(str(raw_path)).expanduser()
                self.add(
                    "Runtime readiness",
                    "OK" if path.is_file() else "FAIL",
                    "wakeword model path",
                    str(path),
                    required=not path.is_file(),
                    roles=("wakeword",),
                )
            return

        if engine == "porcupine":
            available = self.module_available("pvporcupine")
            has_key = self.has_value(self.value("PVPORCUPINE_ACCESS_KEY"))
            self.add(
                "Runtime readiness",
                "OK" if available and has_key else "FAIL",
                "Porcupine runtime",
                "installed and key configured" if available and has_key else "requires pvporcupine and PVPORCUPINE_ACCESS_KEY",
                required=True,
                roles=("wakeword",),
            )
            return

        self.add(
            "Runtime readiness",
            "FAIL",
            "wakeword engine",
            f"unsupported engine {engine!r}",
            required=True,
            roles=("wakeword",),
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

        alpaca_key = (
            self.value("ALPACA_API_KEY_ID")
            or self.value("APCA_API_KEY_ID")
            or os.getenv("ALPACA_API_KEY_ID")
            or os.getenv("APCA_API_KEY_ID")
        )
        alpaca_secret = (
            self.value("ALPACA_API_SECRET_KEY")
            or self.value("APCA_API_SECRET_KEY")
            or os.getenv("ALPACA_API_SECRET_KEY")
            or os.getenv("APCA_API_SECRET_KEY")
        )
        if self.has_value(alpaca_key) and self.has_value(alpaca_secret):
            self.add("Optional integrations", "OK", "Alpaca market data", "configured")
        elif self.has_value(alpaca_key) or self.has_value(alpaca_secret):
            self.add(
                "Optional integrations",
                "WARN",
                "Alpaca market data",
                "partially configured; both key ID and secret key are required",
            )
        else:
            self.add("Optional integrations", "SKIP", "Alpaca market data", "not configured")

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

    def get_url(
        self,
        url: str,
        *,
        headers: Optional[dict] = None,
        max_bytes: int = 2048,
    ) -> tuple[int, str]:
        req = Request(url, headers=headers or {})
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                body = resp.read(max_bytes).decode("utf-8", "replace")
                return int(resp.status), body
        except HTTPError as exc:
            body = exc.read(max_bytes).decode("utf-8", "replace")
            return int(exc.code), body
        except URLError as exc:
            raise RuntimeError(str(exc.reason)) from exc

    def check_live_services(self) -> None:
        self.check_home_assistant_live()
        self.check_alpaca_live()
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
                roles=self.ROLE_ORDER,
            )
        except Exception as exc:
            self.add("Live checks", "FAIL", "Home Assistant reachable", str(exc), required=True, roles=self.ROLE_ORDER)

    def check_alpaca_live(self) -> None:
        key_id = (
            self.value("ALPACA_API_KEY_ID")
            or self.value("APCA_API_KEY_ID")
            or os.getenv("ALPACA_API_KEY_ID")
            or os.getenv("APCA_API_KEY_ID")
        )
        secret_key = (
            self.value("ALPACA_API_SECRET_KEY")
            or self.value("APCA_API_SECRET_KEY")
            or os.getenv("ALPACA_API_SECRET_KEY")
            or os.getenv("APCA_API_SECRET_KEY")
        )
        if not self.has_value(key_id) or not self.has_value(secret_key):
            self.add("Live checks", "SKIP", "Alpaca market data", "not configured")
            return
        base_url = str(self.pref("STOCK_QUOTE_DATA_BASE_URL", "https://data.alpaca.markets"))
        feed = quote(str(self.pref("STOCK_QUOTE_DATA_FEED", "iex")))
        url = urljoin(self.clean_url(base_url), f"v2/stocks/AAPL/snapshot?feed={feed}")
        headers = {
            "APCA-API-KEY-ID": str(key_id),
            "APCA-API-SECRET-KEY": str(secret_key),
        }
        try:
            status, _body = self.get_url(url, headers=headers)
            self.add(
                "Live checks",
                "OK" if status == 200 else "WARN",
                "Alpaca market data",
                f"HTTP {status}",
            )
        except Exception as exc:
            self.add("Live checks", "WARN", "Alpaca market data", str(exc))

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

    @staticmethod
    def _walk_entity_ids(value) -> set[str]:
        """Extract Home Assistant entity IDs from nested deployment mappings."""
        entity_re = re.compile(r"^[a-z_]+\.[a-z0-9_]+$", re.IGNORECASE)
        found: set[str] = set()
        if isinstance(value, str):
            candidate = value.strip()
            if entity_re.fullmatch(candidate):
                found.add(candidate)
        elif isinstance(value, dict):
            for child in value.values():
                found.update(Doctor._walk_entity_ids(child))
        elif isinstance(value, (list, tuple, set)):
            for child in value:
                found.update(Doctor._walk_entity_ids(child))
        return found

    def configured_entity_ids(self) -> set[str]:
        """Return explicitly configured entity IDs without scanning every default."""
        sources = (
            self.pref("ROOMS", {}),
            self.pref("CALENDARS", {}),
            self.pref("WEATHER_ENTITY_ID", None),
            self.pref("HA_DEVICE_ALIASES", {}),
            self.pref("HA_TRIGGER_ALIASES", {}),
        )
        found: set[str] = set()
        for source in sources:
            found.update(self._walk_entity_ids(source))
        return found

    def check_live_topology(self) -> None:
        if not self.configured(["HA_URL", "HA_TOKEN"]):
            self.add("Live checks", "SKIP", "configured HA entities", "missing HA_URL or HA_TOKEN", roles=self.ROLE_ORDER)
            return

        configured_ids = self.configured_entity_ids()
        if not configured_ids:
            self.add("Live checks", "SKIP", "configured HA entities", "no explicit entity IDs to validate", roles=self.ROLE_ORDER)
            return

        url = urljoin(self.clean_url(str(self.value("HA_URL"))), "api/states")
        try:
            status, body = self.get_url(
                url,
                headers={"Authorization": f"Bearer {self.value('HA_TOKEN')}"},
                max_bytes=4 * 1024 * 1024,
            )
            if status != 200:
                self.add("Live checks", "WARN", "configured HA entities", f"could not read states (HTTP {status})", roles=self.ROLE_ORDER)
                return
            states = json.loads(body)
            live_ids = {
                str(row.get("entity_id"))
                for row in states
                if isinstance(row, dict) and row.get("entity_id")
            }
            missing = sorted(configured_ids - live_ids)
            if missing:
                display = ", ".join(missing[:8])
                suffix = "" if len(missing) <= 8 else f" (+{len(missing) - 8} more)"
                self.add(
                    "Live checks",
                    "WARN",
                    "configured HA entities",
                    f"{len(missing)} configured ID(s) not found: {display}{suffix}",
                    roles=self.ROLE_ORDER,
                )
                return
            self.add(
                "Live checks",
                "OK",
                "configured HA entities",
                f"{len(configured_ids)} configured ID(s) found",
                roles=self.ROLE_ORDER,
            )
        except Exception as exc:
            self.add("Live checks", "WARN", "configured HA entities", str(exc), roles=self.ROLE_ORDER)

    def check_live_api_listener(self) -> None:
        if not bool(self.pref("UNIFIED_SERVER_ENABLED", True)):
            self.add("Live checks", "SKIP", "local API listener", "disabled in preferences", roles=("api",))
            return
        try:
            port = int(self.pref("UNIFIED_SERVER_PORT", 8765) or 8765)
            status, _body = self.get_url(f"http://127.0.0.1:{port}/health")
            self.add(
                "Live checks",
                "OK" if status == 200 else "WARN",
                "local API listener",
                f"HTTP {status} on port {port}",
                roles=("api",),
            )
        except Exception as exc:
            self.add(
                "Live checks",
                "WARN",
                "local API listener",
                f"not running yet or unreachable: {exc}",
                roles=("api",),
            )

    def role_summary(self) -> list[dict]:
        roles = tuple(getattr(self, "_active_roles", self.active_roles()))
        summary = []
        for role in roles:
            relevant = [check for check in self.checks if role in check.roles]
            failures = [check for check in relevant if check.required and check.status == "FAIL"]
            warnings = [check for check in relevant if check.status == "WARN"]
            status = "FAIL" if failures else ("WARN" if warnings else "OK")
            summary.append(
                {
                    "role": role,
                    "status": status,
                    "required_failures": len(failures),
                    "warnings": len(warnings),
                }
            )
        return summary

    def relevant_checks(self) -> list[Check]:
        """Return checks that apply to the selected or detected node roles."""
        active_roles = set(getattr(self, "_active_roles", self.active_roles()))
        return [
            check
            for check in self.checks
            if not check.roles or bool(active_roles.intersection(check.roles))
        ]

    def required_failures(self) -> list[Check]:
        return [
            check
            for check in self.relevant_checks()
            if check.required and check.status == "FAIL"
        ]

    @staticmethod
    def _redacted_check_label(check: Check) -> str:
        """Keep diagnostic categories while removing deployment identifiers."""
        if check.group == "Rooms" and check.label.endswith(" brightness"):
            return "room brightness"
        return check.label

    def redacted_report(self) -> dict:
        """Return shareable readiness data without local configuration values."""
        return {
            "ok": not self.required_failures(),
            "roles": self.role_summary(),
            "checks": [
                {
                    "group": check.group,
                    "status": check.status,
                    "label": self._redacted_check_label(check),
                    "required": check.required,
                    "roles": check.roles,
                }
                for check in self.relevant_checks()
            ],
        }

    def print_report(self) -> None:
        checks = self.relevant_checks()
        required_failures = self.required_failures()
        warnings = [check for check in checks if check.status == "WARN"]
        summary = self.role_summary()
        if bool(getattr(self, "json_output", False)):
            print(json.dumps(self.redacted_report(), indent=2, sort_keys=True))
            return

        print("Home Suite doctor")
        print("================")
        current_group = None
        for check in checks:
            if check.group != current_group:
                current_group = check.group
                print()
                print(current_group)
            detail = f" - {check.detail}" if check.detail else ""
            print(f"[{check.status}] {check.label}{detail}")

        print()
        print("Role readiness")
        for role in summary:
            detail = f"{role['required_failures']} required failure(s), {role['warnings']} warning(s)"
            print(f"[{role['status']}] {role['role']} - {detail}")
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
    parser.add_argument(
        "--role",
        action="append",
        choices=("text", "api", "ptt", "wakeword", "all"),
        help="validate one role explicitly instead of auto-detecting enabled roles",
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable redacted report")
    args = parser.parse_args(argv)
    return Doctor(
        live=args.live,
        timeout=args.timeout,
        requested_roles=args.role,
        json_output=args.json,
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
