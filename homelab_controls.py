"""Answer and execute supported homelab operations from spoken commands.

Most status answers are assembled from the supplied Home Assistant state
snapshot and ``HOMELAB_SERVICES`` entity mappings. Operations that require data
or mutations unavailable through Home Assistant delegate to the narrow clients
in :mod:`homelab_clients`, such as pausing completed torrents or reading Uptime
Kuma monitors.

The public handler claims only explicit homelab, storage, download, service,
network, request, or camera language. Missing integrations produce bounded
status text rather than guessed values.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, Iterable, Optional

from homelab_clients import (
    qbittorrent_pause_completed,
    qbittorrent_snapshot,
    seerr_request_title,
    seerr_snapshot,
    summarize_torrents,
    uptime_kuma_snapshot,
)
from runtime_mode import allow_real_effects


MaybeSay = Callable[[str], str]


_QUERY_START_RE = re.compile(
    r"^(?:please\s+)?(?:how|what|which|is|are|has|have|did|do|show|list|give|tell|any)\b"
)
_HOW_IS_RE = re.compile(r"^(?:please\s+)?how(?:'s|\s+is)\b")
_STATUS_WORD_RE = re.compile(
    r"\b(status|health|healthy|warning|warnings|alert|alerts|pending|processing|"
    r"available|issue|issues|queue|queued|upcoming|active|completed|complete|"
    r"finished|done|paused|errored|errors?|inactive|total|speed|speeds|doing|"
    r"working|running|reachable|online|offline|up|down|broken|failing|outages?|"
    r"space|usage|temperature|temp|hot|cpu|memory|ram|load|throughput|updates?)\b"
)
_SYSTEM_HEALTH_IDIOM_RE = re.compile(
    r"^(?:please\s+)?(?:service\s+status|is\s+anything\s+down|anything\s+down|"
    r"what(?:'s|\s+is)\s+down|what\s+are\s+down|what(?:'s|\s+is)\s+broken|"
    r"any\s+outages?|outage\s+status)$"
)
_PLATFORM_RE = re.compile(
    r"\b(qbittorrent|overseerr|overseer|over\s+seer|seerr|seer|see\s+your|"
    r"radarr|sonarr|lidarr|uptime\s*kuma|kuma)\b"
)
_TORRENT_RE = re.compile(r"\b(torrent|torrents|download|downloads|downloading|qbittorrent)\b")
_MEDIA_REQUEST_RE = re.compile(r"\b(request|requests)\b")
_STORAGE_RE = re.compile(
    r"\b(synology|nas|yore\s*nas|yorenas|diskstation|storage|drives?|volumes?)\b"
)
_INTERNET_RE = re.compile(r"\b(internet|speedtest|network\s+speed|connection\s+speed)\b")
_CAMERA_RE = re.compile(r"\b(camera|cameras|reolink)\b")
_SINGULAR_DRIVE_STATUS_RE = re.compile(
    r"\b(?:"
    r"drive\s+(?:status|health|temperature|temp|warning|alert|space|usage)|"
    r"(?:status|health|temperature|temp|warning|alert|space|usage)\s+"
    r"(?:(?:of|for)\s+)?(?:(?:the|my)\s+)?drive|"
    r"(?:is|are)\s+(?:(?:the|my)\s+)?drive\s+"
    r"(?:healthy|failing|failed|hot|full)"
    r")\b"
)


def _norm(text: str) -> str:
    s = (text or "").strip().lower().replace("’", "'")
    s = re.sub(r"[.!,?]+$", "", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def looks_like_homelab_intent(text: str) -> bool:
    """Return whether text expresses an actionable local homelab intent.

    Domain vocabulary alone is deliberately insufficient. The deterministic
    layer owns explicit mutations and live-status questions, while conceptual
    discussion that merely mentions requests, services, cameras, or the
    internet remains available to conversational fallback.
    """
    t = _norm(text)
    if not t:
        return False

    # Explicit mutation supported by this module.
    if (
        re.search(r"\bpause\b", t)
        and re.search(r"\b(completed|complete|finished|done)\b", t)
        and re.search(r"\b(download|downloads|torrent|torrents)\b", t)
    ):
        return True

    if _SYSTEM_HEALTH_IDIOM_RE.fullmatch(t):
        return True

    if re.search(r"\bhomelab\b", t):
        return bool(
            re.fullmatch(r"(?:the\s+)?homelab", t)
            or _HOW_IS_RE.search(t)
            or _STATUS_WORD_RE.search(t)
        )

    # Keep travel uses of singular "drive" out of storage ownership.
    if _SINGULAR_DRIVE_STATUS_RE.search(t):
        return True

    # Named active-download queries and qBittorrent count/status shorthand.
    if (
        re.search(r"\bwhat\b.*\b(download|downloading|grabbing|processing)\b", t)
        and re.search(r"\b(movie|movies|show|shows|episode|episodes|torrent|torrents|media)\b", t)
    ):
        return True
    if _TORRENT_RE.search(t):
        if _HOW_IS_RE.search(t):
            return True
        if _QUERY_START_RE.search(t) and (
            re.search(r"\bhow\s+many\b", t) or _STATUS_WORD_RE.search(t)
        ):
            return True
        if re.fullmatch(
            r"(?:(?:active|completed|complete|finished|done|paused|errored|inactive|all|total)\s+"
            r"(?:torrents?|downloads?)|(?:torrent|torrents|download|downloads|qbittorrent)\s+"
            r"(?:status|speed|speeds|active|completed|paused|errored|inactive|total))",
            t,
        ):
            return True

    # Seerr-style request language needs either a named platform or explicit
    # status grammar. A prose mention of ordinary "requests" is not enough.
    if _PLATFORM_RE.search(t) and (
        _HOW_IS_RE.search(t) or _STATUS_WORD_RE.search(t)
    ):
        return True
    if _MEDIA_REQUEST_RE.search(t):
        if _QUERY_START_RE.search(t) and _STATUS_WORD_RE.search(t):
            return True
        if re.fullmatch(
            r"(?:(?:media\s+)?request(?:s)?\s+(?:status|health)|"
            r"(?:pending|processing|available)\s+(?:media\s+)?requests?)",
            t,
        ):
            return True

    if _STORAGE_RE.search(t):
        if _HOW_IS_RE.search(t):
            return True
        if _QUERY_START_RE.search(t) and _STATUS_WORD_RE.search(t):
            return True
        if re.fullmatch(
            r"(?:nas|synology|diskstation|storage|drives?|volumes?)\s+"
            r"(?:status|health|space|usage|temperature|temp|warnings?|alerts?)",
            t,
        ):
            return True

    if _INTERNET_RE.search(t):
        if _HOW_IS_RE.search(t):
            return True
        if re.search(
            r"\b(internet|speedtest|network\s+speed|connection\s+speed)\b.*"
            r"\b(status|speed|ping|working|running|online|offline|up|down)\b",
            t,
        ):
            return True
        if _QUERY_START_RE.search(t) and re.search(
            r"\b(status|speed|ping|working|running|online|offline|up|down)\b",
            t,
        ):
            return True

    if _CAMERA_RE.search(t):
        if _QUERY_START_RE.search(t) and re.search(
            r"\b(status|alerts?|motion|person|vehicle|package|visitor)\b",
            t,
        ):
            return True
        if re.fullmatch(
            r"(?:any\s+)?(?:camera|cameras|reolink)\s+(?:status|alerts?|motion)",
            t,
        ):
            return True

    return False


def looks_like_homelab_query(text: str) -> bool:
    """Backward-compatible name for the canonical homelab intent predicate."""
    return looks_like_homelab_intent(text)


def _say(maybe_say: Optional[MaybeSay], text: str) -> str:
    if callable(maybe_say):
        return maybe_say(text)
    return text


def _state_map(states_snapshot: Optional[list]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for st in states_snapshot or []:
        eid = st.get("entity_id")
        if isinstance(eid, str) and eid:
            out[eid] = st
    return out


def _entities(config: Optional[dict], service: str) -> Dict[str, str]:
    if not isinstance(config, dict):
        return {}
    svc = config.get(service) or {}
    ents = svc.get("ha_entities") or {}
    return ents if isinstance(ents, dict) else {}


def _alert_entities(config: Optional[dict], service: str) -> list[str]:
    if not isinstance(config, dict):
        return []
    svc = config.get(service) or {}
    vals = svc.get("alert_entities") or []
    return [str(v).strip() for v in vals if str(v).strip()]


def _entity_state(states: Dict[str, dict], entity_id: Optional[str]) -> Optional[str]:
    if not entity_id:
        return None
    st = states.get(str(entity_id).strip())
    if not st:
        return None
    val = st.get("state")
    if val is None:
        return None
    return str(val).strip()


def _entity_attr(states: Dict[str, dict], entity_id: Optional[str], key: str, default=None):
    if not entity_id:
        return default
    st = states.get(str(entity_id).strip()) or {}
    attrs = st.get("attributes") or {}
    return attrs.get(key, default)


def _friendly_name(states: Dict[str, dict], entity_id: str) -> str:
    name = _entity_attr(states, entity_id, "friendly_name", "")
    if name:
        return str(name).strip()
    tail = entity_id.split(".", 1)[-1].replace("_", " ")
    return tail.strip().title()


def _available(value: Optional[str]) -> bool:
    return bool(value) and str(value).strip().lower() not in {
        "unknown",
        "unavailable",
        "none",
        "",
    }


def _num(value: Optional[str]) -> Optional[float]:
    if not _available(value):
        return None
    try:
        return float(str(value).strip())
    except Exception:
        return None


def _int_text(value: Optional[str]) -> Optional[str]:
    n = _num(value)
    if n is None:
        return None
    return str(int(round(n)))


def _float_text(value: Optional[str], places: int = 1) -> Optional[str]:
    n = _num(value)
    if n is None:
        return None
    if abs(n - round(n)) < 0.005:
        return str(int(round(n)))
    return f"{n:.{places}f}".rstrip("0").rstrip(".")


def _unit(states: Dict[str, dict], entity_id: Optional[str]) -> str:
    if not entity_id:
        return ""
    u = _entity_attr(states, entity_id, "unit_of_measurement", "") or ""
    return str(u).strip()


def _value_with_unit(states: Dict[str, dict], entity_id: Optional[str], *, places: int = 1) -> Optional[str]:
    val = _float_text(_entity_state(states, entity_id), places=places)
    if val is None:
        return None
    unit = _unit(states, entity_id)
    return f"{val} {unit}".strip()


def _plural(noun: str, count_text: Optional[str]) -> str:
    try:
        count = int(count_text or "0")
    except Exception:
        count = 0
    return noun if count == 1 else noun + "s"


def _join(parts: Iterable[Optional[str]]) -> str:
    vals = [p for p in parts if p]
    if not vals:
        return ""
    if len(vals) == 1:
        return vals[0]
    return ", ".join(vals[:-1]) + ", and " + vals[-1]


# Download and qBittorrent status/actions

def _qb_counts(states: Dict[str, dict], cfg: Optional[dict]) -> Dict[str, Optional[str]]:
    ents = _entities(cfg, "qbittorrent")
    return {
        "status": _entity_state(states, ents.get("status")),
        "connection": _entity_state(states, ents.get("connection")),
        "download_speed": _value_with_unit(states, ents.get("download_speed"), places=2),
        "upload_speed": _value_with_unit(states, ents.get("upload_speed"), places=2),
        "all": _int_text(_entity_state(states, ents.get("all_torrents"))),
        "active": _int_text(_entity_state(states, ents.get("active_torrents"))),
        "inactive": _int_text(_entity_state(states, ents.get("inactive_torrents"))),
        "paused": _int_text(_entity_state(states, ents.get("paused_torrents"))),
        "errored": _int_text(_entity_state(states, ents.get("errored_torrents"))),
    }


def _torrent_count_response(t: str, states: Dict[str, dict], cfg: Optional[dict]) -> Optional[str]:
    if not re.search(r"\b(torrent|torrents|download|downloads|downloading|qbittorrent)\b", t):
        return None

    direct = qbittorrent_snapshot()
    direct_summary = summarize_torrents(direct.value.get("torrents", [])) if direct.ok else None

    if direct_summary and re.search(r"\b(completed|complete|finished|done)\b", t):
        val = str(direct_summary["completed"])
        return f"qBittorrent has {val} completed {_plural('torrent', val)}."

    q = _qb_counts(states, cfg)

    if re.search(r"\b(completed|complete|finished|done)\b", t):
        inactive = q.get("inactive")
        if inactive is not None:
            return (
                "Home Assistant does not expose completed torrent count separately. "
                f"qBittorrent reports {inactive} inactive {_plural('torrent', inactive)}."
            )
        return "Home Assistant does not expose completed torrent count yet."

    wanted = None
    if re.search(r"\bactive\b", t):
        wanted = "active"
    elif re.search(r"\bpaused\b", t):
        wanted = "paused"
    elif re.search(r"\berror(?:ed|s)?\b", t):
        wanted = "errored"
    elif re.search(r"\binactive\b", t):
        wanted = "inactive"
    elif re.search(r"\b(all|total)\b", t):
        wanted = "all"

    if wanted:
        if direct_summary:
            direct_key = "total" if wanted == "all" else wanted
            if direct_key in direct_summary:
                val = str(direct_summary[direct_key])
                label = "total" if wanted == "all" else wanted
                return f"qBittorrent has {val} {label} {_plural('torrent', val)}."
        val = q.get(wanted)
        if val is None:
            return f"I cannot read {wanted} torrent count from Home Assistant right now."
        label = "total" if wanted == "all" else wanted
        return f"qBittorrent has {val} {label} {_plural('torrent', val)}."

    if re.search(r"\b(how many|status|doing|speed|speeds)\b", t):
        parts = []
        if direct_summary:
            parts.append(f"{direct_summary['active']} active")
            parts.append(f"{direct_summary['completed']} completed")
            if direct_summary["paused"]:
                parts.append(f"{direct_summary['paused']} paused")
            if direct_summary["errored"]:
                parts.append(f"{direct_summary['errored']} errored")
            parts.append(f"{direct_summary['total']} total")
            return "qBittorrent: " + _join(parts) + "."
        if _available(q.get("status")):
            parts.append(f"status {q['status'].replace('_', ' ')}")
        if q.get("active") is not None:
            parts.append(f"{q['active']} active")
        if q.get("paused") is not None:
            parts.append(f"{q['paused']} paused")
        if q.get("errored") is not None:
            parts.append(f"{q['errored']} errored")
        if q.get("all") is not None:
            parts.append(f"{q['all']} total")
        speeds = _join([
            f"{q['download_speed']} down" if q.get("download_speed") else None,
            f"{q['upload_speed']} up" if q.get("upload_speed") else None,
        ])
        if speeds:
            parts.append(speeds)
        if parts:
            return "qBittorrent: " + _join(parts) + "."
        return "I cannot read qBittorrent from Home Assistant right now."

    return None


def _download_names_response(t: str, states: Dict[str, dict], cfg: Optional[dict]) -> Optional[str]:
    if not re.search(r"\bwhat\b.*\b(download|downloading|grabbing|processing)\b", t):
        return None
    if not re.search(r"\b(movie|movies|show|shows|episode|episodes|torrent|torrents|media)\b", t):
        return None

    direct = qbittorrent_snapshot()
    if direct.ok:
        summary = summarize_torrents(direct.value.get("torrents", []))
        names = summary.get("active_names") or []
        if names:
            extra = summary["active_name_count"] - len(names)
            suffix = f", plus {extra} more" if extra > 0 else ""
            return "Active qBittorrent downloads: " + _join(names) + suffix + "."
        return "qBittorrent has no active downloads right now."

    ov = _entities(cfg, "overseerr")
    li = _entities(cfg, "lidarr")
    so = _entities(cfg, "sonarr")

    processing = _int_text(_entity_state(states, ov.get("processing_requests")))
    pending = _int_text(_entity_state(states, ov.get("pending_requests")))
    lidarr_queue = _int_text(_entity_state(states, li.get("queue")))
    sonarr_upcoming = _int_text(_entity_state(states, so.get("upcoming")))

    parts = []
    if processing is not None:
        parts.append(f"Seerr has {processing} processing {_plural('request', processing)}")
    if pending is not None:
        parts.append(f"{pending} pending")
    if lidarr_queue is not None:
        parts.append(f"Lidarr has {lidarr_queue} queued {_plural('album', lidarr_queue)}")
    if sonarr_upcoming is not None:
        parts.append(f"Sonarr has {sonarr_upcoming} upcoming {_plural('episode', sonarr_upcoming)}")

    prefix = _join(parts)
    suffix = "Home Assistant does not expose active download titles yet; that needs direct qBittorrent or Seerr API support."
    return (prefix + ". " + suffix) if prefix else suffix


def _pause_completed_response(t: str) -> Optional[str]:
    if not re.search(r"\bpause\b", t):
        return None
    if not re.search(r"\b(completed|complete|finished|done)\b", t):
        return None
    if not re.search(r"\b(download|downloads|torrent|torrents)\b", t):
        return None
    if not allow_real_effects():
        return "Test preview: would pause completed qBittorrent downloads."
    result = qbittorrent_pause_completed()
    if result.ok:
        paused = int((result.value or {}).get("paused") or 0)
        completed = int((result.value or {}).get("completed") or 0)
        if paused:
            return f"Paused {paused} completed {_plural('torrent', str(paused))} in qBittorrent."
        if completed:
            return "All completed qBittorrent downloads were already paused."
        return "qBittorrent has no completed downloads to pause."
    return "I cannot reach qBittorrent directly right now, so I did not pause anything."


# Network and Uptime Kuma health

def _internet_response(t: str, states: Dict[str, dict], cfg: Optional[dict]) -> Optional[str]:
    if not re.search(r"\b(internet|speedtest|network speed|connection speed)\b", t):
        return None
    ents = _entities(cfg, "speedtest")
    down = _value_with_unit(states, ents.get("download"), places=1)
    up = _value_with_unit(states, ents.get("upload"), places=1)
    ping = _value_with_unit(states, ents.get("ping"), places=3)
    body = _join([
        f"download {down}" if down else None,
        f"upload {up}" if up else None,
        f"ping {ping}" if ping else None,
    ])
    return f"Internet speedtest: {body}." if body else "I cannot read the Speedtest sensors right now."


def _kuma_monitor_parts(snapshot: dict) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    monitors = snapshot.get("monitors") if isinstance(snapshot, dict) else []
    if not isinstance(monitors, list):
        monitors = []
    up = [m for m in monitors if m.get("status") == "up"]
    down = [m for m in monitors if m.get("status") == "down"]
    pending = [m for m in monitors if m.get("status") == "pending"]
    maintenance = [m for m in monitors if m.get("status") == "maintenance"]
    return up, down, pending, maintenance


def _kuma_names(monitors: list[dict], *, limit: int = 5) -> list[str]:
    names = [str(m.get("name") or "").strip() for m in monitors if str(m.get("name") or "").strip()]
    return names[:limit]


def _kuma_brief() -> Optional[str]:
    result = uptime_kuma_snapshot()
    if not result.ok:
        if result.error == "not_configured":
            return None
        if result.error == "status_page_not_found":
            return "Uptime Kuma is running, but the home status page is not published yet"
        return "Uptime Kuma is configured, but I cannot read its status page right now"

    snapshot = result.value or {}
    up, down, pending, maintenance = _kuma_monitor_parts(snapshot)
    total = len(up) + len(down) + len(pending) + len(maintenance)
    if total == 0:
        return "Uptime Kuma is reachable, but no monitors are published on its status page yet"
    if down:
        names = _kuma_names(down, limit=4)
        extra = len(down) - len(names)
        suffix = f", plus {extra} more" if extra > 0 else ""
        return f"Kuma reports {len(down)} down: {_join(names)}{suffix}"
    if pending:
        names = _kuma_names(pending, limit=3)
        return f"Kuma has {len(pending)} pending: {_join(names)}"
    if maintenance:
        names = _kuma_names(maintenance, limit=3)
        return f"Kuma has {len(maintenance)} in maintenance: {_join(names)}"
    return f"Kuma says all {total} monitored services are up"


def _kuma_response(t: str) -> Optional[str]:
    wants_kuma = re.search(
        r"\b(uptime\s*kuma|kuma|services?|service\s+status|anything\s+down|what'?s\s+down|"
        r"what\s+(?:is|are)\s+down|what'?s\s+broken|broken|outage|outages)\b",
        t,
    )
    if not wants_kuma:
        return None

    result = uptime_kuma_snapshot()
    if not result.ok:
        if result.error == "not_configured":
            return "Uptime Kuma is not configured in PiPhone yet."
        if result.error == "status_page_not_found":
            return "Uptime Kuma is running, but the home status page is not published yet."
        return "I can reach the homelab, but I cannot read Uptime Kuma's status page right now."

    snapshot = result.value or {}
    up, down, pending, maintenance = _kuma_monitor_parts(snapshot)
    total = len(up) + len(down) + len(pending) + len(maintenance)
    if total == 0:
        return "Uptime Kuma is reachable, but no monitors are published on its status page yet."

    if re.search(r"\b(anything|what'?s|what is|is anything).*\b(down|broken|failing|offline)\b", t) or re.search(r"\b(down|broken|outage|outages)\b", t):
        if down:
            names = _kuma_names(down, limit=6)
            extra = len(down) - len(names)
            suffix = f", plus {extra} more" if extra > 0 else ""
            verb = "is" if len(down) == 1 else "are"
            return f"{len(down)} monitored {_plural('service', str(len(down)))} {verb} down: {_join(names)}{suffix}."
        return f"No monitored services are down. Kuma says all {total} are up."

    problem_count = len(down) + len(pending)
    if problem_count:
        parts = []
        if down:
            parts.append(f"{len(down)} down: {_join(_kuma_names(down, limit=4))}")
        if pending:
            parts.append(f"{len(pending)} pending: {_join(_kuma_names(pending, limit=3))}")
        if maintenance:
            parts.append(f"{len(maintenance)} in maintenance")
        return "Uptime Kuma status: " + ". ".join(parts) + "."

    maintenance_note = f" {len(maintenance)} in maintenance." if maintenance else ""
    return f"Uptime Kuma status: all {total} monitored services are up.{maintenance_note}"


def _status_word(value: Optional[str]) -> Optional[str]:
    if not _available(value):
        return None
    return str(value).strip().replace("_", " ")


def _is_problem_status(value: Optional[str]) -> bool:
    val = (value or "").strip().lower()
    return bool(val) and val not in {"normal", "healthy", "ok", "off", "idle", "watching", "not_use"}


def _on_alerts(states: Dict[str, dict], cfg: Optional[dict], service: str) -> list[str]:
    active = []
    for eid in _alert_entities(cfg, service):
        if str(_entity_state(states, eid) or "").strip().lower() == "on":
            active.append(_friendly_name(states, eid))
    return active


def _short_nas_alert(label: str) -> str:
    s = str(label or "").strip()
    s = re.sub(r"^YoreNAS\s+", "", s, flags=re.I)
    s = re.sub(r"^yorenas\s+", "", s, flags=re.I)
    s = s.replace("Security status", "Security status")
    return s


def _fmt_measure(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return re.sub(r"\s+%", "%", str(value).strip())


def _status_phrase(status: Optional[str]) -> Optional[str]:
    s = (status or "").strip().lower()
    if not s:
        return None
    if s == "attention":
        return "needs attention"
    if s in {"degrade", "degraded"}:
        return "is degraded"
    if s == "normal":
        return "is normal"
    return f"status is {s}"


def _sentence_join(parts: Iterable[Optional[str]]) -> str:
    vals = [str(p).strip().rstrip(".") for p in parts if str(p or "").strip()]
    if not vals:
        return ""
    return ". ".join(vals) + "."


# Storage, media requests, and camera state

def _synology_volume_notes(states: Dict[str, dict], ents: Dict[str, str], *, include_temp: bool = False) -> list[str]:
    notes = []
    for idx in (1, 2):
        status = _status_word(_entity_state(states, ents.get(f"volume_{idx}_status")))
        used = _fmt_measure(_value_with_unit(states, ents.get(f"volume_{idx}_used"), places=1))
        temp = _fmt_measure(_value_with_unit(states, ents.get(f"volume_{idx}_temp"), places=1))
        bits = []
        if used:
            bits.append(f"at {used}")
        if _is_problem_status(status):
            phrase = _status_phrase(status)
            if phrase:
                bits.append(phrase)
        elif status:
            phrase = _status_phrase(status)
            if phrase:
                bits.append(phrase)
        if include_temp and temp:
            bits.append(f"temperature {temp}")
        if bits:
            notes.append(f"Volume {idx} is " + " and ".join(bits))
    return notes


def _synology_brief(states: Dict[str, dict], cfg: Optional[dict]) -> Optional[str]:
    ents = _entities(cfg, "synology")
    alerts = [_short_nas_alert(a) for a in _on_alerts(states, cfg, "synology")]
    volume_notes = _synology_volume_notes(states, ents)
    temp = _fmt_measure(_value_with_unit(states, ents.get("temperature"), places=1))
    cpu = _fmt_measure(_value_with_unit(states, ents.get("cpu_total"), places=1))
    memory = _fmt_measure(_value_with_unit(states, ents.get("memory_usage"), places=1))

    problems = []
    if volume_notes:
        problems.extend(volume_notes)
    if alerts:
        problems.extend(f"{alert} is flagged" for alert in alerts)

    if problems:
        tail = _join([
            f"Temperature is {temp}" if temp else None,
            f"CPU is {cpu}" if cpu else None,
            f"Memory is {memory}" if memory else None,
        ])
        return "YoreNAS needs attention: " + ". ".join(problems) + (f". {tail}." if tail else ".")

    healthy = _join([
        f"Temperature is {temp}" if temp else None,
        f"CPU is {cpu}" if cpu else None,
        f"Memory is {memory}" if memory else None,
    ])
    return f"YoreNAS looks healthy. {healthy}." if healthy else None


def _synology_parts(t: str, states: Dict[str, dict], cfg: Optional[dict]) -> list[str]:
    ents = _entities(cfg, "synology")
    parts = []

    volumes = []
    for idx in (1, 2):
        status = _status_word(_entity_state(states, ents.get(f"volume_{idx}_status")))
        used = _value_with_unit(states, ents.get(f"volume_{idx}_used"), places=1)
        temp = _value_with_unit(states, ents.get(f"volume_{idx}_temp"), places=1)
        bits = []
        if status:
            bits.append(f"status {status}")
        if used:
            bits.append(f"{used} used")
        if temp:
            bits.append(f"{temp}")
        if bits:
            volumes.append(f"Volume {idx} " + _join(bits))

    drives = []
    problem_drives = []
    for idx in (1, 2, 3, 4):
        status = _status_word(_entity_state(states, ents.get(f"drive_{idx}_status")))
        temp = _value_with_unit(states, ents.get(f"drive_{idx}_temp"), places=1)
        if status or temp:
            drives.append(f"Drive {idx} " + _join([status, temp]))
        if _is_problem_status(status):
            problem_drives.append(f"Drive {idx} {status}")

    cache = []
    for idx in (1, 2):
        status = _status_word(_entity_state(states, ents.get(f"cache_{idx}_status")))
        temp = _value_with_unit(states, ents.get(f"cache_{idx}_temp"), places=1)
        if status or temp:
            cache.append(f"M.2 {idx} " + _join([status, temp]))

    if re.search(r"\b(storage|space|volume|pool|pools|disk space|free|used)\b", t):
        parts.extend(_synology_volume_notes(states, ents, include_temp=False))
        usb_used = _fmt_measure(_value_with_unit(states, ents.get("usb_disk_2_partition_2_used"), places=1))
        usb_status = _status_word(_entity_state(states, ents.get("usb_disk_2_status")))
        if usb_used or _is_problem_status(usb_status):
            bits = [f"{usb_used} used" if usb_used else None]
            if _is_problem_status(usb_status):
                bits.append(_status_phrase(usb_status))
            parts.append("USB disk is " + " and ".join([b for b in bits if b]))
        return parts

    if re.search(r"\b(drive|drives|disk|disks|health|healthy|pool|pools|warning|warnings|alert|alerts)\b", t):
        alerts = [_short_nas_alert(a) for a in _on_alerts(states, cfg, "synology")]
        if problem_drives:
            parts.append("Drive warnings: " + _join(problem_drives))
        problem_volumes = []
        for idx in (1, 2):
            status = _status_word(_entity_state(states, ents.get(f"volume_{idx}_status")))
            if _is_problem_status(status):
                problem_volumes.append(f"Volume {idx} {_status_phrase(status)}")
        if problem_volumes:
            parts.append("Storage needs attention: " + _join(problem_volumes))
        if alerts:
            parts.append("Other alerts: " + _join(f"{alert} is flagged" for alert in alerts))
        if not problem_drives and drives:
            parts.insert(0, "Drive safety sensors are clear")
        if not problem_drives and not problem_volumes and not alerts and drives:
            parts.append(_join(drives))
        return parts

    if re.search(r"\b(temp|temperature|hot|thermal|cpu|memory|ram|load|throughput|network)\b", t):
        temp = _fmt_measure(_value_with_unit(states, ents.get("temperature"), places=1))
        cpu = _fmt_measure(_value_with_unit(states, ents.get("cpu_total"), places=1))
        memory = _fmt_measure(_value_with_unit(states, ents.get("memory_usage"), places=1))
        down = _fmt_measure(_value_with_unit(states, ents.get("download_throughput"), places=1))
        up = _fmt_measure(_value_with_unit(states, ents.get("upload_throughput"), places=1))
        parts.append(_join([
            f"Temperature is {temp}" if temp else None,
            f"CPU is {cpu}" if cpu else None,
            f"Memory is {memory}" if memory else None,
            f"Network is {down} down" if down else None,
            f"Upload is {up}" if up else None,
        ]))
        return parts

    brief = _synology_brief(states, cfg)
    if brief:
        return [brief.rstrip(".")]

    volume_warnings = []
    for idx in (1, 2):
        status = _status_word(_entity_state(states, ents.get(f"volume_{idx}_status")))
        used = _fmt_measure(_value_with_unit(states, ents.get(f"volume_{idx}_used"), places=1))
        if _is_problem_status(status):
            volume_warnings.append(f"Volume {idx} {_status_phrase(status)}")
        elif used:
            volume_warnings.append(f"Volume {idx} is at {used}")
    if volume_warnings:
        parts.append(_join(volume_warnings))

    temp = _fmt_measure(_value_with_unit(states, ents.get("temperature"), places=1))
    cpu = _fmt_measure(_value_with_unit(states, ents.get("cpu_total"), places=1))
    memory = _fmt_measure(_value_with_unit(states, ents.get("memory_usage"), places=1))
    if temp or cpu or memory:
        parts.append(_join([
            f"Temperature is {temp}" if temp else None,
            f"CPU is {cpu}" if cpu else None,
            f"Memory is {memory}" if memory else None,
        ]))

    dsm_update = _entity_state(states, ents.get("dsm_update"))
    plex_update = _entity_state(states, ents.get("plex_update"))
    update_parts = []
    if str(dsm_update or "").lower() == "on":
        update_parts.append("DSM update available")
    if str(plex_update or "").lower() == "on":
        update_parts.append("Plex update available")
    if update_parts:
        parts.append(_join(update_parts))

    return parts


def _synology_response(t: str, states: Dict[str, dict], cfg: Optional[dict]) -> Optional[str]:
    if not re.search(r"\b(synology|nas|yore\s*nas|yorenas|diskstation|storage pool|storage|drives?|volumes?)\b", t):
        return None
    if not re.search(r"\b(status|health|healthy|warning|warnings|alert|alerts|space|storage|pool|pools|volume|volumes|drive|drives|disk|disks|temperature|temp|hot|cpu|memory|ram|load|throughput|network|update|updates|how|any|is|are)\b", t):
        return None

    parts = [p for p in _synology_parts(t, states, cfg) if p]
    if parts:
        body = ". ".join(str(p).strip().rstrip(".") for p in parts) + "."
        if body.startswith("YoreNAS "):
            return body
        if re.search(r"\b(storage|space|volume|pool|pools|disk space|free|used)\b", t):
            return "Storage status: " + body
        return "YoreNAS status: " + body
    return "I cannot read Synology NAS status from Home Assistant right now."


def _media_requests_response(t: str, states: Dict[str, dict], cfg: Optional[dict]) -> Optional[str]:
    if not re.search(
        r"\b(overseerr|overseer|over\s+seer|seerr|seer|see\s+your|request|requests|radarr|sonarr|lidarr)\b",
        t,
    ):
        return None
    if not re.search(r"\b(status|pending|processing|available|issue|issues|queue|queued|upcoming|health|how many)\b", t):
        return None

    ov = _entities(cfg, "overseerr")
    ra = _entities(cfg, "radarr")
    so = _entities(cfg, "sonarr")
    li = _entities(cfg, "lidarr")

    if re.search(r"\b(pending|request|requests|overseerr|overseer|over\s+seer|seerr|seer|see\s+your)\b", t):
        direct = seerr_snapshot()
        if direct.ok:
            counts = (direct.value or {}).get("counts") or {}
            pending_items = (direct.value or {}).get("pending") or []
            pending = counts.get("pending")
            processing = counts.get("processing")
            available = counts.get("available")
            titles = [seerr_request_title(item) for item in pending_items[:3]]
            title_part = f" Pending: {_join(titles)}." if titles else ""
            body = _join([
                f"{pending} pending" if pending is not None else None,
                f"{processing} processing" if processing is not None else None,
                f"{available} available" if available is not None else None,
            ])
            return f"Seerr has {body}.{title_part}" if body else "I can reach Seerr, but I could not read request counts."

        pending = _int_text(_entity_state(states, ov.get("pending_requests")))
        processing = _int_text(_entity_state(states, ov.get("processing_requests")))
        available = _int_text(_entity_state(states, ov.get("available_requests")))
        issues = _int_text(_entity_state(states, ov.get("open_issues")))
        body = _join([
            f"{pending} pending" if pending is not None else None,
            f"{processing} processing" if processing is not None else None,
            f"{available} available" if available is not None else None,
            f"{issues} open {_plural('issue', issues)}" if issues is not None else None,
        ])
        return f"Seerr has {body}." if body else "I cannot read Seerr from Home Assistant right now."

    if re.search(r"\bsonarr\b", t):
        upcoming = _int_text(_entity_state(states, so.get("upcoming")))
        if upcoming is None:
            return "I cannot read Sonarr from Home Assistant right now."
        return f"Sonarr has {upcoming} upcoming {_plural('episode', upcoming)}."

    if re.search(r"\blidarr\b", t):
        queue = _int_text(_entity_state(states, li.get("queue")))
        disk = _value_with_unit(states, li.get("disk_space"), places=1)
        body = _join([
            f"{queue} queued {_plural('album', queue)}" if queue is not None else None,
            f"{disk} free" if disk else None,
        ])
        return f"Lidarr has {body}." if body else "I cannot read Lidarr from Home Assistant right now."

    if re.search(r"\bradarr\b", t):
        health = _entity_state(states, ra.get("health"))
        disk = _value_with_unit(states, ra.get("disk_movies"), places=1)
        movie = _entity_attr(states, ra.get("calendar"), "message", None)
        parts = []
        if _available(health):
            parts.append("healthy" if str(health).lower() == "off" else "has a health warning")
        if disk:
            parts.append(f"{disk} free")
        if movie:
            parts.append(f"next calendar item is {movie}")
        body = _join(parts)
        return f"Radarr is {body}." if body else "I cannot read Radarr from Home Assistant right now."

    return None


def _camera_response(t: str, states: Dict[str, dict], cfg: Optional[dict]) -> Optional[str]:
    if not re.search(r"\b(camera|cameras|reolink|motion|person|vehicle|package|visitor)\b", t):
        return None
    if not re.search(r"\b(alert|alerts|motion|person|vehicle|package|visitor|anything|status)\b", t):
        return None

    active = []
    missing = 0
    for eid in _alert_entities(cfg, "reolink"):
        st = _entity_state(states, eid)
        if st is None:
            missing += 1
            continue
        if str(st).strip().lower() == "on":
            active.append(_friendly_name(states, eid))

    if active:
        return "Camera alerts active: " + _join(active) + "."
    if missing:
        return "No camera alerts are active from the sensors I can read. Some configured camera sensors were not present."
    return "No camera alerts are active."


# Aggregate summary and public dispatch entry point

def _homelab_summary(states: Dict[str, dict], cfg: Optional[dict]) -> str:
    q = _qb_counts(states, cfg)
    ov = _entities(cfg, "overseerr")
    sp = _entities(cfg, "speedtest")
    li = _entities(cfg, "lidarr")
    ra = _entities(cfg, "radarr")

    parts = []

    kuma = _kuma_brief()
    if kuma:
        parts.append(kuma)

    direct_qb = qbittorrent_snapshot()
    if direct_qb.ok:
        q_summary = summarize_torrents(direct_qb.value.get("torrents", []))
        q_parts = [
            f"qBittorrent {q_summary['active']} active",
            f"{q_summary['completed']} completed",
            f"{q_summary['total']} total",
        ]
        if q_summary["errored"]:
            q_parts.append(f"{q_summary['errored']} errored")
        parts.append(_join(q_parts))
    elif _available(q.get("status")):
        q_parts = [f"qBittorrent is {q['status'].replace('_', ' ')}"]
        if q.get("active") is not None:
            q_parts.append(f"{q['active']} active")
        if q.get("errored") is not None and q.get("errored") != "0":
            q_parts.append(f"{q['errored']} errored")
        if q.get("download_speed"):
            q_parts.append(f"{q['download_speed']} down")
        parts.append(_join(q_parts))

    direct_seerr = seerr_snapshot()
    if direct_seerr.ok:
        counts = direct_seerr.value.get("counts", {})
        pending = counts.get("pending")
        processing = counts.get("processing")
        available = counts.get("available")
        parts.append(_join([
            f"Seerr {pending} pending" if pending is not None else None,
            f"{processing} processing" if processing is not None else None,
            f"{available} available" if available is not None else None,
        ]))
    else:
        pending = _int_text(_entity_state(states, ov.get("pending_requests")))
        processing = _int_text(_entity_state(states, ov.get("processing_requests")))
        issues = _int_text(_entity_state(states, ov.get("open_issues")))
        if any(v is not None for v in (pending, processing, issues)):
            parts.append(_join([
                f"Seerr {pending} pending" if pending is not None else None,
                f"{processing} processing" if processing is not None else None,
                f"{issues} open {_plural('issue', issues)}" if issues is not None else None,
            ]))

    down = _value_with_unit(states, sp.get("download"), places=1)
    up = _value_with_unit(states, sp.get("upload"), places=1)
    if down or up:
        parts.append(_join([
            f"internet {down} down" if down else None,
            f"{up} up" if up else None,
        ]))

    radarr_health = _entity_state(states, ra.get("health"))
    if _available(radarr_health):
        parts.append("Radarr healthy" if str(radarr_health).lower() == "off" else "Radarr has a health warning")

    synology_brief = _synology_brief(states, cfg)
    if synology_brief:
        parts.append(synology_brief.rstrip("."))

    lidarr_queue = _int_text(_entity_state(states, li.get("queue")))
    if lidarr_queue is not None and lidarr_queue != "0":
        parts.append(f"Lidarr {lidarr_queue} queued")

    active_cameras = []
    for eid in _alert_entities(cfg, "reolink"):
        if str(_entity_state(states, eid) or "").strip().lower() == "on":
            active_cameras.append(_friendly_name(states, eid))
    parts.append("camera alerts active" if active_cameras else "no camera alerts")

    if parts:
        return "Homelab status: " + _join(parts) + "."
    return "I cannot read homelab status from Home Assistant right now."


def handle_homelab_controls(
    tl: str,
    *,
    states_snapshot: Optional[list],
    maybe_say: Optional[MaybeSay] = None,
    service_config: Optional[dict] = None,
) -> Optional[str]:
    """Route one homelab query/action and return spoken text when claimed."""
    t = _norm(tl)
    if not t:
        return None

    if not looks_like_homelab_intent(t):
        return None

    try:
        if service_config is None:
            from app_config import HOMELAB_SERVICES as service_config
    except Exception:
        service_config = {}

    states = _state_map(states_snapshot)

    response = _pause_completed_response(t)
    if response is None and re.search(r"\bhomelab\b", t):
        response = _homelab_summary(states, service_config)
    if response is None:
        response = _kuma_response(t)
    if response is None:
        response = _internet_response(t, states, service_config)
    if response is None:
        response = _synology_response(t, states, service_config)
    if response is None:
        response = _camera_response(t, states, service_config)
    if response is None:
        response = _download_names_response(t, states, service_config)
    if response is None:
        response = _torrent_count_response(t, states, service_config)
    if response is None:
        response = _media_requests_response(t, states, service_config)

    if response is None:
        return None
    return _say(maybe_say, response)
