"""unified_server.py — In-process aiohttp HTTP/WS server + HA WebSocket subscription.

Runs inside homesuite.service as a background daemon thread, sharing the
process's memory (including command_dispatch state and the cache-backed
ha_get_states swap). Replaces the retired piphone-wsh.service.

Public API:
    start_in_background_thread(*, port, api_key, ha_url, ha_token,
                               runtime_module) -> None
    shutdown(timeout=5.0) -> None

Threading model:
  - Caller (main runtime thread) invokes start_in_background_thread().
  - A single daemon thread named "unified_server" is created.
  - Inside that thread: asyncio.new_event_loop() owns aiohttp + HA WS.
  - Command handlers run in a 1-worker ThreadPoolExecutor ("homesuite_cmd")
    so they're sequential and don't block the asyncio loop.
  - Sync code elsewhere (PTT path, scheduler, buttons) reads from
    entity_cache via command_dispatch's swapped ha_get_states callable.
    Cache mutation happens only on the asyncio thread; reads use a
    dict(entity_cache) snapshot which is atomic under CPython's GIL.

Gotcha mitigations (per SPIKE doc):
  - G1 (circular imports): no module-level import of main; module ref
    is passed in via start_in_background_thread().
  - G2 (aiohttp shutdown): shutdown() coordinates clean runner.cleanup()
    + loop.stop() across threads; TCPSite uses reuse_address=True.
  - G5 (logging): no basicConfig — relies on main runtime's root-logger setup.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hmac
import ipaddress
import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

import aiohttp
from aiohttp import web, WSMsgType

import ha_room_state
from home_registry import ROOMS, DEFAULT_ROOM, build_manifest
from event_log import log_command_event
from interaction_flow import handle_text_interaction
from request_context import (
    build_request_context,
    replace_current_request_context,
    set_current_request_context,
)
from voice_timing import timing_with_brain_receive, unix_time_ms
from wakeword_arbitration import PROTOCOL_VERSION, WakewordArbitrator

log = logging.getLogger("unified_server")

# ---------------------------------------------------------------------------
# Shared state (mutated only on the asyncio loop thread, except as noted)
# ---------------------------------------------------------------------------

# {entity_id: {"state": str, "attrs": dict, "lu": float}}
entity_cache: Dict[str, Dict[str, Any]] = {}

# Tracks whether the HA WS subscription is currently delivering events.
# When False, cache-backed reads fall back to direct REST.
_ha_ws_connected: bool = False

# {aiohttp WebSocketResponse -> room_id | None}
connected_clients: Dict[Any, Optional[str]] = {}

# Wake-word frontends use a separate, device-addressed channel. Browser/state
# clients remain room broadcasts and cannot participate in arbitration.
connected_satellites: Dict[str, Any] = {}
satellite_metadata: Dict[str, Dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Module-level config (set once by start_in_background_thread)
# ---------------------------------------------------------------------------

_API_KEY: str = ""
_HA_URL: str = ""
_HA_TOKEN: str = ""
_HA_WS_URL: str = ""
_PORT: int = 8765
_RUNTIME_MODULE: Any = None  # main runtime module reference (passed in)
_CMD_EXECUTOR: Optional[concurrent.futures.ThreadPoolExecutor] = None
_AUDIO_EXECUTOR: Optional[concurrent.futures.ThreadPoolExecutor] = None
_WAKEWORD_ARBITRATOR: Optional[WakewordArbitrator] = None

# Thread / asyncio handles (for shutdown coordination)
_SERVER_THREAD: Optional[threading.Thread] = None
_SERVER_LOOP: Optional[asyncio.AbstractEventLoop] = None
_SERVER_RUNNER: Optional[web.AppRunner] = None
_SERVER_STARTED_EVT: threading.Event = threading.Event()

# Captured pre-swap REST callbacks (for fallback when cache is cold)
_REAL_HA_GET_STATES = None
_REAL_HA_GET_STATE = None


# ---------------------------------------------------------------------------
# Cache-backed dispatch swaps
# ---------------------------------------------------------------------------


def _cache_backed_ha_get_states():
    """Return entity_cache as a REST-shape list, or fall back to REST."""
    if not _ha_ws_connected or not entity_cache:
        return _REAL_HA_GET_STATES() if _REAL_HA_GET_STATES else None
    return ha_room_state.cache_to_states_list(dict(entity_cache))


def _cache_backed_ha_get_state(entity_id: str):
    """Return a single entity in REST shape from cache, or fall back to REST.

    Matches the behavior of ha_client.ha_get_state() (single-entity REST fetch
    added 2026-05-31 for ATV seek freshness). When cache is warm, returns a
    dict in the same shape REST would; otherwise delegates to REST.
    """
    if _ha_ws_connected and entity_cache:
        entry = entity_cache.get(entity_id)
        if entry is not None:
            lu = entry.get("lu")
            try:
                lu_iso = (
                    datetime.utcfromtimestamp(float(lu)).isoformat() + "+00:00"
                    if lu else ""
                )
            except Exception:
                lu_iso = ""
            return {
                "entity_id": entity_id,
                "state": entry.get("state") or "",
                "attributes": entry.get("attrs") or {},
                "last_updated": lu_iso,
                "last_changed": lu_iso,
            }
    return _REAL_HA_GET_STATE(entity_id) if _REAL_HA_GET_STATE else None


def _install_cache_swaps() -> None:
    """Replace command_dispatch's REST callbacks with cache-backed versions.

    Called once at server start. The swap functions themselves fall back to
    REST when _ha_ws_connected is False or cache is empty, so it's safe to
    install before the WS has connected.
    """
    global _REAL_HA_GET_STATES, _REAL_HA_GET_STATE
    import command_dispatch
    _REAL_HA_GET_STATES = command_dispatch.ha_get_states
    _REAL_HA_GET_STATE = command_dispatch.ha_get_state
    command_dispatch.ha_get_states = _cache_backed_ha_get_states
    command_dispatch.ha_get_state = _cache_backed_ha_get_state
    log.info("CACHE_SWAP_INSTALLED ha_get_states + ha_get_state now cache-backed")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _request_api_key(request: web.Request, *, allow_query: bool = False) -> str:
    """Read the shared client key without inventing a second auth scheme."""
    incoming = str(request.headers.get("X-API-Key", "") or "").strip()
    if incoming:
        return incoming

    authorization = str(request.headers.get("Authorization", "") or "").strip()
    scheme, separator, value = authorization.partition(" ")
    if separator and scheme.lower() == "bearer" and value.strip():
        return value.strip()

    if allow_query:
        try:
            return str(request.rel_url.query.get("api_key", "") or "").strip()
        except Exception:
            return ""
    return ""


def _auth_ok(request: web.Request, *, allow_query: bool = False) -> bool:
    if not _API_KEY:
        return False
    incoming = _request_api_key(request, allow_query=allow_query)
    return bool(incoming and hmac.compare_digest(incoming, _API_KEY))


def _internal_auth_ok(request: web.Request) -> bool:
    """Restrict management-only runtime routes to authenticated loopback calls."""
    if not _auth_ok(request):
        return False
    remote = str(request.remote or "").strip()
    try:
        return ipaddress.ip_address(remote).is_loopback
    except ValueError:
        return remote.lower() == "localhost"


# ---------------------------------------------------------------------------
# Request context / payload helpers (ported verbatim from piphone_wsh.py)
# ---------------------------------------------------------------------------


def _clean_optional(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _payload_text(data: Dict[str, Any]) -> str:
    for key in ("text", "transcript", "command"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _source_type_default(*, source_id: Optional[str], origin: Optional[str], source_type: Optional[str]) -> Optional[str]:
    if source_type:
        return source_type
    oid = (origin or "").strip().lower()
    sid = (source_id or "").strip().lower()
    if "satellite" in oid or "satellite" in sid:
        return "satellite"
    if sid and sid != "http":
        return "remote"
    return "http"


def _build_context_from_payload(data: Dict[str, Any]):
    source_id = _clean_optional(data.get("source_id")) or "http"
    origin = _clean_optional(data.get("origin")) or "http"
    source_type = _clean_optional(data.get("source_type"))
    source_type = _source_type_default(source_id=source_id, origin=origin, source_type=source_type)
    source_room = _clean_optional(data.get("source_room"))
    effective_target_room = (
        _clean_optional(data.get("effective_target_room"))
        or _clean_optional(data.get("target_room"))
    )
    return build_request_context(
        source_id=source_id,
        source_type=source_type,
        origin=origin,
        source_room=source_room,
        effective_target_room=effective_target_room,
    )


def _interaction_result_to_payload(
    result,
    *,
    text: str,
    request_id: Optional[str],
    response_mode: Optional[str],
    request_ctx,
    stt_meta,
    timing_meta=None,
):
    handled = bool(getattr(result, "handled", False))
    action_occurred = bool(getattr(result, "action_occurred", False))
    response_text = str(getattr(result, "response_text", "") or "").strip()
    source = str(getattr(result, "source", "") or "").strip() or None
    payload = {
        "ok": True,
        "handled": handled,
        "action_occurred": action_occurred,
        "text": response_text if response_text else None,
        "response": response_text,
        "source": source,
        "request_id": request_id,
        "response_mode": response_mode,
        "context": request_ctx.to_log_dict() if request_ctx else None,
    }
    if stt_meta is not None:
        payload["stt"] = stt_meta
    if timing_meta is not None:
        payload["timing"] = timing_meta
    return payload


def _arbitration_terminal_payload(
    *,
    request_id: Optional[str],
    interaction_id: str,
    disposition: str,
    reason: str,
    winner_source_id: str = "",
) -> dict[str, Any]:
    """Return a non-error terminal result that voice satellites render silently."""
    return {
        "ok": True,
        "handled": False,
        "action_occurred": False,
        "text": None,
        "response": "",
        "source": "arbitration_suppressed",
        "request_id": request_id,
        "disposition": disposition,
        "arbitration": {
            "interaction_id": interaction_id or None,
            "disposition": disposition,
            "reason": reason,
            "winner_source_id": winner_source_id or None,
        },
    }


# ---------------------------------------------------------------------------
# Sync command runner (runs in _CMD_EXECUTOR thread)
# ---------------------------------------------------------------------------


def _run_command_sync(text: str, request_ctx) -> Any:
    """Execute the command pipeline synchronously. Thread-safe via thread-local ctx."""
    previous_ctx = replace_current_request_context(request_ctx)
    try:
        t0 = time.monotonic()
        result = handle_text_interaction(_RUNTIME_MODULE, text)
        log_command_event(text, request_ctx, result, int((time.monotonic() - t0) * 1000))
        return result
    finally:
        set_current_request_context(previous_ctx)


# ---------------------------------------------------------------------------
# WebSocket broadcast
# ---------------------------------------------------------------------------


async def _broadcast_room(room_id: str) -> None:
    if not connected_clients:
        return
    payload = ha_room_state.build_room_state(room_id, entity_cache)
    msg = json.dumps(payload)
    dead = []
    for ws, client_room in list(connected_clients.items()):
        if (client_room or DEFAULT_ROOM) == room_id:
            try:
                await ws.send_str(msg)
            except Exception:
                dead.append(ws)
    for ws in dead:
        connected_clients.pop(ws, None)


async def _broadcast_command_ack(room_id: str, text: str, result: Any) -> None:
    if not connected_clients:
        return
    ack = {
        "event": "command_ack",
        "room": room_id,
        "text": text,
        "response": str(getattr(result, "response_text", "") or "").strip() or None,
        "handled": bool(getattr(result, "handled", False)),
        "action_occurred": bool(getattr(result, "action_occurred", False)),
    }
    msg = json.dumps(ack)
    dead = []
    for ws, client_room in list(connected_clients.items()):
        if (client_room or DEFAULT_ROOM) == room_id:
            try:
                await ws.send_str(msg)
            except Exception:
                dead.append(ws)
    for ws in dead:
        connected_clients.pop(ws, None)


# ---------------------------------------------------------------------------
# Wake-word satellite coordination
# ---------------------------------------------------------------------------


async def _send_satellite_message(source_id: str, payload: dict[str, Any]) -> None:
    ws = connected_satellites.get(str(source_id or "").strip())
    if ws is None or ws.closed:
        return
    try:
        await ws.send_json(payload)
        if payload.get("type") == "wakeword_decision":
            log.info(
                "WAKEWORD_ARBITRATION_DECISION source_id=%r candidate_id=%r "
                "disposition=%r winner=%r nodes=%s hold_ms=%s candidate_total=%r "
                "winner_total=%r reason=%r",
                source_id,
                payload.get("candidate_id"),
                payload.get("disposition"),
                payload.get("winner_source_id"),
                payload.get("eligible_wakeword_nodes"),
                payload.get("election_hold_ms"),
                (payload.get("candidate_score") or {}).get("total"),
                (payload.get("winner_score") or {}).get("total"),
                payload.get("reason"),
            )
    except Exception:
        log.warning("SATELLITE_COORDINATION_SEND_FAIL source_id=%r", source_id)


async def _broadcast_satellite_cluster_state() -> None:
    arbitrator = _WAKEWORD_ARBITRATOR
    if arbitrator is None or not connected_satellites:
        return
    payload = arbitrator.cluster_state()
    for source_id in list(connected_satellites):
        await _send_satellite_message(source_id, payload)


def _build_wakeword_arbitrator() -> WakewordArbitrator:
    try:
        import app_config

        election_window_ms = int(
            getattr(app_config, "WAKEWORD_ARBITRATION_ELECTION_WINDOW_MS", 180)
        )
        cohort_window_ms = int(
            getattr(app_config, "WAKEWORD_ARBITRATION_COHORT_WINDOW_MS", 700)
        )
        lease_seconds = float(
            getattr(app_config, "WAKEWORD_ARBITRATION_LEASE_SECONDS", 30.0)
        )
    except Exception:
        election_window_ms = 180
        cohort_window_ms = 700
        lease_seconds = 30.0
    return WakewordArbitrator(
        _send_satellite_message,
        election_window_ms=election_window_ms,
        cohort_window_ms=cohort_window_ms,
        lease_seconds=lease_seconds,
    )


# ---------------------------------------------------------------------------
# HA WebSocket subscription loop
# ---------------------------------------------------------------------------


async def ha_subscription_loop() -> None:
    global _ha_ws_connected
    retry_delay = 2.0
    while True:
        try:
            log.info("Connecting to HA WebSocket: %s", _HA_WS_URL)
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(_HA_WS_URL, heartbeat=30) as ws:
                    retry_delay = 2.0

                    msg = await ws.receive_json()
                    if msg.get("type") != "auth_required":
                        log.error("Expected auth_required, got: %s", msg.get("type"))
                        continue

                    await ws.send_json({"type": "auth", "access_token": _HA_TOKEN})
                    msg = await ws.receive_json()
                    if msg.get("type") != "auth_ok":
                        log.error("HA auth failed: %s", msg)
                        continue

                    log.info("HA authenticated OK (HA version: %s)", msg.get("ha_version", "?"))

                    await ws.send_json({"id": 1, "type": "subscribe_entities"})

                    _ha_ws_connected = True
                    log.info("HA WS connected — ha_get_states now cache-backed")

                    async for msg in ws:
                        if msg.type != WSMsgType.TEXT:
                            continue
                        try:
                            envelope = json.loads(msg.data)
                        except json.JSONDecodeError:
                            continue
                        if envelope.get("type") != "event" or envelope.get("id") != 1:
                            continue
                        changed = ha_room_state.process_ha_event(
                            envelope.get("event") or {}, entity_cache
                        )
                        for room_id in ha_room_state.changed_rooms(changed):
                            await _broadcast_room(room_id)

        except aiohttp.ClientError as e:
            log.error("HA connection error: %s — retry in %.0fs", e, retry_delay)
        except Exception:
            log.exception("HA loop error — retry in %.0fs", retry_delay)
        finally:
            _ha_ws_connected = False

        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 60.0)


# ---------------------------------------------------------------------------
# HTTP route handlers
# ---------------------------------------------------------------------------


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "homesuite_unified", "phase": "4_inproc"})


async def handle_manifest(request: web.Request) -> web.Response:
    if not _auth_ok(request):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    try:
        manifest = build_manifest()
    except Exception:
        log.exception("MANIFEST_BUILD_FAIL")
        return web.json_response({"ok": False, "error": "manifest_build_failed"}, status=500)
    payload = {"ok": True}
    payload.update(manifest)
    return web.json_response(payload)


async def handle_state(request: web.Request) -> web.Response:
    if not _auth_ok(request):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    room_id = request.match_info.get("room_id", "").strip()
    if not room_id or room_id not in ROOMS:
        return web.json_response({"ok": False, "error": "unknown_room"}, status=404)
    try:
        state = ha_room_state.build_room_state(room_id, entity_cache)
    except Exception:
        log.exception("STATE_BUILD_FAIL room=%s", room_id)
        return web.json_response({"ok": False, "error": "state_build_failed"}, status=500)
    return web.json_response(state)


async def handle_command(request: web.Request) -> web.Response:
    if not _auth_ok(request):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)

    brain_received_at_ms = unix_time_ms()

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

    if not isinstance(data, dict):
        return web.json_response({"ok": False, "error": "json_object_required"}, status=400)

    try:
        calibration = _RUNTIME_MODULE.audio_calibration_status()
        if bool((calibration or {}).get("active")):
            return web.json_response(
                {"ok": False, "error": "Microphone calibration is active. Try the command again when it finishes."},
                status=409,
            )
    except AttributeError:
        pass

    text = _payload_text(data)
    if not text:
        return web.json_response({"ok": False, "error": "missing_text"}, status=400)

    request_id = _clean_optional(data.get("request_id"))
    response_mode = _clean_optional(data.get("response_mode")) or "text"
    stt_meta = data.get("stt") if isinstance(data.get("stt"), dict) else None
    timing_meta = timing_with_brain_receive(
        data.get("timing") if isinstance(data.get("timing"), dict) else None,
        received_at_ms=brain_received_at_ms,
    )

    request_ctx = _build_context_from_payload(data)
    interaction_id = _clean_optional(data.get("interaction_id")) or ""
    winner_token = _clean_optional(data.get("winner_token")) or ""
    arbitration_execute = False

    if interaction_id or winner_token:
        arbitrator = _WAKEWORD_ARBITRATOR
        if arbitrator is None or not interaction_id or not winner_token:
            payload = _arbitration_terminal_payload(
                request_id=request_id,
                interaction_id=interaction_id,
                disposition="suppressed",
                reason="invalid_winner_lease",
            )
            return web.json_response(payload)
        authorization = arbitrator.begin_command(
            source_id=str(request_ctx.source_id or ""),
            interaction_id=interaction_id,
            winner_token=winner_token,
        )
        authorization_state = str(authorization.get("state") or "rejected")
        if authorization_state == "cached":
            return web.json_response(
                authorization.get("payload") or {},
                status=int(authorization.get("status") or 200),
            )
        if authorization_state == "wait":
            try:
                cached_payload, cached_status = await asyncio.wait_for(
                    asyncio.shield(authorization["future"]),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                return web.json_response(
                    {"ok": False, "error": "winner_command_still_processing"},
                    status=504,
                )
            return web.json_response(cached_payload, status=int(cached_status))
        if authorization_state != "execute":
            payload = _arbitration_terminal_payload(
                request_id=request_id,
                interaction_id=interaction_id,
                disposition="suppressed",
                reason=str(authorization.get("reason") or "not_winner"),
                winner_source_id=str(authorization.get("winner_source_id") or ""),
            )
            log.info(
                "WAKEWORD_COMMAND_SUPPRESSED interaction_id=%r source_id=%r reason=%r",
                interaction_id,
                request_ctx.source_id,
                payload["arbitration"]["reason"],
            )
            return web.json_response(payload)
        arbitration_execute = True

    log.info(
        "[HTTP] command=%r request_id=%r utterance_id=%r source_id=%r "
        "source_room=%r target_room=%r brain_received_at_ms=%s apparent_transit_ms=%r",
        text,
        request_id,
        (timing_meta or {}).get("utterance_id"),
        request_ctx.source_id,
        request_ctx.source_room,
        request_ctx.effective_target_room,
        brain_received_at_ms,
        (timing_meta or {}).get("apparent_transit_ms"),
    )

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_CMD_EXECUTOR, _run_command_sync, text, request_ctx)
    except Exception as e:
        log.exception("Command executor error")
        error_payload = {"ok": False, "error": str(e)}
        if arbitration_execute and _WAKEWORD_ARBITRATOR is not None:
            _WAKEWORD_ARBITRATOR.finish_command(
                interaction_id,
                error_payload,
                status=500,
            )
        return web.json_response(error_payload, status=500)

    payload = _interaction_result_to_payload(
        result,
        text=text,
        request_id=request_id,
        response_mode=response_mode,
        request_ctx=request_ctx,
        stt_meta=stt_meta,
        timing_meta=timing_meta,
    )
    if arbitration_execute:
        payload["disposition"] = "winner"
        payload["arbitration"] = {
            "interaction_id": interaction_id,
            "disposition": "winner",
            "winner_source_id": request_ctx.source_id,
        }
        if _WAKEWORD_ARBITRATOR is not None:
            _WAKEWORD_ARBITRATOR.finish_command(interaction_id, payload, status=200)

    # Broadcast command_ack to WS clients in the target room
    ack_room = request_ctx.effective_target_room or DEFAULT_ROOM
    asyncio.ensure_future(_broadcast_command_ack(ack_room, text, result))

    return web.json_response(payload)


async def _internal_audio_call(method_name: str, *args, **kwargs) -> web.Response:
    method = getattr(_RUNTIME_MODULE, method_name, None)
    if not callable(method):
        return web.json_response(
            {"ok": False, "error": "Audio setup is unavailable in the running Home Suite service."},
            status=503,
        )
    try:
        loop = asyncio.get_running_loop()
        payload = await loop.run_in_executor(
            _AUDIO_EXECUTOR,
            lambda: method(*args, **kwargs),
        )
        return web.json_response(payload if isinstance(payload, dict) else {"ok": True})
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    except RuntimeError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=409)
    except Exception:
        log.exception("INTERNAL_AUDIO_CALL_FAIL method=%s", method_name)
        return web.json_response(
            {"ok": False, "error": "The audio operation failed. Check homesuite.service logs."},
            status=500,
        )


async def handle_internal_audio_status(request: web.Request) -> web.Response:
    if not _internal_auth_ok(request):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    return await _internal_audio_call("audio_calibration_status")


async def handle_internal_audio_acquire(request: web.Request) -> web.Response:
    if not _internal_auth_ok(request):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
    try:
        lease_seconds = float(data.get("lease_seconds") or 45.0)
    except (TypeError, ValueError):
        return web.json_response(
            {"ok": False, "error": "lease_seconds must be a number"},
            status=400,
        )
    return await _internal_audio_call(
        "acquire_audio_calibration_lease",
        str(data.get("owner") or "management_console"),
        lease_seconds,
    )


async def handle_internal_audio_capture(request: web.Request) -> web.Response:
    if not _internal_auth_ok(request):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
    try:
        seconds = float(data.get("seconds") or 0.0)
    except (TypeError, ValueError):
        return web.json_response(
            {"ok": False, "error": "seconds must be a number"},
            status=400,
        )
    return await _internal_audio_call(
        "capture_audio_calibration_segment",
        str(data.get("token") or ""),
        phase=str(data.get("phase") or ""),
        seconds=seconds,
        profile=data.get("profile") if isinstance(data.get("profile"), dict) else None,
        noise_metrics=data.get("noise_metrics") if isinstance(data.get("noise_metrics"), dict) else None,
    )


async def handle_internal_audio_release(request: web.Request) -> web.Response:
    if not _internal_auth_ok(request):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
    return await _internal_audio_call(
        "release_audio_calibration_lease",
        str(data.get("token") or ""),
        reason=str(data.get("reason") or "complete")[:80],
    )


async def handle_internal_audio_test_output(request: web.Request) -> web.Response:
    if not _internal_auth_ok(request):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
    return await _internal_audio_call(
        "test_audio_output_device",
        str(data.get("device") or ""),
    )


# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------


async def handle_satellite_ws(request: web.Request) -> web.StreamResponse:
    """Maintain one authenticated, device-addressed wake coordination channel."""
    if not _auth_ok(request):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)

    ws = web.WebSocketResponse(heartbeat=20.0)
    await ws.prepare(request)
    source_id = ""
    remote = request.remote or "?"

    try:
        first = await ws.receive(timeout=5.0)
        if first.type != WSMsgType.TEXT:
            await ws.close(code=1008, message=b"hello required")
            return ws
        try:
            hello = json.loads(first.data)
        except (TypeError, json.JSONDecodeError):
            await ws.close(code=1008, message=b"invalid hello")
            return ws
        if not isinstance(hello, dict) or hello.get("type") != "satellite_hello":
            await ws.close(code=1008, message=b"hello required")
            return ws
        if int(hello.get("protocol_version") or 0) != PROTOCOL_VERSION:
            await ws.send_json(
                {
                    "type": "protocol_error",
                    "supported_version": PROTOCOL_VERSION,
                    "error": "unsupported_protocol_version",
                }
            )
            await ws.close(code=1002, message=b"protocol mismatch")
            return ws

        source_id = str(hello.get("source_id") or "").strip()
        source_room = str(hello.get("source_room") or "").strip()
        if not source_id or len(source_id) > 120:
            await ws.close(code=1008, message=b"invalid source_id")
            return ws

        previous = connected_satellites.get(source_id)
        if previous is not None and previous is not ws and not previous.closed:
            await previous.close(code=1001, message=b"replaced by new connection")
        connected_satellites[source_id] = ws
        satellite_metadata[source_id] = {
            "source_room": source_room,
            "wakeword_capable": bool(hello.get("wakeword_capable", True)),
            "capabilities": dict(hello.get("capabilities") or {})
            if isinstance(hello.get("capabilities"), dict)
            else {},
        }
        arbitrator = _WAKEWORD_ARBITRATOR
        if arbitrator is None:
            raise RuntimeError("wakeword arbitrator is unavailable")
        arbitrator.register_source(source_id, **satellite_metadata[source_id])
        log.info(
            "SATELLITE_COORDINATION_CONNECTED remote=%s source_id=%r room=%r",
            remote,
            source_id,
            source_room,
        )
        await ws.send_json(
            {
                **arbitrator.cluster_state(),
                "type": "satellite_hello_ack",
                "protocol_version": PROTOCOL_VERSION,
                "source_id": source_id,
            }
        )
        await _broadcast_satellite_cluster_state()

        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                payload = json.loads(msg.data)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            message_type = str(payload.get("type") or "").strip()
            if message_type == "wakeword_candidate":
                payload.setdefault("source_room", source_room)
                try:
                    await arbitrator.submit_candidate(source_id, payload)
                except ValueError as exc:
                    await ws.send_json(
                        {
                            "type": "candidate_error",
                            "candidate_id": str(payload.get("candidate_id") or ""),
                            "error": str(exc),
                        }
                    )
            elif message_type == "ping":
                await ws.send_json(
                    {
                        "type": "pong",
                        "client_sent_at_ms": payload.get("client_sent_at_ms"),
                        "brain_sent_at_ms": unix_time_ms(),
                    }
                )
    except asyncio.TimeoutError:
        log.warning("SATELLITE_COORDINATION_HELLO_TIMEOUT remote=%s", remote)
    except Exception:
        log.exception(
            "SATELLITE_COORDINATION_ERROR remote=%s source_id=%r",
            remote,
            source_id,
        )
    finally:
        if source_id and connected_satellites.get(source_id) is ws:
            connected_satellites.pop(source_id, None)
            satellite_metadata.pop(source_id, None)
            if _WAKEWORD_ARBITRATOR is not None:
                _WAKEWORD_ARBITRATOR.unregister_source(source_id)
            await _broadcast_satellite_cluster_state()
        log.info(
            "SATELLITE_COORDINATION_DISCONNECTED remote=%s source_id=%r",
            remote,
            source_id,
        )
    return ws


async def handle_ws(request: web.Request) -> web.StreamResponse:
    # Native browser WebSocket clients cannot set arbitrary headers, so they
    # may use ?api_key=... as a compatibility fallback. Prefer X-API-Key or an
    # Authorization Bearer header in clients that support headers.
    if not _auth_ok(request, allow_query=True):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    room_id: Optional[str] = None
    r = request.rel_url.query.get("room", "").strip()
    if r and r in ROOMS:
        room_id = r

    effective_room = room_id or DEFAULT_ROOM
    remote = request.remote or "?"
    log.info("WS client connected: %s room=%s", remote, effective_room)

    connected_clients[ws] = room_id

    try:
        # Send initial snapshot
        await ws.send_str(json.dumps(ha_room_state.build_room_state(effective_room, entity_cache)))

        async for msg in ws:
            pass  # client messages are ignored; connection kept open for pushes

    except Exception as e:
        log.warning("WS client error: %s", e)
    finally:
        connected_clients.pop(ws, None)
        log.info("WS client disconnected: %s", remote)

    return ws


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_get("/healthz", handle_health)
    app.router.add_get("/manifest", handle_manifest)
    app.router.add_get("/state/{room_id}", handle_state)
    app.router.add_post("/command", handle_command)
    app.router.add_get("/internal/audio/status", handle_internal_audio_status)
    app.router.add_post("/internal/audio/acquire", handle_internal_audio_acquire)
    app.router.add_post("/internal/audio/capture", handle_internal_audio_capture)
    app.router.add_post("/internal/audio/release", handle_internal_audio_release)
    app.router.add_post("/internal/audio/test-output", handle_internal_audio_test_output)
    app.router.add_get("/satellite/ws", handle_satellite_ws)
    app.router.add_get("/ws", handle_ws)
    return app


# ---------------------------------------------------------------------------
# Background-thread runner
# ---------------------------------------------------------------------------


def _thread_main() -> None:
    """Entry point for the unified_server daemon thread.

    Creates a new asyncio event loop, starts the aiohttp server bound to
    _PORT, kicks off the HA WS subscription as a long-running task, and
    runs the loop forever until shutdown() stops it.
    """
    global _SERVER_LOOP, _SERVER_RUNNER, _WAKEWORD_ARBITRATOR

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _SERVER_LOOP = loop
    _WAKEWORD_ARBITRATOR = _build_wakeword_arbitrator()

    async def _bootstrap():
        global _SERVER_RUNNER
        app = _make_app()
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=_PORT, reuse_address=True)
        await site.start()
        _SERVER_RUNNER = runner
        log.info("UNIFIED_SERVER_LISTENING port=%d", _PORT)
        log.info("Entity labels: %d — rooms: %d", len(ha_room_state._TV_ENTITIES), len(ROOMS))
        # Launch HA WS subscription as a long-running background task
        loop.create_task(ha_subscription_loop())

    try:
        loop.run_until_complete(_bootstrap())
        _SERVER_STARTED_EVT.set()
        loop.run_forever()
    except Exception:
        log.exception("UNIFIED_SERVER_THREAD_CRASH")
        _SERVER_STARTED_EVT.set()  # unblock waiters even on failure
    finally:
        try:
            loop.close()
        except Exception:
            pass
        log.info("UNIFIED_SERVER_THREAD_EXIT")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def start_in_background_thread(
    *,
    port: int,
    api_key: str,
    ha_url: str,
    ha_token: str,
    runtime_module: Any,
    wait_for_listen: bool = True,
    wait_timeout: float = 5.0,
) -> None:
    """Start the unified server in a daemon thread. Idempotent.

    Must be called once from main.main() after command_dispatch has been
    wired (ha_get_states / ha_get_state set on the command_dispatch module).

    Args:
      port: TCP port to bind (typically 8765).
      api_key: Shared key required by every non-health HTTP/WS route.
      ha_url: Home Assistant base URL (http(s)://host:port).
      ha_token: HA long-lived access token.
      runtime_module: The main runtime module reference (used by handle_text_interaction).
      wait_for_listen: If True, block until the server is accepting connections.
      wait_timeout: Seconds to wait for listen.
    """
    global _SERVER_THREAD, _API_KEY, _HA_URL, _HA_TOKEN, _HA_WS_URL, _PORT
    global _RUNTIME_MODULE, _CMD_EXECUTOR, _AUDIO_EXECUTOR

    configured_api_key = (api_key or "").strip()
    if not configured_api_key:
        raise ValueError(
            "HOMESUITE_HTTP_API_KEY is required when the unified server is enabled"
        )

    if _SERVER_THREAD is not None and _SERVER_THREAD.is_alive():
        log.warning("UNIFIED_SERVER_ALREADY_RUNNING skipping start")
        return

    _API_KEY = configured_api_key
    _HA_URL = (ha_url or "").rstrip("/")
    _HA_TOKEN = (ha_token or "").strip()
    _HA_WS_URL = _HA_URL.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
    _PORT = int(port)
    _RUNTIME_MODULE = runtime_module

    if _CMD_EXECUTOR is None:
        _CMD_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="homesuite_cmd"
        )
    if _AUDIO_EXECUTOR is None:
        _AUDIO_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="homesuite_audio_setup"
        )

    # Install the cache-backed swaps NOW (before any traffic). Safe because
    # the swap functions fall back to REST when cache is cold or WS down.
    _install_cache_swaps()

    _SERVER_STARTED_EVT.clear()
    t = threading.Thread(target=_thread_main, daemon=True, name="unified_server")
    t.start()
    _SERVER_THREAD = t
    log.info("UNIFIED_SERVER_THREAD_STARTED")

    if wait_for_listen:
        if not _SERVER_STARTED_EVT.wait(timeout=wait_timeout):
            log.warning("UNIFIED_SERVER_START_TIMEOUT after %.1fs", wait_timeout)


def shutdown(timeout: float = 5.0) -> None:
    """Stop the unified server cleanly. Safe to call from any thread.

    Performs runner.cleanup() on the asyncio loop, then stops the loop,
    then waits for the thread to exit. Designed to be called from
    main runtime's cleanup_handler on SIGTERM/SIGINT.
    """
    global _SERVER_LOOP, _SERVER_RUNNER, _SERVER_THREAD, _AUDIO_EXECUTOR

    loop = _SERVER_LOOP
    runner = _SERVER_RUNNER
    thread = _SERVER_THREAD

    if loop is None or runner is None or thread is None:
        return

    async def _stop():
        # Cancel any pending tasks on the loop (e.g. ha_subscription_loop)
        # so they don't emit "Task was destroyed but it is pending" warnings.
        try:
            current = asyncio.current_task()
            pending = [t for t in asyncio.all_tasks(loop) if t is not current and not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                # Give cancelled tasks a chance to unwind before runner cleanup.
                await asyncio.gather(*pending, return_exceptions=True)
        except Exception:
            log.exception("UNIFIED_SERVER_TASK_CANCEL_FAIL")
        try:
            await runner.cleanup()
        except Exception:
            log.exception("UNIFIED_SERVER_RUNNER_CLEANUP_FAIL")

    try:
        fut = asyncio.run_coroutine_threadsafe(_stop(), loop)
        fut.result(timeout=timeout)
    except Exception:
        log.exception("UNIFIED_SERVER_SHUTDOWN_COROUTINE_FAIL")

    try:
        loop.call_soon_threadsafe(loop.stop)
    except Exception:
        pass

    thread.join(timeout=timeout)

    if _CMD_EXECUTOR is not None:
        try:
            _CMD_EXECUTOR.shutdown(wait=False)
        except Exception:
            pass
    if _AUDIO_EXECUTOR is not None:
        try:
            _AUDIO_EXECUTOR.shutdown(wait=False)
        except Exception:
            pass
        _AUDIO_EXECUTOR = None

    _SERVER_LOOP = None
    _SERVER_RUNNER = None
    _SERVER_THREAD = None
    log.info("UNIFIED_SERVER_STOPPED")
