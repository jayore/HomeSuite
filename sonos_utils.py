"""Sonos / Home Assistant media helpers.

Split out of main.py to reduce coupling and keep the main runtime loop focused.
"""

from __future__ import annotations

import logging
import os

try:
    from env_compat import env_get, install_homesuite_env_aliases
    install_homesuite_env_aliases()
except Exception:
    env_get = lambda name, default=None: os.environ.get(name, default)
import socket
import time
from pathlib import Path
from typing import Optional, Union

from ha_client import call_ha_service, ha_get_states


# -------------------------
# Announcement HTTP server (Sonos fetches MP3 via HTTP)
# -------------------------
_announce_httpd = None
_announce_http_thread = None
_last_generated_media_cleanup_ts = 0.0

_GENERATED_MEDIA_PREFIXES = (
    "announce_",
    "assistant_response_",
    "homesuite_alarm_voice_",
    "homesuite_alarm_sonos_voice_",
    "homesuite_alarm_sonos_voice_combo_",
    "homesuite_alarm_combo_",
    "piphone_alarm_voice_",
    "piphone_alarm_sonos_voice_",
    "piphone_alarm_sonos_voice_combo_",
    "piphone_alarm_combo_",
)


def _resolve_homesuite_host() -> str:
    """
    Best-effort local LAN IP so Sonos can fetch generated media files.
    """
    env = (env_get("PIPHONE_HOST") or "").strip()
    if env:
        return env

    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip:
            return ip
    except Exception:
        pass

    return "127.0.0.1"


def homesuite_media_url_for_path(path: Union[str, Path], *, port: int = 8000) -> str:
    p = str(path)
    if not p.startswith("/"):
        p = "/" + p
    return f"http://{_resolve_homesuite_host()}:{int(port)}{p}"


def piphone_media_url_for_path(path: Union[str, Path], *, port: int = 8000) -> str:
    # Legacy alias for older callers; prefer homesuite_media_url_for_path().
    return homesuite_media_url_for_path(path, port=port)


def cleanup_generated_media_files(*, max_age_sec: float = 6 * 60 * 60, min_interval_sec: float = 60 * 60) -> int:
    """
    Remove old generated MP3 files that Sonos fetches from /tmp.

    Files are intentionally not deleted immediately after a Sonos play_media
    call, because the speaker may fetch the URL after the service call returns.
    """
    global _last_generated_media_cleanup_ts

    now = time.time()
    if min_interval_sec > 0 and (now - _last_generated_media_cleanup_ts) < min_interval_sec:
        return 0
    _last_generated_media_cleanup_ts = now

    removed = 0
    cutoff = now - max(60.0, float(max_age_sec))
    for prefix in _GENERATED_MEDIA_PREFIXES:
        for path in Path("/tmp").glob(f"{prefix}*.mp3"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except FileNotFoundError:
                pass
            except Exception:
                logging.exception("Generated media cleanup failed path=%s", path)

    if removed:
        logging.info("GENERATED_MEDIA_CLEANUP removed=%d max_age_sec=%.0f", removed, max_age_sec)
    return removed

def _ensure_announce_http_server(port: int = 8000, directory: str = "/") -> bool:
    """
    Ensure a background HTTP server is running so Sonos can fetch /tmp/*.mp3 via URL.
    Serves files from `directory` (default "/"), on http://<host>:<port>/...
    """
    global _announce_httpd, _announce_http_thread

    try:
        cleanup_generated_media_files()
    except Exception:
        logging.exception("Generated media cleanup failed")

    try:
        # If already running, we're done
        if _announce_httpd is not None:
            return True
    except Exception:
        _announce_httpd = None

    try:
        import threading
        import socketserver
        from http.server import SimpleHTTPRequestHandler

        class QuietHandler(SimpleHTTPRequestHandler):
            # Silence request logs
            def log_message(self, format, *args):
                pass

        # Python 3.9 SimpleHTTPRequestHandler supports `directory=...`
        handler = lambda *args, **kwargs: QuietHandler(*args, directory=directory, **kwargs)  # type: ignore

        class ReusableTCPServer(socketserver.TCPServer):
            allow_reuse_address = True

        _announce_httpd = ReusableTCPServer(("", port), handler)

    except Exception:
        import logging
        logging.exception("Announcement HTTP server bind failed (port %s)", port)
        _announce_httpd = None
        return False

    def _serve():
        import logging
        try:
            _announce_httpd.serve_forever()
        except Exception:
            logging.exception("Announcement HTTP server crashed")

    _announce_http_thread = threading.Thread(target=_serve, daemon=True)
    _announce_http_thread.start()

    import logging
    logging.info("CLAIM: announce_http_server_started port=%s dir=%r", port, directory)
    return True

def sonos_play_media(
    *,
    entity_id: str,
    media_url: str,
    media_type: str = "music",
    announce: bool = False,
    announce_volume: int = None,
    announce_volume_floor: int = 10,
) -> bool:
    """
    Ask HA Sonos integration to play a URL (Sonos fetches it directly).

    If announce=True, request Sonos-native announcement behavior (duck + restore).
    If announce_volume is not provided, we try to match current volume but apply
    a floor (announce_volume_floor) so announcements aren't too quiet.
    """
    try:
        _ensure_announce_http_server(port=8000, directory="/")

        payload = {
            "entity_id": entity_id,
            "media_content_id": media_url,
            "media_content_type": media_type,
        }

        if announce:
            payload["announce"] = True
            vol = announce_volume

            # If caller didn't specify a volume, infer current volume from HA state.
            if vol is None:
                current_pct = None
                try:
                    states = ha_get_states() or []
                    st = next((s for s in states if s.get("entity_id") == entity_id), None)
                    attrs = (st or {}).get("attributes") or {}
                    vl = attrs.get("volume_level")  # 0.0..1.0
                    if vl is not None:
                        current_pct = int(round(float(vl) * 100))
                except Exception:
                    current_pct = None

                try:
                    floor = int(announce_volume_floor) if announce_volume_floor is not None else None
                except Exception:
                    floor = None

                if current_pct is not None and floor is not None:
                    vol = max(current_pct, floor)
                elif current_pct is not None:
                    vol = current_pct
                elif floor is not None:
                    vol = floor

            # If we have a usable volume, pass it via extra.volume
            if vol is not None:
                try:
                    vol_i = int(vol)
                except Exception:
                    vol_i = None
                if vol_i is not None:
                    payload["extra"] = dict(payload.get("extra") or {})
                    payload["extra"]["volume"] = vol_i

        ok = call_ha_service("media_player/play_media", payload)
        logging.info(
            "CLAIM: sonos_play_media entity_id=%r url=%r ok=%r announce=%r vol=%r",
            entity_id,
            media_url,
            bool(ok),
            bool(announce),
            ((payload.get("extra") or {}).get("volume") if announce else None),
        )
        return bool(ok)

    except Exception:
        logging.exception("sonos_play_media failed entity_id=%r url=%r", entity_id, media_url)
        return False
