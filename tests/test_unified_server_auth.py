from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

import unified_server


class _Request:
    def __init__(self, *, headers=None, query=None):
        self.headers = headers or {}
        self.rel_url = SimpleNamespace(query=query or {})


class UnifiedServerAuthTests(unittest.TestCase):
    def setUp(self):
        self.original_key = unified_server._API_KEY
        unified_server._API_KEY = "shared-passphrase"

    def tearDown(self):
        unified_server._API_KEY = self.original_key

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
