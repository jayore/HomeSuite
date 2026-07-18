from __future__ import annotations

import asyncio
import concurrent.futures
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

import unified_server
from wakeword_arbitration import WakewordArbitrator


class _Request:
    def __init__(self, *, headers=None, query=None, remote="127.0.0.1"):
        self.headers = headers or {}
        self.rel_url = SimpleNamespace(query=query or {})
        self.remote = remote


class UnifiedServerAuthTests(unittest.TestCase):
    def setUp(self):
        self.original_key = unified_server._API_KEY
        self.original_runtime = unified_server._RUNTIME_MODULE
        self.original_arbitrator = unified_server._WAKEWORD_ARBITRATOR
        self.original_executor = unified_server._CMD_EXECUTOR
        unified_server._API_KEY = "shared-passphrase"
        unified_server.connected_satellites.clear()
        unified_server.satellite_metadata.clear()
        unified_server._WAKEWORD_ARBITRATOR = unified_server._build_wakeword_arbitrator()

    def tearDown(self):
        unified_server._API_KEY = self.original_key
        unified_server._RUNTIME_MODULE = self.original_runtime
        unified_server._WAKEWORD_ARBITRATOR = self.original_arbitrator
        unified_server._CMD_EXECUTOR = self.original_executor
        unified_server.connected_satellites.clear()
        unified_server.satellite_metadata.clear()

    def test_shared_key_header_is_accepted(self):
        request = _Request(headers={"X-API-Key": "shared-passphrase"})
        self.assertTrue(unified_server._auth_ok(request))

    def test_bearer_header_is_accepted(self):
        request = _Request(headers={"Authorization": "Bearer shared-passphrase"})
        self.assertTrue(unified_server._auth_ok(request))

    def test_query_key_is_websocket_only_fallback(self):
        request = _Request(query={"api_key": "shared-passphrase"})
        self.assertFalse(unified_server._auth_ok(request))
        self.assertTrue(unified_server._auth_ok(request, allow_query=True))

    def test_missing_or_wrong_key_is_rejected(self):
        self.assertFalse(unified_server._auth_ok(_Request()))
        self.assertFalse(
            unified_server._auth_ok(_Request(headers={"X-API-Key": "wrong"}))
        )

    def test_internal_routes_require_authenticated_loopback(self):
        self.assertTrue(
            unified_server._internal_auth_ok(
                _Request(headers={"X-API-Key": "shared-passphrase"}, remote="127.0.0.1")
            )
        )
        self.assertFalse(
            unified_server._internal_auth_ok(
                _Request(headers={"X-API-Key": "shared-passphrase"}, remote="192.168.1.20")
            )
        )

    def test_internal_audio_status_and_lease_routes(self):
        async def scenario():
            calls = []

            def status():
                return {"ok": True, "available": True, "active": False}

            def acquire(owner, lease_seconds):
                calls.append((owner, lease_seconds))
                return {"ok": True, "token": "lease-token", "active": True}

            def release(token, reason="complete"):
                calls.append((token, reason))
                return {"ok": True, "released": True, "active": False}

            unified_server._RUNTIME_MODULE = SimpleNamespace(
                audio_calibration_status=status,
                acquire_audio_calibration_lease=acquire,
                release_audio_calibration_lease=release,
            )
            client = TestClient(TestServer(unified_server._make_app()))
            await client.start_server()
            try:
                response = await client.get("/internal/audio/status")
                self.assertEqual(response.status, 403)
                headers = {"X-API-Key": "shared-passphrase"}
                response = await client.get("/internal/audio/status", headers=headers)
                self.assertEqual(response.status, 200)
                self.assertTrue((await response.json())["available"])
                response = await client.post("/internal/audio/acquire", headers=headers, json={})
                self.assertEqual((await response.json())["token"], "lease-token")
                response = await client.post(
                    "/internal/audio/acquire",
                    headers=headers,
                    json={"lease_seconds": "later"},
                )
                self.assertEqual(response.status, 400)
                response = await client.post(
                    "/internal/audio/capture",
                    headers=headers,
                    json={"seconds": "briefly"},
                )
                self.assertEqual(response.status, 400)
                response = await client.post(
                    "/internal/audio/release",
                    headers=headers,
                    json={"token": "lease-token", "reason": "complete"},
                )
                self.assertEqual(response.status, 200)
                self.assertEqual(calls[0][0], "management_console")
                self.assertEqual(calls[1], ("lease-token", "complete"))
            finally:
                await client.close()

        asyncio.run(scenario())

    def test_websocket_rejects_before_upgrade(self):
        response = asyncio.run(unified_server.handle_ws(_Request()))
        self.assertEqual(response.status, 403)

    def test_websocket_accepts_shared_header_and_completes_upgrade(self):
        async def scenario():
            client = TestClient(TestServer(unified_server._make_app()))
            await client.start_server()
            try:
                ws = await client.ws_connect(
                    "/ws",
                    headers={"X-API-Key": "shared-passphrase"},
                )
                initial = await ws.receive(timeout=2)
                self.assertEqual(initial.type, WSMsgType.TEXT)
                await ws.close()
            finally:
                await client.close()

        asyncio.run(scenario())

    def test_satellite_websocket_registers_and_grants_single_device_without_hold(self):
        async def scenario():
            client = TestClient(TestServer(unified_server._make_app()))
            await client.start_server()
            try:
                ws = await client.ws_connect(
                    "/satellite/ws",
                    headers={"X-API-Key": "shared-passphrase"},
                )
                await ws.send_json(
                    {
                        "type": "satellite_hello",
                        "protocol_version": 1,
                        "source_id": "piphone1",
                        "source_room": "living_room",
                        "wakeword_capable": True,
                    }
                )
                hello = await ws.receive_json(timeout=2)
                self.assertEqual(hello["type"], "satellite_hello_ack")
                self.assertEqual(hello["eligible_wakeword_nodes"], 1)
                await ws.receive_json(timeout=2)  # topology broadcast

                await ws.send_json(
                    {
                        "type": "wakeword_candidate",
                        "protocol_version": 1,
                        "candidate_id": "candidate-a",
                        "wakeword_label": "hal_v2",
                        "wakeword_score": 0.90,
                        "wakeword_threshold": 0.75,
                        "audio_quality": {
                            "separation_db": 18.0,
                            "p90_dbfs": -14.0,
                            "clip_pct": 0.0,
                        },
                        "timing": {
                            "wakeword": {
                                "label": "hal_v2",
                                "audio_end_at_ms": 1000,
                            }
                        },
                    }
                )
                decision = await ws.receive_json(timeout=2)
                self.assertEqual(decision["type"], "wakeword_decision")
                self.assertEqual(decision["disposition"], "granted")
                self.assertEqual(decision["election_hold_ms"], 0)
                self.assertTrue(decision["winner_token"])
                await ws.close()
            finally:
                await client.close()

        asyncio.run(scenario())

    def test_winner_command_lease_executes_once_across_concurrent_retries(self):
        async def scenario():
            decisions = []

            async def emit(source_id, payload):
                decisions.append((source_id, payload))

            arbitrator = WakewordArbitrator(emit, election_window_ms=5)
            arbitrator.register_source("piphone1", source_room="living_room")
            await arbitrator.submit_candidate(
                "piphone1",
                {
                    "candidate_id": "candidate-a",
                    "wakeword_label": "hal_v2",
                    "wakeword_score": 0.9,
                    "wakeword_threshold": 0.75,
                    "timing": {
                        "wakeword": {
                            "label": "hal_v2",
                            "audio_end_at_ms": 1000,
                        }
                    },
                },
            )
            decision = decisions[0][1]
            unified_server._WAKEWORD_ARBITRATOR = arbitrator
            unified_server._RUNTIME_MODULE = SimpleNamespace(
                audio_calibration_status=lambda: {"active": False}
            )
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            unified_server._CMD_EXECUTOR = executor
            calls = []

            def run_command(text, request_ctx):
                calls.append((text, request_ctx.source_id))
                time.sleep(0.05)
                return SimpleNamespace(
                    handled=True,
                    action_occurred=True,
                    response_text="Done.",
                    source="device_confirm",
                )

            client = TestClient(TestServer(unified_server._make_app()))
            await client.start_server()
            try:
                request_payload = {
                    "text": "turn off the stair light",
                    "source_id": "piphone1",
                    "source_type": "satellite",
                    "origin": "satellite_wakeword",
                    "source_room": "living_room",
                    "request_id": "utterance-a",
                    "interaction_id": decision["interaction_id"],
                    "winner_token": decision["winner_token"],
                }
                headers = {"X-API-Key": "shared-passphrase"}
                with mock.patch.object(
                    unified_server,
                    "_run_command_sync",
                    side_effect=run_command,
                ):
                    first, duplicate = await asyncio.gather(
                        client.post("/command", headers=headers, json=request_payload),
                        client.post("/command", headers=headers, json=request_payload),
                    )
                    first_payload = await first.json()
                    duplicate_payload = await duplicate.json()

                self.assertEqual(len(calls), 1)
                self.assertEqual(first_payload["response"], "Done.")
                self.assertEqual(duplicate_payload["response"], "Done.")
                self.assertEqual(first_payload["disposition"], "winner")
                self.assertEqual(duplicate_payload["disposition"], "winner")
            finally:
                await client.close()
                executor.shutdown(wait=True)

        asyncio.run(scenario())

    def test_blank_server_key_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "HOMESUITE_HTTP_API_KEY"):
            unified_server.start_in_background_thread(
                port=8765,
                api_key="",
                ha_url="http://homeassistant.local:8123",
                ha_token="token",
                runtime_module=SimpleNamespace(),
            )


if __name__ == "__main__":
    unittest.main()
