from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from aiohttp import CookieJar, FormData
from aiohttp.test_utils import TestClient, TestServer

import console_server
from console_wakewords import ConsoleWakewordManager


class ConsoleWakewordRouteTests(unittest.TestCase):
    def test_authenticated_state_selection_upload_and_remove_routes(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                config = SimpleNamespace(
                    WAKEWORD_ENABLED=False,
                    WAKEWORD_ENGINE="openwakeword",
                    WAKEWORD_MODEL="",
                    WAKEWORD_MODEL_PATHS=[],
                    WAKEWORD_THRESHOLD=0.5,
                )
                editor = mock.Mock()
                editor.app_config = config
                editor.preview.return_value = {
                    "changes": [{"key": "WAKEWORD_MODEL_PATHS", "label": "Custom model paths"}],
                    "change_count": 1,
                    "revisions": {"local_prefs.py": "abc"},
                    "restart_services": ["homesuite.service"],
                }
                editor.apply.return_value = {
                    "applied": True,
                    "changes": [{"key": "WAKEWORD_MODEL_PATHS", "label": "Custom model paths"}],
                    "change_count": 1,
                    "written_files": ["local_prefs.py"],
                    "restart_services": [],
                    "backup_dir": str(root / "backup"),
                }
                manager = ConsoleWakewordManager(
                    root=root,
                    editor=editor,
                    app_config=config,
                    model_probe=lambda path: {"validated": True, "label": path.stem},
                    extra_model_dirs=[],
                )
                app = console_server.create_app(
                    console_key="console-passphrase",
                    api_key="api-key",
                    live_api_url="http://127.0.0.1:8765/command",
                    wakeword_manager=manager,
                    config_refresher=lambda: None,
                )
                client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
                await client.start_server()
                try:
                    self.assertEqual((await client.get("/api/wakewords")).status, 401)
                    await client.post("/api/login", json={"key": "console-passphrase"})

                    initial = await (await client.get("/api/wakewords")).json()
                    self.assertTrue(initial["ok"])
                    self.assertEqual(initial["models"], [])

                    form = FormData()
                    form.add_field(
                        "model",
                        b"fake onnx model",
                        filename="Hal.onnx",
                        content_type="application/octet-stream",
                    )
                    upload_response = await client.post("/api/wakewords/upload", data=form)
                    self.assertEqual(upload_response.status, 200)
                    uploaded = await upload_response.json()
                    self.assertTrue(uploaded["added"])
                    model_id = uploaded["model"]["id"]

                    preview_response = await client.post(
                        "/api/wakewords/preview",
                        json={"active_ids": [model_id], "enabled": True},
                    )
                    self.assertEqual(preview_response.status, 200)
                    preview = await preview_response.json()
                    self.assertEqual(preview["selection"]["active_count"], 1)

                    apply_response = await client.post(
                        "/api/wakewords/apply",
                        json={
                            "active_ids": [model_id],
                            "enabled": True,
                            "revisions": preview["revisions"],
                        },
                    )
                    self.assertEqual(apply_response.status, 200)
                    editor.apply.assert_called_once()

                    remove_response = await client.post(
                        "/api/wakewords/remove",
                        json={"model_id": model_id},
                    )
                    self.assertEqual(remove_response.status, 200)
                    self.assertTrue((await remove_response.json())["removed"])
                finally:
                    await client.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
