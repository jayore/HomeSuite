"""Small helpers for optional integration configuration checks."""
from __future__ import annotations

from typing import Iterable


def private_value(name: str, default=None):
    try:
        import private_config
        return getattr(private_config, name, default)
    except Exception:
        return default


def _has_value(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def configured(*names: str) -> bool:
    return all(_has_value(private_value(name, "")) for name in names)


def missing(*names: str) -> list[str]:
    return [name for name in names if not _has_value(private_value(name, ""))]


def friendly_missing(service_name: str, names: Iterable[str]) -> str:
    fields = ", ".join(names)
    return f"{service_name} is not configured yet. Add {fields} in private_config.py."


def plex_configured() -> bool:
    return configured("PLEX_URL", "PLEX_TOKEN")


def spotify_web_configured() -> bool:
    return configured("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN")


def telegram_configured() -> bool:
    return configured("TELEGRAM_BOT_TOKEN")
