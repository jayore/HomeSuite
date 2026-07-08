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


def _ha_ws_request(msg_type: str, req_id: int):
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

        ws.send(json.dumps({
            "id": req_id,
            "type": msg_type,
        }))

        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") != req_id:
                continue
            if msg.get("type") != "result":
                raise RuntimeError(f"Unexpected websocket result shape: {msg!r}")
            if not msg.get("success"):
                raise RuntimeError(f"HA websocket request failed: {msg!r}")
            return msg.get("result")
    finally:
        try:
            ws.close()
        except Exception:
            pass


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
        areas = _ha_ws_request("config/area_registry/list", 1001)
        devices = _ha_ws_request("config/device_registry/list", 1002)
        entities = _ha_ws_request("config/entity_registry/list", 1003)

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


def ha_get_light_entities_for_area(area_id: str, *, refresh_if_needed: bool = True) -> List[str]:
    """
    Return exact light.* entity_ids belonging to the given HA area_id.

    Membership is determined by:
    * entity_registry.area_id directly, or
    * entity_registry.device_id -> device_registry.area_id
    """
    area_id = str(area_id or "").strip()
    if not area_id:
        return []

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
        if not entity_id.startswith("light."):
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
