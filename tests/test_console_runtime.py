from __future__ import annotations

import asyncio
import unittest

from aiohttp import web
from aiohttp.test_utils import TestServer

from console_runtime import ConsoleCommandRuntime, ConsoleRuntimeError


class ConsoleRuntimeTests(unittest.TestCase):
    def test_invalid_mode_is_rejected(self):
        runtime = ConsoleCommandRuntime(api_key="key", live_api_url="http://127.0.0.1/command")
        try:
            with self.assertRaisesRegex(ConsoleRuntimeError, "test or live"):
                asyncio.run(runtime.execute(text="hello", mode="unsafe", session_id="session123", room=None))
        finally:
            runtime.close()

    def test_live_mode_forwards_context_and_authentication(self):
        async def scenario():
            seen = {}

            async def command(request):
                seen["key"] = request.headers.get("X-API-Key")
                seen["payload"] = await request.json()
                return web.json_response(
                    {
                        "ok": True,
                        "handled": True,
                        "action_occurred": True,
                        "response": "Turned it on.",
                        "source": "device_confirm",
                    }
                )

            app = web.Application()
            app.router.add_post("/command", command)
            server = TestServer(app)
            await server.start_server()
            runtime = ConsoleCommandRuntime(api_key="shared-key", live_api_url=str(server.make_url("/command")))
            try:
                result = await runtime.execute(
                    text="turn it on",
                    mode="live",
                    session_id="session123",
                    room=None,
                )
            finally:
                runtime.close()
                await server.close()

            self.assertEqual(seen["key"], "shared-key")
            self.assertEqual(seen["payload"]["source_id"], "console_session123")
            self.assertEqual(seen["payload"]["origin"], "console_live")
            self.assertEqual(result["mode"], "live")
            self.assertFalse(result["simulated"])
            self.assertTrue(result["action_occurred"])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
