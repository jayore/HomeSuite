"""Shared credential contracts and readiness checks for integrations.

Runtime providers, Doctor, and the management console should agree about what
"configured" means.  This module owns canonical credential names, supported
aliases, environment fallbacks, and redacted readiness metadata.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence


@dataclass(frozen=True)
class CredentialRequirement:
    """One required credential, including accepted compatibility aliases."""

    key: str
    aliases: tuple[str, ...] = ()
    environment: bool = False

    @property
    def names(self) -> tuple[str, ...]:
        return (self.key, *self.aliases)


@dataclass(frozen=True)
class IntegrationSpec:
    """User-facing integration identity and its required credentials."""

    integration_id: str
    label: str
    description: str
    credentials: tuple[CredentialRequirement, ...]
    settings: tuple[str, ...] = ()

    @property
    def setting_keys(self) -> tuple[str, ...]:
        """Return every guided setting owned by this integration."""

        ordered = [requirement.key for requirement in self.credentials]
        for key in self.settings:
            if key not in ordered:
                ordered.append(key)
        return tuple(ordered)


def _credential(
    key: str,
    *aliases: str,
    environment: bool = False,
) -> CredentialRequirement:
    return CredentialRequirement(
        key=key,
        aliases=tuple(aliases),
        environment=environment,
    )


INTEGRATION_SPECS = (
    IntegrationSpec(
        "home_assistant",
        "Home Assistant",
        "Core automation and state provider",
        (_credential("HA_URL"), _credential("HA_TOKEN")),
    ),
    IntegrationSpec(
        "openai",
        "OpenAI",
        "Speech recognition and conversational fallback",
        (_credential("OPENAI_API_KEY"),),
    ),
    IntegrationSpec(
        "plex",
        "Plex",
        "Media library and playback",
        (_credential("PLEX_URL"), _credential("PLEX_TOKEN")),
    ),
    IntegrationSpec(
        "spotify",
        "Spotify",
        "Music search and playback",
        (
            _credential("SPOTIFY_CLIENT_ID"),
            _credential("SPOTIFY_CLIENT_SECRET"),
            _credential("SPOTIFY_REFRESH_TOKEN"),
        ),
        ("SPOTIFY_DISCOVER_WEEKLY_URI",),
    ),
    IntegrationSpec(
        "telegram",
        "Telegram",
        "Remote text interface",
        (_credential("TELEGRAM_BOT_TOKEN"),),
        ("TELEGRAM_ALLOWED_USER_IDS", "TELEGRAM_ALLOWED_CHAT_IDS"),
    ),
    IntegrationSpec(
        "youtube",
        "YouTube",
        "YouTube account and playback features",
        (
            _credential("YOUTUBE_OAUTH_CLIENT_ID"),
            _credential("YOUTUBE_OAUTH_CLIENT_SECRET"),
            _credential("YOUTUBE_OAUTH_REFRESH_TOKEN"),
        ),
        ("YOUTUBE_REEL_REFRESH_ENABLED",),
    ),
    IntegrationSpec(
        "alpaca",
        "Alpaca",
        "Read-only market quotes",
        (
            _credential("ALPACA_API_KEY_ID", "APCA_API_KEY_ID", environment=True),
            _credential("ALPACA_API_SECRET_KEY", "APCA_API_SECRET_KEY", environment=True),
        ),
    ),
    IntegrationSpec(
        "uptime_kuma",
        "Uptime Kuma",
        "Homelab status summaries",
        (_credential("UPTIME_KUMA_URL"), _credential("UPTIME_KUMA_STATUS_PAGE_SLUG")),
    ),
    IntegrationSpec(
        "qbittorrent",
        "qBittorrent",
        "Download status",
        (
            _credential("QBITTORRENT_URL"),
            _credential("QBITTORRENT_USERNAME"),
            _credential("QBITTORRENT_PASSWORD"),
        ),
    ),
    IntegrationSpec(
        "seerr",
        "Seerr",
        "Media requests",
        (_credential("SEERR_URL"), _credential("SEERR_API_KEY")),
    ),
    IntegrationSpec(
        "radarr",
        "Radarr",
        "Movie library status",
        (_credential("RADARR_URL"), _credential("RADARR_API_KEY")),
    ),
    IntegrationSpec(
        "sonarr",
        "Sonarr",
        "TV library status",
        (_credential("SONARR_URL"), _credential("SONARR_API_KEY")),
    ),
    IntegrationSpec(
        "lidarr",
        "Lidarr",
        "Music library status",
        (_credential("LIDARR_URL"), _credential("LIDARR_API_KEY")),
    ),
    IntegrationSpec(
        "porcupine",
        "Porcupine wake word",
        "Optional Picovoice wake-word engine",
        (_credential("PVPORCUPINE_ACCESS_KEY"),),
    ),
)


SPECS_BY_ID = {spec.integration_id: spec for spec in INTEGRATION_SPECS}


def has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def resolve_credential_names(
    private_config,
    names: Sequence[str],
    *,
    environ: Optional[Mapping[str, str]] = None,
    allow_environment: bool = True,
) -> tuple[Any, Optional[str]]:
    """Resolve aliases without exposing a credential value in status output."""

    environment = os.environ if environ is None else environ
    for name in names:
        value = getattr(private_config, name, None) if private_config is not None else None
        if has_value(value):
            return value, name
    if allow_environment:
        for name in names:
            value = environment.get(name)
            if has_value(value):
                return value, name
    return "", None


def resolve_requirement(
    private_config,
    requirement: CredentialRequirement,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> tuple[Any, Optional[str]]:
    return resolve_credential_names(
        private_config,
        requirement.names,
        environ=environ,
        allow_environment=requirement.environment,
    )


def integration_readiness(
    spec: IntegrationSpec,
    private_config,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Return redacted, node-local readiness for one integration."""

    configured_fields: list[str] = []
    missing_fields: list[str] = []
    resolved_names: dict[str, str] = {}
    for requirement in spec.credentials:
        _value, resolved_name = resolve_requirement(
            private_config,
            requirement,
            environ=environ,
        )
        if resolved_name:
            configured_fields.append(requirement.key)
            resolved_names[requirement.key] = resolved_name
        else:
            missing_fields.append(requirement.key)

    if not configured_fields:
        status = "not_configured"
    elif missing_fields:
        status = "partial"
    else:
        status = "configured"
    return {
        "id": spec.integration_id,
        "label": spec.label,
        "description": spec.description,
        "status": status,
        "scope": "device",
        "configured_fields": configured_fields,
        "missing_fields": missing_fields,
        "resolved_names": resolved_names,
    }


def integration_rows(
    private_config,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> list[dict[str, Any]]:
    return [
        integration_readiness(spec, private_config, environ=environ)
        for spec in INTEGRATION_SPECS
    ]


def credentials_for(
    integration_id: str,
    private_config,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Return canonical credential keys mapped to resolved runtime values."""

    spec = SPECS_BY_ID[str(integration_id)]
    return {
        requirement.key: resolve_requirement(
            private_config,
            requirement,
            environ=environ,
        )[0]
        for requirement in spec.credentials
    }
