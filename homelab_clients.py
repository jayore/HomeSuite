from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests


DEFAULT_TIMEOUT = 5


def _clean_url(url: Optional[str]) -> str:
    return (url or "").strip().rstrip("/") + "/"


def _secret(name: str, default: str = "") -> str:
    try:
        import private_config

        return str(getattr(private_config, name, default) or "").strip()
    except Exception:
        return default


def _json_get(url: str, path: str, *, headers: Optional[dict] = None) -> Any:
    resp = requests.get(urljoin(_clean_url(url), path.lstrip("/")), headers=headers or {}, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


@dataclass
class DirectResult:
    ok: bool
    value: Any = None
    error: Optional[str] = None


class QBittorrentClient:
    def __init__(self) -> None:
        self.url = _secret("QBITTORRENT_URL")
        self.username = _secret("QBITTORRENT_USERNAME")
        self.password = _secret("QBITTORRENT_PASSWORD")
        self._session: Optional[requests.Session] = None

    @property
    def configured(self) -> bool:
        return bool(self.url and self.username and self.password)

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        if not self.configured:
            raise RuntimeError("qBittorrent direct API is not configured")
        if self._session is None:
            sess = requests.Session()
            resp = sess.post(
                urljoin(_clean_url(self.url), "api/v2/auth/login"),
                data={"username": self.username, "password": self.password},
                timeout=DEFAULT_TIMEOUT,
            )
            if resp.status_code not in (200, 204):
                raise RuntimeError(f"qBittorrent login failed with HTTP {resp.status_code}")
            if resp.status_code == 200 and (resp.text or "").strip().lower() not in ("ok", ""):
                raise RuntimeError("qBittorrent login was rejected")
            self._session = sess
        resp = self._session.request(method, urljoin(_clean_url(self.url), path.lstrip("/")), timeout=DEFAULT_TIMEOUT, **kwargs)
        resp.raise_for_status()
        return resp

    def torrents(self) -> List[Dict[str, Any]]:
        resp = self._request("GET", "api/v2/torrents/info")
        data = resp.json()
        return data if isinstance(data, list) else []

    def transfer_info(self) -> Dict[str, Any]:
        resp = self._request("GET", "api/v2/transfer/info")
        data = resp.json()
        return data if isinstance(data, dict) else {}

    def pause_hashes(self, hashes: List[str]) -> int:
        vals = [h for h in hashes if h]
        if not vals:
            return 0
        self._request("POST", "api/v2/torrents/pause", data={"hashes": "|".join(vals)})
        return len(vals)


def qbittorrent_snapshot() -> DirectResult:
    try:
        client = QBittorrentClient()
        if not client.configured:
            return DirectResult(False, error="not_configured")
        return DirectResult(True, {"torrents": client.torrents(), "transfer": client.transfer_info()})
    except Exception as exc:
        return DirectResult(False, error=type(exc).__name__)


def qbittorrent_pause_completed() -> DirectResult:
    try:
        client = QBittorrentClient()
        if not client.configured:
            return DirectResult(False, error="not_configured")
        torrents = client.torrents()
        hashes = [
            str(t.get("hash") or "")
            for t in torrents
            if _is_completed_torrent(t) and not _is_paused_torrent(t)
        ]
        paused = client.pause_hashes(hashes)
        return DirectResult(True, {"paused": paused, "completed": len([t for t in torrents if _is_completed_torrent(t)])})
    except Exception as exc:
        return DirectResult(False, error=type(exc).__name__)


def _is_completed_torrent(torrent: Dict[str, Any]) -> bool:
    try:
        if float(torrent.get("progress") or 0) >= 0.999:
            return True
    except Exception:
        pass
    try:
        if int(torrent.get("amount_left") or 0) <= 0 and int(torrent.get("completion_on") or 0) > 0:
            return True
    except Exception:
        pass
    state = str(torrent.get("state") or "").lower()
    return state in {"uploading", "stalledup", "queuedup", "pausedup", "forcedup", "checkingup"}


def _is_paused_torrent(torrent: Dict[str, Any]) -> bool:
    return str(torrent.get("state") or "").lower().startswith("paused")


def _is_active_download(torrent: Dict[str, Any]) -> bool:
    if _is_completed_torrent(torrent):
        return False
    state = str(torrent.get("state") or "").lower()
    if state in {"downloading", "forceddl", "queueddl", "stalleddl", "checkingdl", "metadl"}:
        return True
    try:
        return int(torrent.get("dlspeed") or 0) > 0
    except Exception:
        return False


def summarize_torrents(torrents: List[Dict[str, Any]], *, limit: int = 5) -> Dict[str, Any]:
    active = [t for t in torrents if _is_active_download(t)]
    completed = [t for t in torrents if _is_completed_torrent(t)]
    paused = [t for t in torrents if _is_paused_torrent(t)]
    errored = [t for t in torrents if "error" in str(t.get("state") or "").lower()]
    names = [str(t.get("name") or "").strip() for t in active if str(t.get("name") or "").strip()]
    return {
        "total": len(torrents),
        "active": len(active),
        "completed": len(completed),
        "paused": len(paused),
        "errored": len(errored),
        "active_names": names[:limit],
        "active_name_count": len(names),
    }


class SeerrClient:
    def __init__(self) -> None:
        self.url = _secret("SEERR_URL")
        self.api_key = _secret("SEERR_API_KEY")

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key)

    @property
    def headers(self) -> dict:
        return {"X-Api-Key": self.api_key}

    def request_counts(self) -> Dict[str, Any]:
        if not self.configured:
            raise RuntimeError("Seerr direct API is not configured")
        data = _json_get(self.url, "api/v1/request/count", headers=self.headers)
        return data if isinstance(data, dict) else {}

    def requests(self, *, filter_name: Optional[str] = None, take: int = 10) -> List[Dict[str, Any]]:
        if not self.configured:
            raise RuntimeError("Seerr direct API is not configured")
        path = f"api/v1/request?take={int(take)}&skip=0&sort=added"
        if filter_name:
            path += f"&filter={filter_name}"
        data = _json_get(self.url, path, headers=self.headers)
        results = data.get("results") if isinstance(data, dict) else None
        return results if isinstance(results, list) else []


def seerr_snapshot() -> DirectResult:
    try:
        client = SeerrClient()
        if not client.configured:
            return DirectResult(False, error="not_configured")
        return DirectResult(True, {"counts": client.request_counts(), "pending": client.requests(filter_name="pending", take=5)})
    except Exception as exc:
        return DirectResult(False, error=type(exc).__name__)


def seerr_request_title(request: Dict[str, Any]) -> str:
    media = request.get("media") if isinstance(request, dict) else {}
    if not isinstance(media, dict):
        media = {}
    title = (
        media.get("title")
        or media.get("name")
        or request.get("title")
        or request.get("name")
        or media.get("originalTitle")
        or media.get("originalName")
    )
    if title:
        return str(title).strip()
    media_type = request.get("type") or media.get("mediaType") or "request"
    rid = request.get("id")
    return f"{media_type} request {rid}" if rid else str(media_type)


class UptimeKumaClient:
    def __init__(self) -> None:
        self.url = _secret("UPTIME_KUMA_URL")
        self.status_page_slug = _secret("UPTIME_KUMA_STATUS_PAGE_SLUG", "home")

    @property
    def configured(self) -> bool:
        return bool(self.url and self.status_page_slug)

    def status_page_snapshot(self) -> Dict[str, Any]:
        if not self.configured:
            raise RuntimeError("Uptime Kuma status page is not configured")

        page = _json_get(self.url, f"api/status-page/{self.status_page_slug}")
        heartbeat = _json_get(self.url, f"api/status-page/heartbeat/{self.status_page_slug}")

        groups = page.get("publicGroupList") if isinstance(page, dict) else None
        if not isinstance(groups, list):
            groups = []
        heartbeat_list = heartbeat.get("heartbeatList") if isinstance(heartbeat, dict) else None
        if not isinstance(heartbeat_list, dict):
            heartbeat_list = {}
        uptime_list = heartbeat.get("uptimeList") if isinstance(heartbeat, dict) else None
        if not isinstance(uptime_list, dict):
            uptime_list = {}

        monitors = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            for mon in group.get("monitorList") or []:
                if not isinstance(mon, dict):
                    continue
                mid = str(mon.get("id") or "").strip()
                name = str(mon.get("name") or "").strip()
                if not mid or not name:
                    continue
                heartbeats = heartbeat_list.get(mid) or heartbeat_list.get(int(mid)) or []
                latest = heartbeats[-1] if isinstance(heartbeats, list) and heartbeats else {}
                if not isinstance(latest, dict):
                    latest = {}
                status_code = latest.get("status")
                monitors.append(
                    {
                        "id": mid,
                        "name": name,
                        "status": _kuma_status_word(status_code),
                        "status_code": status_code,
                        "message": str(latest.get("msg") or "").strip(),
                        "time": latest.get("time"),
                        "active": mon.get("active"),
                        "type": mon.get("type"),
                        "uptime": _kuma_monitor_uptime(uptime_list, mid),
                    }
                )

        return {
            "slug": self.status_page_slug,
            "title": page.get("title") if isinstance(page, dict) else None,
            "monitors": monitors,
        }


def _kuma_status_word(status_code: Any) -> str:
    try:
        code = int(status_code)
    except Exception:
        return "unknown"
    if code == 1:
        return "up"
    if code == 0:
        return "down"
    if code == 2:
        return "pending"
    if code == 3:
        return "maintenance"
    return "unknown"


def _kuma_monitor_uptime(uptime_list: Dict[str, Any], monitor_id: str) -> Optional[float]:
    candidates = [
        uptime_list.get(monitor_id),
        uptime_list.get(f"{monitor_id}_24"),
        uptime_list.get(f"{monitor_id}_720"),
    ]
    for val in candidates:
        try:
            if val is not None:
                return float(val)
        except Exception:
            pass
    return None


def uptime_kuma_snapshot() -> DirectResult:
    try:
        client = UptimeKumaClient()
        if not client.configured:
            return DirectResult(False, error="not_configured")
        return DirectResult(True, client.status_page_snapshot())
    except requests.HTTPError as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code == 404:
            return DirectResult(False, error="status_page_not_found")
        return DirectResult(False, error=f"http_{status_code}" if status_code else "HTTPError")
    except Exception as exc:
        return DirectResult(False, error=type(exc).__name__)
