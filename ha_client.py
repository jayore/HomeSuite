"""Small Home Assistant REST and WebSocket client boundary.

The runtime configures this module once with its Home Assistant URL and token.
Command modules use the service and state helpers instead of constructing HTTP
requests themselves. Registry lookups are cached briefly because area, device,
and entity metadata changes far less often than entity state.

Functions return explicit failure values and log transport errors; callers are
responsible for deciding whether a failed action should be spoken to the user.
"""

import json
import logging
import time
from typing import Optional, Dict, List, Any

import requests
import websocket


HA_SESSION = requests.Session()
HEADERS = {}

_HA_URL = None

_REGISTRY_CACHE: Dict[str, Any] = {
    "ts": 0.0,
    "areas": None,
    "devices": None,
    "entities": None,
}


def configure_ha(*, ha_url: str, ha_token: str) -> None:
    """
    Configure Home Assistant URL + auth headers used by this module.
    Must be called once by the main runtime after loading secrets.
    """
    global _HA_URL, HEADERS

    _HA_URL = (ha_url or "").strip().rstrip("/")
    tok = (ha_token or "").strip()

    HEADERS = {
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json",
    }


def call_ha_service(service: str, data: dict) -> bool:
    """
    Call HA service using the global HA_SESSION.
    `service` is like "light/turn_on" or "media_player/play_media".
    """
    if not _HA_URL:
        raise RuntimeError("ha_client.configure_ha() was not called (missing HA_URL).")

    url = f"{_HA_URL}/api/services/{service}"
    _t0 = time.time()

    try:
        _payload = (data or {}) if isinstance(data, dict) else {}
        _target = (
            _payload.get("entity_id")
            or _payload.get("entity_ids")
            or _payload.get("device_id")
            or _payload.get("area_id")
        )
        if _target:
            logging.info("HA_TARGET svc=%s target=%r", service, _target)
    except Exception:
        pass

    r = HA_SESSION.post(url, headers=HEADERS, json=(data or {}), timeout=10)
    logging.info("PERF_HA_POST svc=%s dt=%.3f status=%s", service, (time.time() - _t0), getattr(r, "status_code", None))
    return (r.status_code // 100) == 2


def ha_get_weather_forecasts(
    entity_id: str,
    *,
    forecast_type: str = "daily",
) -> Optional[list]:
    """Return forecast rows from Home Assistant's response-producing action.

    Modern weather entities expose current attributes as entity state and
    forecasts through ``weather.get_forecasts``. This helper deliberately
    keeps that response shape inside the HA client boundary.
    """
    if not _HA_URL:
        raise RuntimeError("ha_client.configure_ha() was not called (missing HA_URL).")

    entity_id = str(entity_id or "").strip()
    if not entity_id:
        return None

    url = f"{_HA_URL}/api/services/weather/get_forecasts?return_response"
    started = time.time()
    response = HA_SESSION.post(
        url,
        headers=HEADERS,
        json={"entity_id": entity_id, "type": forecast_type},
        timeout=10,
    )
    logging.info(
        "PERF_HA_FORECAST entity=%s type=%s dt=%.3f status=%s",
        entity_id,
        forecast_type,
        time.time() - started,
        getattr(response, "status_code", None),
    )
    if (response.status_code // 100) != 2:
        return None

    try:
        body = response.json() or {}
    except Exception:
        return None
    service_response = body.get("service_response") or {}
    entity_response = service_response.get(entity_id)
    if not isinstance(entity_response, dict) and len(service_response) == 1:
        entity_response = next(iter(service_response.values()))
    if not isinstance(entity_response, dict):
        return None
    forecasts = entity_response.get("forecast")
    return forecasts if isinstance(forecasts, list) else None


def ha_get_calendar_events(
    entity_ids,
    *,
    start_date_time: str,
    end_date_time: str,
) -> Optional[Dict[str, List[dict]]]:
    """Return events from Home Assistant's response-producing calendar action.

    Google and other calendar-provider credentials remain entirely inside Home
    Assistant. HomeSuite sends only configured ``calendar.*`` entity IDs and a
    bounded time window, then normalizes the provider response by entity.
    """
    if not _HA_URL:
        raise RuntimeError("ha_client.configure_ha() was not called (missing HA_URL).")

    if isinstance(entity_ids, str):
        requested = [entity_ids.strip()] if entity_ids.strip() else []
    else:
        requested = [str(value or "").strip() for value in (entity_ids or [])]
        requested = [value for value in requested if value]
    if not requested or not start_date_time or not end_date_time:
        return None

    url = f"{_HA_URL}/api/services/calendar/get_events?return_response"
    started = time.time()
    response = HA_SESSION.post(
        url,
        headers=HEADERS,
        json={
            "entity_id": requested,
            "start_date_time": str(start_date_time),
            "end_date_time": str(end_date_time),
        },
        timeout=10,
    )
    logging.info(
        "PERF_HA_CALENDAR entities=%d dt=%.3f status=%s",
        len(requested),
        time.time() - started,
        getattr(response, "status_code", None),
    )
    if (response.status_code // 100) != 2:
        return None

    try:
        body = response.json() or {}
    except Exception:
        return None
    service_response = body.get("service_response") or {}
    if not isinstance(service_response, dict):
        return None

    normalized: Dict[str, List[dict]] = {}
    for entity_id in requested:
        entity_response = service_response.get(entity_id) or {}
        events = entity_response.get("events") if isinstance(entity_response, dict) else None
        normalized[entity_id] = [row for row in (events or []) if isinstance(row, dict)]
    return normalized


def ha_get_states() -> Optional[list]:
    """
    Fetch full HA state snapshot (list of entity dicts) or None on failure.
    """
    if not _HA_URL:
        raise RuntimeError("ha_client.configure_ha() was not called (missing HA_URL).")

    _t0 = time.time()
    r = HA_SESSION.get(f"{_HA_URL}/api/states", headers=HEADERS, timeout=10)
    logging.info("PERF_HA_GET_STATES dt=%.3f status=%s", (time.time() - _t0), getattr(r, "status_code", None))
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def ha_get_state(entity_id: str) -> Optional[dict]:
    """
    Fetch the current HA state for a single entity. Returns the entity dict or None on failure.
    Use this instead of ha_get_states() when you only need one entity and freshness matters.
    """
    if not _HA_URL:
        raise RuntimeError("ha_client.configure_ha() was not called (missing HA_URL).")
    _t0 = time.time()
    r = HA_SESSION.get(f"{_HA_URL}/api/states/{entity_id}", headers=HEADERS, timeout=5)
    logging.info("PERF_HA_GET_STATE entity=%s dt=%.3f status=%s", entity_id, (time.time() - _t0), getattr(r, "status_code", None))
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def _ha_ws_url() -> str:
    if not _HA_URL:
        raise RuntimeError("ha_client.configure_ha() was not called (missing HA_URL).")
    if _HA_URL.startswith("https://"):
        return "wss://" + _HA_URL[len("https://"):] + "/api/websocket"
    if _HA_URL.startswith("http://"):
        return "ws://" + _HA_URL[len("http://"):] + "/api/websocket"
    raise RuntimeError(f"Unsupported HA URL for websocket conversion: {_HA_URL!r}")


def _ha_access_token() -> str:
    auth = HEADERS.get("Authorization") or ""
    prefix = "Bearer "
    if not auth.startswith(prefix):
        raise RuntimeError("Missing Bearer token in HA headers.")
    return auth[len(prefix):].strip()


def _ha_ws_requests(requests_by_id: Dict[int, str]) -> Dict[int, Any]:
    """Run multiple Home Assistant websocket requests over one connection."""
    ws = websocket.create_connection(_ha_ws_url(), timeout=10)

    try:
        hello = json.loads(ws.recv())
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"Unexpected websocket hello: {hello!r}")

        ws.send(json.dumps({
            "type": "auth",
            "access_token": _ha_access_token(),
        }))

        auth_resp = json.loads(ws.recv())
        if auth_resp.get("type") != "auth_ok":
            raise RuntimeError(f"HA websocket auth failed: {auth_resp!r}")

        pending = {int(req_id): str(msg_type) for req_id, msg_type in requests_by_id.items()}
        for req_id, msg_type in pending.items():
            ws.send(json.dumps({
                "id": req_id,
                "type": msg_type,
            }))

        results: Dict[int, Any] = {}
        while pending:
            msg = json.loads(ws.recv())
            req_id = msg.get("id")
            if req_id not in pending:
                continue
            if msg.get("type") != "result":
                raise RuntimeError(f"Unexpected websocket result shape: {msg!r}")
            if not msg.get("success"):
                raise RuntimeError(f"HA websocket request failed: {msg!r}")
            results[req_id] = msg.get("result")
            pending.pop(req_id, None)
        return results
    finally:
        try:
            # HA has already returned the requested data. Do not let a missing
            # close acknowledgement inherit the 10-second request timeout and
            # stall an otherwise complete room-state query.
            ws.close(timeout=0.25)
        except Exception:
            pass


def _ha_ws_request(msg_type: str, req_id: int):
    """Compatibility wrapper for one websocket request."""
    return _ha_ws_requests({req_id: msg_type}).get(req_id)


def _registry_cache_is_fresh(ttl_s: float) -> bool:
    try:
        ts = float(_REGISTRY_CACHE.get("ts") or 0.0)
    except Exception:
        return False
    return (time.time() - ts) < float(ttl_s)


def ha_refresh_registry_cache(force: bool = False, ttl_s: float = 300.0) -> bool:
    """
    Refresh area/device/entity registry cache from HA websocket API.
    """
    global _REGISTRY_CACHE

    if (not force) and _registry_cache_is_fresh(ttl_s):
        return True

    try:
        results = _ha_ws_requests({
            1001: "config/area_registry/list",
            1002: "config/device_registry/list",
            1003: "config/entity_registry/list",
        })
        areas = results.get(1001)
        devices = results.get(1002)
        entities = results.get(1003)

        _REGISTRY_CACHE = {
            "ts": time.time(),
            "areas": areas if isinstance(areas, list) else [],
            "devices": devices if isinstance(devices, list) else [],
            "entities": entities if isinstance(entities, list) else [],
        }
        logging.info(
            "HA_REGISTRY_CACHE_REFRESH ok areas=%s devices=%s entities=%s",
            len(_REGISTRY_CACHE["areas"]),
            len(_REGISTRY_CACHE["devices"]),
            len(_REGISTRY_CACHE["entities"]),
        )
        return True
    except Exception:
        logging.exception("HA_REGISTRY_CACHE_REFRESH_FAIL")
        return False


def ha_get_registry_snapshot(
    *,
    force: bool = False,
    ttl_s: float = 300.0,
) -> Optional[Dict[str, List[dict]]]:
    """Return a detached HA area/device/entity registry snapshot."""
    if not ha_refresh_registry_cache(force=force, ttl_s=ttl_s):
        return None
    return {
        key: [dict(row) for row in (_REGISTRY_CACHE.get(key) or []) if isinstance(row, dict)]
        for key in ("areas", "devices", "entities")
    }


def _ha_get_area_entities_from_template(area_id: str) -> Optional[List[str]]:
    """Use HA's template API for a fast, read-only area membership lookup."""
    if not _HA_URL:
        return None
    template = "{{ area_entities(" + json.dumps(area_id) + ") | tojson }}"
    started = time.time()
    try:
        response = HA_SESSION.post(
            f"{_HA_URL}/api/template",
            headers=HEADERS,
            json={"template": template},
            timeout=5,
        )
        logging.info(
            "PERF_HA_AREA_ENTITIES area=%s dt=%.3f status=%s",
            area_id,
            time.time() - started,
            getattr(response, "status_code", None),
        )
        if response.status_code != 200:
            return None
        rows = json.loads(response.text)
        if not isinstance(rows, list):
            return None
        return sorted({
            str(entity_id).strip()
            for entity_id in rows
            if isinstance(entity_id, str) and "." in entity_id
        })
    except Exception:
        logging.exception("HA_AREA_ENTITIES_TEMPLATE_FAIL area=%s", area_id)
        return None


def ha_get_entities_for_area(
    area_id: str,
    *,
    domains=None,
    refresh_if_needed: bool = True,
) -> List[str]:
    """Return registered entity IDs belonging to one Home Assistant area.

    ``domains`` may be a string or iterable of domain names. Membership comes
    from either the entity's direct area assignment or its device's area.
    """
    area_id = str(area_id or "").strip()
    if not area_id:
        return []

    if isinstance(domains, str):
        wanted_domains = {domains.strip().lower()} if domains.strip() else set()
    elif domains is None:
        wanted_domains = set()
    else:
        wanted_domains = {
            str(domain or "").strip().lower()
            for domain in domains
            if str(domain or "").strip()
        }

    template_entities = _ha_get_area_entities_from_template(area_id)
    if template_entities is not None:
        if not wanted_domains:
            return template_entities
        return [
            entity_id
            for entity_id in template_entities
            if entity_id.split(".", 1)[0].lower() in wanted_domains
        ]

    if refresh_if_needed:
        if not ha_refresh_registry_cache():
            return []

    entities = _REGISTRY_CACHE.get("entities") or []
    devices = _REGISTRY_CACHE.get("devices") or []

    device_area_by_id: Dict[str, str] = {}
    for dev in devices:
        if not isinstance(dev, dict):
            continue
        dev_id = str(dev.get("id") or "").strip()
        dev_area = str(dev.get("area_id") or "").strip()
        if dev_id:
            device_area_by_id[dev_id] = dev_area

    out: List[str] = []
    seen = set()

    for ent in entities:
        if not isinstance(ent, dict):
            continue

        entity_id = str(ent.get("entity_id") or "").strip()
        if "." not in entity_id:
            continue
        domain = entity_id.split(".", 1)[0].lower()
        if wanted_domains and domain not in wanted_domains:
            continue

        entity_area = str(ent.get("area_id") or "").strip()
        if entity_area == area_id:
            if entity_id not in seen:
                seen.add(entity_id)
                out.append(entity_id)
            continue

        device_id = str(ent.get("device_id") or "").strip()
        if device_id and device_area_by_id.get(device_id) == area_id:
            if entity_id not in seen:
                seen.add(entity_id)
                out.append(entity_id)

    return sorted(out)


def ha_get_light_entities_for_area(area_id: str, *, refresh_if_needed: bool = True) -> List[str]:
    """Return exact ``light.*`` entity IDs belonging to an HA area."""
    return ha_get_entities_for_area(
        area_id,
        domains={"light"},
        refresh_if_needed=refresh_if_needed,
    )
