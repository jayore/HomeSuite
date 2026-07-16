"""Provider-scoped setup and safe connection checks for the web console."""

from __future__ import annotations

import importlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from urllib.parse import quote, urljoin, urlsplit

import requests

from config_schema import EDITABLE_FIELDS
from integration_registry import INTEGRATION_SPECS, SPECS_BY_ID, has_value


class ConsoleIntegrationError(RuntimeError):
    """A user-facing integration-console error with an HTTP status."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = int(status)


class IntegrationConnectionTester:
    """Run bounded, read-only provider checks without exposing response data."""

    SUPPORTED_IDS = frozenset(
        {
            "home_assistant",
            "openai",
            "plex",
            "spotify",
            "telegram",
            "youtube",
            "alpaca",
            "uptime_kuma",
            "qbittorrent",
            "seerr",
            "radarr",
            "sonarr",
            "lidarr",
        }
    )

    SUCCESS_DETAILS = {
        "home_assistant": "Home Assistant accepted the configured token.",
        "openai": "OpenAI accepted the configured API key.",
        "plex": "The Plex server accepted the configured token.",
        "spotify": "Spotify accepted the configured account authorization.",
        "telegram": "Telegram recognized the configured bot token.",
        "youtube": "Google accepted the configured YouTube authorization.",
        "alpaca": "Alpaca returned read-only market data.",
        "uptime_kuma": "The public Uptime Kuma status page is reachable.",
        "qbittorrent": "qBittorrent accepted the configured Web UI credentials.",
        "seerr": "Seerr accepted the configured API key.",
        "radarr": "Radarr accepted the configured API key.",
        "sonarr": "Sonarr accepted the configured API key.",
        "lidarr": "Lidarr accepted the configured API key.",
    }

    def __init__(
        self,
        *,
        session: Optional[requests.Session] = None,
        timeout: float = 5.0,
        app_config=None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = max(1.0, min(float(timeout), 15.0))
        self.app_config = app_config or importlib.import_module("app_config")

    @classmethod
    def supports(cls, integration_id: str) -> bool:
        return str(integration_id) in cls.SUPPORTED_IDS

    @staticmethod
    def _base_url(value: Any) -> str:
        text = str(value or "").strip()
        parsed = urlsplit(text)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConsoleIntegrationError("Enter a complete http:// or https:// service URL first.")
        return text.rstrip("/") + "/"

    def _request(self, method: str, url: str, **kwargs) -> tuple[int, bytes]:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("User-Agent", "HomeSuite-Console/1")
        try:
            response = self.session.request(
                method,
                url,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
                stream=True,
                **kwargs,
            )
            try:
                raw = getattr(response, "raw", None)
                if raw is not None and hasattr(raw, "read"):
                    try:
                        body = raw.read(4096, decode_content=True)
                    except TypeError:
                        body = raw.read(4096)
                else:
                    body = bytes(getattr(response, "content", b"") or b"")[:4096]
            finally:
                response.close()
            return int(response.status_code), bytes(body or b"")
        except requests.Timeout as exc:
            raise ConsoleIntegrationError("The service did not respond within the five-second test window.") from exc
        except requests.RequestException as exc:
            raise ConsoleIntegrationError(
                "The service could not be reached from this Home Suite device. Check its address, DNS, and network access."
            ) from exc

    @staticmethod
    def _failure_for_status(status: int) -> str:
        if status in {401, 403}:
            return "The service was reached, but it rejected the configured credentials."
        if status == 404:
            return "The service was reached, but the expected API endpoint was not found. Check the base URL."
        if status == 429:
            return "The service was reached, but the account or API is currently rate limited."
        if 300 <= status < 400:
            return "The configured address redirected. Use the service's direct base URL."
        if status >= 500:
            return f"The service was reached, but it returned server error HTTP {status}."
        return f"The service was reached, but its API returned HTTP {status}."

    def _expect_success(self, status: int, *, allowed: tuple[int, ...] = (200,)) -> None:
        if status not in allowed:
            raise ConsoleIntegrationError(self._failure_for_status(status))

    @staticmethod
    def _json_object(body: bytes) -> dict[str, Any]:
        try:
            value = json.loads(body.decode("utf-8", "replace"))
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def test(self, integration_id: str, values: Mapping[str, Any]) -> dict[str, str]:
        integration_id = str(integration_id)
        if integration_id not in self.SUPPORTED_IDS:
            raise ConsoleIntegrationError("This integration does not have a standalone connection test.", status=409)

        try:
            if integration_id == "home_assistant":
                status, _ = self._request(
                    "GET",
                    urljoin(self._base_url(values["HA_URL"]), "api/"),
                    headers={"Authorization": f"Bearer {values['HA_TOKEN']}"},
                )
                self._expect_success(status)
            elif integration_id == "openai":
                status, _ = self._request(
                    "GET",
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {values['OPENAI_API_KEY']}"},
                )
                self._expect_success(status)
            elif integration_id == "plex":
                status, _ = self._request(
                    "GET",
                    urljoin(self._base_url(values["PLEX_URL"]), "identity"),
                    headers={"X-Plex-Token": str(values["PLEX_TOKEN"])},
                )
                self._expect_success(status)
            elif integration_id == "spotify":
                status, _ = self._request(
                    "POST",
                    "https://accounts.spotify.com/api/token",
                    auth=(str(values["SPOTIFY_CLIENT_ID"]), str(values["SPOTIFY_CLIENT_SECRET"])),
                    data={"grant_type": "refresh_token", "refresh_token": values["SPOTIFY_REFRESH_TOKEN"]},
                )
                self._expect_success(status)
            elif integration_id == "telegram":
                token = quote(str(values["TELEGRAM_BOT_TOKEN"]), safe="")
                status, body = self._request("POST", f"https://api.telegram.org/bot{token}/getMe")
                self._expect_success(status)
                if not bool(self._json_object(body).get("ok")):
                    raise ConsoleIntegrationError("Telegram was reached, but it did not accept the configured bot token.")
            elif integration_id == "youtube":
                status, _ = self._request(
                    "POST",
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": values["YOUTUBE_OAUTH_CLIENT_ID"],
                        "client_secret": values["YOUTUBE_OAUTH_CLIENT_SECRET"],
                        "refresh_token": values["YOUTUBE_OAUTH_REFRESH_TOKEN"],
                        "grant_type": "refresh_token",
                    },
                )
                self._expect_success(status)
            elif integration_id == "alpaca":
                base_url = str(
                    getattr(self.app_config, "STOCK_QUOTE_DATA_BASE_URL", "https://data.alpaca.markets")
                    or "https://data.alpaca.markets"
                )
                feed = quote(str(getattr(self.app_config, "STOCK_QUOTE_DATA_FEED", "iex") or "iex"))
                status, _ = self._request(
                    "GET",
                    urljoin(self._base_url(base_url), f"v2/stocks/AAPL/snapshot?feed={feed}"),
                    headers={
                        "APCA-API-KEY-ID": str(values["ALPACA_API_KEY_ID"]),
                        "APCA-API-SECRET-KEY": str(values["ALPACA_API_SECRET_KEY"]),
                    },
                )
                self._expect_success(status)
            elif integration_id == "uptime_kuma":
                slug = quote(str(values["UPTIME_KUMA_STATUS_PAGE_SLUG"]).strip("/"), safe="")
                status, _ = self._request(
                    "GET",
                    urljoin(self._base_url(values["UPTIME_KUMA_URL"]), f"api/status-page/{slug}"),
                )
                self._expect_success(status)
            elif integration_id == "qbittorrent":
                status, body = self._request(
                    "POST",
                    urljoin(self._base_url(values["QBITTORRENT_URL"]), "api/v2/auth/login"),
                    data={
                        "username": values["QBITTORRENT_USERNAME"],
                        "password": values["QBITTORRENT_PASSWORD"],
                    },
                )
                self._expect_success(status, allowed=(200, 204))
                if status == 200 and body.decode("utf-8", "replace").strip().lower() not in {"", "ok."}:
                    raise ConsoleIntegrationError("qBittorrent was reached, but it rejected the configured credentials.")
            elif integration_id == "seerr":
                status, _ = self._request(
                    "GET",
                    urljoin(self._base_url(values["SEERR_URL"]), "api/v1/request/count"),
                    headers={"X-Api-Key": str(values["SEERR_API_KEY"])},
                )
                self._expect_success(status)
            else:
                prefix = integration_id.upper()
                status, _ = self._request(
                    "GET",
                    urljoin(self._base_url(values[f"{prefix}_URL"]), "api/v3/system/status"),
                    headers={"X-Api-Key": str(values[f"{prefix}_API_KEY"])},
                )
                self._expect_success(status)
        except ConsoleIntegrationError as exc:
            return {
                "status": "failed",
                "summary": "Connection test failed",
                "detail": str(exc),
            }

        return {
            "status": "success",
            "summary": "Connection successful",
            "detail": self.SUCCESS_DETAILS[integration_id],
        }


class ConsoleIntegrationManager:
    """Expose integration setup metadata from the canonical config schema."""

    def __init__(
        self,
        *,
        editor,
        private_config=None,
        app_config=None,
        environ: Optional[Mapping[str, str]] = None,
        tester: Optional[IntegrationConnectionTester] = None,
    ) -> None:
        self.editor = editor
        self.private_config = private_config or importlib.import_module("private_config")
        self.app_config = app_config or importlib.import_module("app_config")
        self.environ = os.environ if environ is None else environ
        self.tester = tester or IntegrationConnectionTester(app_config=self.app_config)

    @staticmethod
    def _spec(integration_id: str):
        spec = SPECS_BY_ID.get(str(integration_id or "").strip())
        if spec is None:
            raise ConsoleIntegrationError("Unknown integration.", status=404)
        return spec

    def _state(self, *, include_secrets: bool) -> tuple[dict, dict[str, dict]]:
        state = self.editor.public_state(include_secrets=include_secrets)
        fields = {str(field["key"]): field for field in state.get("fields") or []}
        return state, fields

    def _fallback_value(self, requirement) -> tuple[Any, Optional[str]]:
        for alias in requirement.aliases:
            value = getattr(self.private_config, alias, None)
            if has_value(value):
                return value, alias
        if requirement.environment:
            for name in requirement.names:
                value = self.environ.get(name)
                if has_value(value):
                    return value, name
        return "", None

    def _requirement_value(self, requirement, fields: Mapping[str, dict]) -> tuple[Any, Optional[str]]:
        field = fields.get(requirement.key)
        value = field.get("value") if field else None
        if field and field.get("configured") and has_value(value):
            return value, requirement.key
        return self._fallback_value(requirement)

    def _row(self, spec, fields: Mapping[str, dict]) -> dict[str, Any]:
        configured_fields: list[str] = []
        missing_fields: list[str] = []
        resolved_names: dict[str, str] = {}
        for requirement in spec.credentials:
            field = fields.get(requirement.key)
            configured = bool(field and field.get("configured"))
            resolved_name: Optional[str] = requirement.key if configured else None
            if not configured:
                fallback_value, fallback_name = self._fallback_value(requirement)
                configured = has_value(fallback_value)
                resolved_name = fallback_name
            if configured:
                configured_fields.append(requirement.key)
                if resolved_name:
                    resolved_names[requirement.key] = resolved_name
            else:
                missing_fields.append(requirement.key)
        status = "configured" if not missing_fields else ("partial" if configured_fields else "not_configured")
        return {
            "id": spec.integration_id,
            "label": spec.label,
            "description": spec.description,
            "status": status,
            "scope": "device",
            "configured_fields": configured_fields,
            "missing_fields": missing_fields,
            "resolved_names": resolved_names,
            "editable": all(key in fields for key in spec.setting_keys),
            "setting_count": len([key for key in spec.setting_keys if key in fields]),
            "test_supported": self.tester.supports(spec.integration_id),
            "docs_path": "docs/INTEGRATIONS.md",
            "credential_keys": [requirement.key for requirement in spec.credentials],
        }

    def public_state(self) -> dict[str, Any]:
        _state, fields = self._state(include_secrets=False)
        return {"integrations": [self._row(spec, fields) for spec in INTEGRATION_SPECS]}

    def edit_state(self, integration_id: str) -> dict[str, Any]:
        spec = self._spec(integration_id)
        state, fields = self._state(include_secrets=True)
        selected = [fields[key] for key in spec.setting_keys if key in fields]
        if not selected:
            raise ConsoleIntegrationError("This integration is not editable from the console.", status=409)
        return {
            "integration": self._row(spec, fields),
            "fields": selected,
            "revisions": state.get("revisions") or {},
        }

    def test_connection(self, integration_id: str) -> dict[str, Any]:
        spec = self._spec(integration_id)
        if not self.tester.supports(spec.integration_id):
            raise ConsoleIntegrationError("This integration does not have a standalone connection test.", status=409)
        _state, fields = self._state(include_secrets=True)
        values = {
            key: fields[key].get("value")
            for key in spec.setting_keys
            if key in fields and has_value(fields[key].get("value"))
        }
        missing: list[str] = []
        for requirement in spec.credentials:
            value, _resolved_name = self._requirement_value(requirement, fields)
            if has_value(value):
                values[requirement.key] = value
            else:
                missing.append(requirement.key)
        if missing:
            labels = {
                field.key: field.label
                for field in EDITABLE_FIELDS
                if field.key in missing
            }
            readable = ", ".join(labels.get(key, key) for key in missing)
            raise ConsoleIntegrationError(f"Configure {readable} before testing this connection.", status=409)
        result = self.tester.test(spec.integration_id, values)
        return {
            "integration_id": spec.integration_id,
            "label": spec.label,
            "tested_at": datetime.now(timezone.utc).isoformat(),
            **result,
        }
