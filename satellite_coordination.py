"""Persistent brain channel for wake-word candidate arbitration.

Transcript submission remains ordinary authenticated HTTP. This channel exists
only for the latency-sensitive decision that happens between a local wake hit
and the acknowledgement cue.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from wakeword_arbitration import PROTOCOL_VERSION


@dataclass(frozen=True)
class WakewordDecision:
    disposition: str
    candidate_id: str
    interaction_id: str = ""
    winner_token: str = ""
    winner_source_id: str = ""
    reason: str = ""
    eligible_wakeword_nodes: int = 0
    election_hold_ms: int = 0

    @property
    def granted(self) -> bool:
        return self.disposition in {"granted", "legacy"}

    @property
    def suppressed(self) -> bool:
        return self.disposition == "suppressed"

    @property
    def coordinated(self) -> bool:
        return self.disposition in {"granted", "suppressed"}

    def public_metadata(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "interaction_id": self.interaction_id or None,
            "disposition": self.disposition,
            "winner_source_id": self.winner_source_id or None,
            "reason": self.reason or None,
            "eligible_wakeword_nodes": int(self.eligible_wakeword_nodes),
            "election_hold_ms": int(self.election_hold_ms),
        }


def normalize_satellite_ws_url(value: str) -> str:
    """Convert a brain HTTP or command URL to its satellite WebSocket URL."""
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        raise ValueError("Satellite brain URL must be a complete http:// or https:// URL.")
    scheme = "wss" if parsed.scheme in {"https", "wss"} else "ws"
    path = parsed.path.rstrip("/")
    if path.endswith("/command"):
        path = path[: -len("/command")]
    path = f"{path}/satellite/ws" if path else "/satellite/ws"
    return urlunsplit((scheme, parsed.netloc, path, parsed.query, ""))


class SatelliteCoordinationClient:
    """Own a reconnecting WebSocket and expose synchronous wake decisions."""

    def __init__(
        self,
        *,
        brain_url: str,
        api_key: str,
        source_id: str,
        source_room: str,
        capabilities: Optional[Mapping[str, Any]] = None,
        logger=None,
    ) -> None:
        self.ws_url = normalize_satellite_ws_url(brain_url)
        self.api_key = str(api_key or "").strip()
        self.source_id = str(source_id or "").strip()
        self.source_room = str(source_room or "").strip()
        if not self.api_key:
            raise ValueError("Satellite brain API key is empty.")
        if not self.source_id:
            raise ValueError("Satellite source ID is empty.")
        if not self.source_room:
            raise ValueError("Satellite source room is empty.")
        self.capabilities = dict(capabilities or {})
        self.log = logger or logging.getLogger("satellite_coordination")

        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._outgoing: queue.Queue = queue.Queue()
        self._pending: dict[str, queue.Queue] = {}
        self._lock = threading.RLock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._outgoing_event: Optional[asyncio.Event] = None
        self._eligible_nodes = 0
        self._ever_multi_node = False
        self._last_error = ""

    def start(self, *, wait_for_ready_seconds: float = 0.0) -> bool:
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._thread_main,
                daemon=True,
                name="satellite_coordination",
            )
            self._thread.start()
        if wait_for_ready_seconds > 0:
            self._ready_event.wait(timeout=max(0.0, float(wait_for_ready_seconds)))
        return self._ready_event.is_set()

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop_event.set()
        self._ready_event.clear()
        self._outgoing.put(None)
        self._wake_sender()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connected": self._ready_event.is_set(),
                "eligible_wakeword_nodes": self._eligible_nodes,
                "ever_multi_node": self._ever_multi_node,
                "last_error": self._last_error or None,
                "brain_ws_url": self.ws_url,
            }

    def _fallback_decision(self, candidate_id: str, reason: str) -> WakewordDecision:
        with self._lock:
            multi_node = self._ever_multi_node or self._eligible_nodes > 1
            eligible = self._eligible_nodes
        return WakewordDecision(
            disposition="unavailable" if multi_node else "legacy",
            candidate_id=candidate_id,
            reason=reason,
            eligible_wakeword_nodes=eligible,
        )

    def request_wakeword_decision(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float = 0.75,
    ) -> WakewordDecision:
        candidate = dict(payload)
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        if not candidate_id:
            raise ValueError("Wake-word candidate ID is required.")
        if not self._ready_event.is_set():
            return self._fallback_decision(candidate_id, "coordination_not_connected")

        response_queue: queue.Queue = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[candidate_id] = response_queue
        candidate["type"] = "wakeword_candidate"
        candidate["protocol_version"] = PROTOCOL_VERSION
        self._outgoing.put(candidate)
        self._wake_sender()
        started = time.monotonic()
        try:
            decision = response_queue.get(timeout=max(0.05, float(timeout_seconds)))
        except queue.Empty:
            with self._lock:
                self._pending.pop(candidate_id, None)
            return self._fallback_decision(candidate_id, "decision_timeout")
        self.log.info(
            "WAKEWORD_ARBITRATION_ROUNDTRIP candidate_id=%r disposition=%r dt_ms=%.1f nodes=%s hold_ms=%s",
            candidate_id,
            decision.disposition,
            (time.monotonic() - started) * 1000.0,
            decision.eligible_wakeword_nodes,
            decision.election_hold_ms,
        )
        return decision

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run_forever())
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            self.log.exception("SATELLITE_COORDINATION_THREAD_FAIL")
        finally:
            self._ready_event.clear()
            self._fail_pending("coordination_stopped")

    async def _run_forever(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._outgoing_event = asyncio.Event()
        retry_seconds = 0.5
        try:
            while not self._stop_event.is_set():
                try:
                    await self._run_connection()
                    retry_seconds = 0.5
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    with self._lock:
                        self._last_error = str(exc)
                    self.log.warning(
                        "SATELLITE_COORDINATION_CONNECT_FAIL url=%r error=%s retry_sec=%.1f",
                        self.ws_url,
                        exc,
                        retry_seconds,
                    )
                finally:
                    self._ready_event.clear()
                    self._fail_pending("coordination_disconnected")
                    self._drain_outgoing()
                if self._stop_event.is_set():
                    break
                await asyncio.sleep(retry_seconds)
                retry_seconds = min(10.0, retry_seconds * 2.0)
        finally:
            self._loop = None
            self._outgoing_event = None

    async def _run_connection(self) -> None:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=None, connect=5.0, sock_connect=5.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(
                self.ws_url,
                headers={"X-API-Key": self.api_key},
                heartbeat=20.0,
            ) as ws:
                await ws.send_json(
                    {
                        "type": "satellite_hello",
                        "protocol_version": PROTOCOL_VERSION,
                        "source_id": self.source_id,
                        "source_room": self.source_room,
                        "wakeword_capable": True,
                        "capabilities": self.capabilities,
                    }
                )
                sender = asyncio.create_task(self._sender(ws))
                if not self._outgoing.empty() and self._outgoing_event is not None:
                    self._outgoing_event.set()
                try:
                    async for message in ws:
                        if message.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            payload = json.loads(message.data)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        if not isinstance(payload, dict):
                            continue
                        self._handle_message(payload)
                finally:
                    sender.cancel()
                    try:
                        await sender
                    except (asyncio.CancelledError, Exception):
                        pass

    async def _sender(self, ws) -> None:
        event = self._outgoing_event
        if event is None:
            return
        while not self._stop_event.is_set():
            await event.wait()
            event.clear()
            while True:
                try:
                    payload = self._outgoing.get_nowait()
                except queue.Empty:
                    break
                if payload is None:
                    return
                await ws.send_json(payload)

    def _wake_sender(self) -> None:
        loop = self._loop
        event = self._outgoing_event
        if loop is None or event is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            pass

    def _handle_message(self, payload: Mapping[str, Any]) -> None:
        message_type = str(payload.get("type") or "").strip()
        if message_type in {"satellite_hello_ack", "cluster_state"}:
            eligible = max(0, int(payload.get("eligible_wakeword_nodes") or 0))
            with self._lock:
                self._eligible_nodes = eligible
                self._ever_multi_node = self._ever_multi_node or eligible > 1
                self._last_error = ""
            if message_type == "satellite_hello_ack":
                self._ready_event.set()
                self.log.info(
                    "SATELLITE_COORDINATION_READY source_id=%r nodes=%s election_window_ms=%s",
                    self.source_id,
                    eligible,
                    payload.get("election_window_ms"),
                )
            return

        if message_type not in {"wakeword_decision", "candidate_error"}:
            return
        candidate_id = str(payload.get("candidate_id") or "").strip()
        if not candidate_id:
            return
        with self._lock:
            response_queue = self._pending.pop(candidate_id, None)
            eligible = self._eligible_nodes
        if response_queue is None:
            return
        if message_type == "candidate_error":
            decision = self._fallback_decision(
                candidate_id,
                str(payload.get("error") or "candidate_error"),
            )
        else:
            decision = WakewordDecision(
                disposition=str(payload.get("disposition") or "unavailable"),
                candidate_id=candidate_id,
                interaction_id=str(payload.get("interaction_id") or ""),
                winner_token=str(payload.get("winner_token") or ""),
                winner_source_id=str(payload.get("winner_source_id") or ""),
                reason=str(payload.get("reason") or ""),
                eligible_wakeword_nodes=int(
                    payload.get("eligible_wakeword_nodes") or eligible
                ),
                election_hold_ms=int(payload.get("election_hold_ms") or 0),
            )
        try:
            response_queue.put_nowait(decision)
        except queue.Full:
            pass

    def _fail_pending(self, reason: str) -> None:
        with self._lock:
            pending = list(self._pending.items())
            self._pending.clear()
        for candidate_id, response_queue in pending:
            try:
                response_queue.put_nowait(self._fallback_decision(candidate_id, reason))
            except queue.Full:
                pass

    def _drain_outgoing(self) -> None:
        while True:
            try:
                self._outgoing.get_nowait()
            except queue.Empty:
                return
