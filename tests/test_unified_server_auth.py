from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

import unified_server


class _Request:
    def __init__(self, *, headers=None, query=None, remote="127.0.0.1"):
        self.headers = headers or {}
        self.rel_url = SimpleNamespace(query=query or {})
        self.remote = remote


class UnifiedServerAuthTests(unittest.TestCase):
    def setUp(self):
        self.original_key = unified_server._API_KEY
        self.original_runtime = unified_server._RUNTIME_MODULE
        unified_server._API_KEY = "shared-passphrase"

    def tearDown(self):
        unified_server._API_KEY = self.original_key
        unified_server._RUNTIME_MODULE = self.original_runtime

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
