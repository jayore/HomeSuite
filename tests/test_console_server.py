from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

import console_server
from console_bootstrap import ConsoleBootstrap


class ConsoleServerTests(unittest.TestCase):
    def test_pending_bootstrap_gets_process_local_startup_key(self):
        with mock.patch.object(console_server.secrets, "token_urlsafe", return_value="temporary-key"):
            self.assertEqual(
                console_server._startup_console_key(
                    "",
                    "",
                    bootstrap_pending=True,
                ),
                "temporary-key",
            )
            self.assertEqual(
                console_server._startup_console_key(
                    "",
                    "legacy-api-key",
                    bootstrap_pending=True,
                ),
                "temporary-key",
            )

    def test_startup_console_key_keeps_legacy_fallback_after_setup(self):
        self.assertEqual(
            console_server._startup_console_key(
                "",
                "legacy-api-key",
                bootstrap_pending=False,
            ),
            "legacy-api-key",
        )
        with self.assertRaisesRegex(ValueError, "HOMESUITE_CONSOLE_KEY"):
            console_server._startup_console_key("", "", bootstrap_pending=False)

    def test_blank_console_key_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "console_key"):
            console_server.create_app(console_key="", api_key="api", live_api_url="http://localhost/command")

    def test_fresh_install_can_claim_passphrase_once(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / "state").mkdir()
                (root / "state" / "console_bootstrap_pending").write_text("pending\n", encoding="utf-8")
                (root / "private_config.py").write_text(
                    'HOMESUITE_HTTP_API_KEY = "generated-api-key"\n'
                    'HOMESUITE_CONSOLE_KEY = ""\n',
                    encoding="utf-8",
                )
                app = console_server.create_app(
                    console_key="generated-api-key",
                    api_key="generated-api-key",
                    live_api_url="http://127.0.0.1:8765/command",
                    bootstrap_manager=ConsoleBootstrap(root=root),
                    config_refresher=lambda: None,
                )
                client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
                await client.start_server()
                try:
                    session = await (await client.get("/api/session")).json()
                    self.assertFalse(session["authenticated"])
                    self.assertTrue(session["bootstrap_required"])
                    self.assertEqual(
                        (await client.post("/api/login", json={"key": "generated-api-key"})).status,
                        409,
                    )
                    self.assertEqual(
                        (
                            await client.post(
                                "/api/bootstrap",
                                json={"passphrase": "short", "confirmation": "short"},
                            )
                        ).status,
                        400,
                    )

                    response = await client.post(
                        "/api/bootstrap",
                        json={
                            "passphrase": "correct horse battery",
                            "confirmation": "correct horse battery",
                        },
                    )
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["X-HomeSuite-Bootstrap"], "claimed")
                    self.assertNotIn("correct horse battery", await response.text())
                    session = await (await client.get("/api/session")).json()
                    self.assertTrue(session["authenticated"])
                    self.assertFalse(session["bootstrap_required"])
                    self.assertEqual(
                        (
                            await client.post(
                                "/api/bootstrap",
                                json={
                                    "passphrase": "another long passphrase",
                                    "confirmation": "another long passphrase",
                                },
                            )
                        ).status,
                        409,
                    )
                    await client.post("/api/logout")
                    self.assertEqual(
                        (await client.post("/api/login", json={"key": "generated-api-key"})).status,
                        403,
                    )
                    self.assertEqual(
                        (await client.post("/api/login", json={"key": "correct horse battery"})).status,
                        200,
                    )
                finally:
                    await client.close()

        asyncio.run(scenario())

    def test_login_protects_snapshot_and_command_routes(self):
        async def scenario():
            runtime = mock.Mock()
            runtime.execute = mock.AsyncMock(
                return_value={"ok": True, "mode": "test", "response": "Preview"}
            )
            app = console_server.create_app(
                console_key="console-passphrase",
                api_key="api-key",
                live_api_url="http://127.0.0.1:8765/command",
                runtime=runtime,
            )
            client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
            await client.start_server()
            try:
                response = await client.get("/api/snapshot")
                self.assertEqual(response.status, 401)
                response = await client.get("/api/config")
                self.assertEqual(response.status, 401)
                response = await client.get("/api/rooms")
                self.assertEqual(response.status, 401)
                response = await client.get("/docs/WAKEWORD.md")
                self.assertEqual(response.status, 401)
                response = await client.post("/api/config/edit-state", json={})
                self.assertEqual(response.status, 401)

                response = await client.post("/api/login", json={"key": "wrong"})
                self.assertEqual(response.status, 403)

                response = await client.post("/api/login", json={"key": "console-passphrase"})
                self.assertEqual(response.status, 200)
                self.assertIn(console_server.SESSION_COOKIE, client.session.cookie_jar.filter_cookies(client.make_url("/")))

                with mock.patch.object(console_server, "build_snapshot", return_value={"overview": {}}):
                    response = await client.get("/api/snapshot")
                    self.assertEqual(response.status, 200)
                    self.assertEqual((await response.json())["overview"], {})

                response = await client.get("/docs/WAKEWORD.md")
                self.assertEqual(response.status, 200)
                self.assertIn("wake", (await response.text()).lower())

                response = await client.post(
                    "/api/command",
                    json={"text": "turn on the light", "mode": "test", "session_id": "session123"},
                )
                self.assertEqual(response.status, 200)
                runtime.execute.assert_awaited_once()
            finally:
                await client.close()

        asyncio.run(scenario())

    def test_authenticated_config_preview_and_apply_routes(self):
        async def scenario():
            runtime = mock.Mock()
            editor = mock.Mock()
            editor.public_state.return_value = {
                "schema_version": 1,
                "sections": [],
                "fields": [],
                "revisions": {},
            }
            editor.preview.return_value = {
                "changes": [{"key": "DEFAULT_ROOM"}],
                "change_count": 1,
                "revisions": {"local_prefs.py": "abc"},
                "restart_services": ["homesuite.service"],
            }
            editor.apply.return_value = {
                "applied": True,
                "changes": [{"key": "DEFAULT_ROOM"}],
                "change_count": 1,
                "written_files": ["local_prefs.py"],
                "restart_services": ["homesuite.service"],
                "backup_dir": "/tmp/backup",
            }
            room_editor = mock.Mock()
            room_editor.public_state.return_value = {
                "schema_version": 1,
                "rooms": {"office": {"label": "Office"}},
                "default_room": "office",
                "revision": "rooms-abc",
            }
            room_editor.catalog.return_value = {
                "available": True,
                "areas": [{"id": "office", "label": "Office"}],
                "entities": [],
            }
            room_editor.preview.return_value = {
                "changes": [{"room_id": "office", "action": "update"}],
                "change_count": 1,
                "revision": "rooms-abc",
                "restart_services": ["homesuite.service"],
            }
            room_editor.apply.return_value = {
                "applied": True,
                "changes": [{"room_id": "office", "action": "update"}],
                "change_count": 1,
                "written_files": ["deployment_config.py"],
                "restart_services": ["homesuite.service"],
                "backup_dir": "/tmp/rooms-backup",
            }
            service_manager = mock.Mock()
            service_manager.mark_required.return_value = {}
            config_refresher = mock.Mock()
            app = console_server.create_app(
                console_key="console-passphrase",
                api_key="api-key",
                live_api_url="http://127.0.0.1:8765/command",
                runtime=runtime,
                editor=editor,
                room_editor=room_editor,
                service_manager=service_manager,
                config_refresher=config_refresher,
            )
            client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
            await client.start_server()
            try:
                await client.post("/api/login", json={"key": "console-passphrase"})
                response = await client.get("/api/config")
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["schema_version"], 1)
                response = await client.post("/api/config/edit-state", json={})
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["schema_version"], 1)

                changes = [{"key": "DEFAULT_ROOM", "action": "set", "value": "office"}]
                response = await client.post(
                    "/api/config/preview",
                    json={"changes": changes},
                )
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["change_count"], 1)

                response = await client.post(
                    "/api/config/apply",
                    json={"changes": changes, "revisions": {"local_prefs.py": "abc"}},
                )
                self.assertEqual(response.status, 200)
                self.assertTrue((await response.json())["applied"])
                editor.preview.assert_called_once_with(changes)
                editor.apply.assert_called_once_with(changes, {"local_prefs.py": "abc"})
                editor.public_state.assert_has_calls(
                    [mock.call(), mock.call(include_secrets=True)]
                )

                response = await client.get("/api/rooms")
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["default_room"], "office")

                response = await client.post("/api/rooms/catalog", json={"force": True})
                self.assertEqual(response.status, 200)
                self.assertTrue((await response.json())["available"])

                rooms = {"office": {"label": "Main Office"}}
                response = await client.post("/api/rooms/preview", json={"rooms": rooms})
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["change_count"], 1)

                response = await client.post(
                    "/api/rooms/apply",
                    json={"rooms": rooms, "revision": "rooms-abc"},
                )
                self.assertEqual(response.status, 200)
                self.assertTrue((await response.json())["applied"])
                room_editor.public_state.assert_called_once_with()
                room_editor.catalog.assert_called_once_with(force=True)
                room_editor.preview.assert_called_once_with(rooms)
                room_editor.apply.assert_called_once_with(rooms, "rooms-abc")
                self.assertEqual(service_manager.mark_required.call_count, 2)
                self.assertEqual(config_refresher.call_count, 2)
            finally:
                await client.close()

        asyncio.run(scenario())

    def test_cross_origin_write_is_rejected_after_login(self):
        async def scenario():
            app = console_server.create_app(
                console_key="console-passphrase",
                api_key="api-key",
                live_api_url="http://127.0.0.1:8765/command",
            )
            client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
            await client.start_server()
            try:
                await client.post("/api/login", json={"key": "console-passphrase"})
                response = await client.post(
                    "/api/logout",
                    headers={"Origin": "http://malicious.example"},
                )
                self.assertEqual(response.status, 403)
                response = await client.post(
                    "/api/config/edit-state",
                    json={},
                    headers={"Origin": "http://malicious.example"},
                )
                self.assertEqual(response.status, 403)
            finally:
                await client.close()

        asyncio.run(scenario())

    def test_authenticated_audio_editor_and_calibration_routes(self):
        async def scenario():
            profile = {
                "name": "test_mic",
                "device_match": "Test USB Mic",
                "device_index": None,
                "sample_rate": 48000,
                "channels": 1,
                "stream_latency": "high",
                "strict_device_match": True,
                "alsa_card": None,
                "mixer_control": None,
                "mixer_value": None,
                "verify_interval_sec": 0,
                "noise_suppression_level": 0,
                "auto_gain_dbfs": 0,
                "volume_multiplier": 1.0,
                "command_noise_suppression_level": 0,
                "command_auto_gain_dbfs": 0,
                "command_volume_multiplier": 1.0,
                "ptt_volume_multiplier": 1.0,
                "aec_mode": "none",
            }
            runtime = mock.Mock()
            audio_editor = mock.Mock()
            audio_editor.public_state.return_value = {
                "schema_version": 1,
                "profile": profile,
                "output_override": None,
                "output_effective": "default",
                "hardware": {"inputs": [], "outputs": []},
                "revision": "audio-abc",
            }
            audio_editor.preview.return_value = {
                "profile": profile,
                "output_override": None,
                "changes": [{"key": "AUDIO_INPUT_PROFILE"}],
                "change_count": 1,
                "revision": "audio-abc",
                "restart_services": ["homesuite.service"],
            }
            audio_editor.apply.return_value = {
                "applied": True,
                "changes": [{"key": "AUDIO_INPUT_PROFILE"}],
                "change_count": 1,
                "restart_services": ["homesuite.service"],
            }
            audio_runtime = mock.Mock()
            audio_runtime.status = mock.AsyncMock(return_value={"available": True, "active": False})
            audio_runtime.acquire = mock.AsyncMock(return_value={"ok": True, "token": "lease-token", "available": True, "active": True})
            audio_runtime.capture = mock.AsyncMock(return_value={"ok": True, "phase": "noise", "metrics": {}})
            audio_runtime.release = mock.AsyncMock(return_value={"ok": True, "released": True, "available": True, "active": False})
            audio_runtime.test_output = mock.AsyncMock(return_value={"ok": True, "device": "default"})
            service_manager = mock.Mock()
            service_manager.mark_required.return_value = {}
            config_refresher = mock.Mock()
            app = console_server.create_app(
                console_key="console-passphrase",
                api_key="api-key",
                live_api_url="http://127.0.0.1:8765/command",
                runtime=runtime,
                audio_editor=audio_editor,
                audio_runtime=audio_runtime,
                service_manager=service_manager,
                config_refresher=config_refresher,
            )
            client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
            await client.start_server()
            try:
                self.assertEqual((await client.get("/api/audio")).status, 401)
                await client.post("/api/login", json={"key": "console-passphrase"})

                response = await client.get("/api/audio")
                self.assertEqual(response.status, 200)
                self.assertTrue((await response.json())["runtime"]["available"])

                response = await client.post("/api/audio/preview", json={"profile": profile, "output_override": None})
                self.assertEqual(response.status, 200)
                response = await client.post(
                    "/api/audio/apply",
                    json={"profile": profile, "output_override": None, "revision": "audio-abc"},
                )
                self.assertEqual(response.status, 200)

                response = await client.post("/api/audio/calibration/acquire", json={})
                self.assertEqual((await response.json())["token"], "lease-token")
                response = await client.post(
                    "/api/audio/calibration/capture",
                    json={"token": "lease-token", "phase": "noise", "profile": profile},
                )
                self.assertEqual(response.status, 200)
                response = await client.post(
                    "/api/audio/calibration/release",
                    json={"token": "lease-token", "reason": "complete"},
                )
                self.assertEqual(response.status, 200)
                response = await client.post("/api/audio/test-output", json={"device": "default"})
                self.assertEqual(response.status, 200)

                audio_editor.preview.assert_called_once_with(profile, None)
                audio_editor.apply.assert_called_once_with(profile, None, "audio-abc")
                audio_runtime.capture.assert_awaited_once()
                audio_runtime.release.assert_awaited_once_with(token="lease-token", reason="complete")
                service_manager.mark_required.assert_called_once()
                config_refresher.assert_called_once_with()
            finally:
                await client.close()

        asyncio.run(scenario())

    def test_authenticated_service_status_and_fixed_restart_routes(self):
        async def scenario():
            service_manager = mock.Mock()
            service_manager.public_status.return_value = {
                "schema_version": 1,
                "restart_required": True,
                "services": [
                    {
                        "service": "homesuite.service",
                        "label": "Home Suite runtime",
                        "active_state": "active",
                        "restart_required": True,
                        "restart_reasons": ["Audio configuration"],
                    },
                    {
                        "service": "homesuite-console.service",
                        "label": "Management console",
                        "active_state": "active",
                        "restart_required": False,
                        "restart_reasons": [],
                    },
                ],
            }
            service_manager.reconcile.return_value = False
            service_manager.request_restart.return_value = {
                "service": "homesuite.service",
                "previous_pid": 123,
                "previous_invocation_id": "old",
                "restart_requested": True,
            }
            audio_runtime = mock.Mock()
            audio_runtime.status = mock.AsyncMock(return_value={"active": False, "busy_reason": None})
            health_probe = mock.AsyncMock(return_value=True)
            app = console_server.create_app(
                console_key="console-passphrase",
                api_key="api-key",
                live_api_url="http://127.0.0.1:8765/command",
                audio_runtime=audio_runtime,
                service_manager=service_manager,
                runtime_health_probe=health_probe,
            )
            client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
            await client.start_server()
            try:
                self.assertEqual((await client.get("/api/services")).status, 401)
                await client.post("/api/login", json={"key": "console-passphrase"})

                response = await client.get("/api/services")
                self.assertEqual(response.status, 200)
                payload = await response.json()
                self.assertTrue(payload["services"][0]["healthy"])

                response = await client.post(
                    "/api/services/restart",
                    json={"service": "homesuite.service"},
                )
                self.assertEqual(response.status, 200)
                self.assertTrue((await response.json())["restart_requested"])
                service_manager.request_restart.assert_called_once_with(
                    "homesuite.service",
                    delay_seconds=0.0,
                )
            finally:
                await client.close()

        asyncio.run(scenario())

    def test_authenticated_setup_status_and_activation_routes(self):
        async def scenario():
            setup_manager = mock.Mock()
            setup_manager.public_status.return_value = {
                "schema_version": 1,
                "complete": False,
                "activation_requested": False,
                "activation_supported": True,
                "runtime_healthy": False,
            }
            setup_manager.request_activation.return_value = {
                "activation_requested": True,
                "already_requested": False,
            }
            health_probe = mock.AsyncMock(return_value=False)
            app = console_server.create_app(
                console_key="console-passphrase",
                api_key="api-key",
                live_api_url="http://127.0.0.1:8765/command",
                setup_manager=setup_manager,
                runtime_health_probe=health_probe,
            )
            client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
            await client.start_server()
            try:
                self.assertEqual((await client.get("/api/setup")).status, 401)
                await client.post("/api/login", json={"key": "console-passphrase"})

                response = await client.get("/api/setup")
                self.assertEqual(response.status, 200)
                self.assertFalse((await response.json())["complete"])
                setup_manager.record_running_installation.assert_not_called()
                setup_manager.public_status.assert_called_once_with(runtime_healthy=False)

                with mock.patch.object(
                    console_server,
                    "build_doctor_report",
                    return_value={"ok": False, "checks": []},
                ):
                    response = await client.post("/api/setup/activate", json={})
                self.assertEqual(response.status, 409)
                setup_manager.request_activation.assert_not_called()

                with mock.patch.object(
                    console_server,
                    "build_doctor_report",
                    return_value={"ok": True, "checks": []},
                ):
                    response = await client.post("/api/setup/activate", json={})
                self.assertEqual(response.status, 200)
                self.assertTrue((await response.json())["activation_requested"])
                setup_manager.request_activation.assert_called_once_with()
            finally:
                await client.close()

        asyncio.run(scenario())

    def test_setup_status_persists_completion_for_running_existing_node(self):
        async def scenario():
            setup_manager = mock.Mock()
            setup_manager.public_status.return_value = {
                "schema_version": 1,
                "complete": True,
                "activation_requested": True,
                "activation_supported": True,
                "runtime_healthy": True,
            }
            health_probe = mock.AsyncMock(return_value=True)
            app = console_server.create_app(
                console_key="console-passphrase",
                api_key="api-key",
                live_api_url="http://127.0.0.1:8765/command",
                setup_manager=setup_manager,
                runtime_health_probe=health_probe,
            )
            client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
            await client.start_server()
            try:
                await client.post("/api/login", json={"key": "console-passphrase"})
                response = await client.get("/api/setup")
                self.assertEqual(response.status, 200)
                self.assertTrue((await response.json())["complete"])
                setup_manager.record_running_installation.assert_called_once_with()
                setup_manager.public_status.assert_called_once_with(runtime_healthy=True)
            finally:
                await client.close()

        asyncio.run(scenario())

    def test_authenticated_provider_scoped_integration_routes(self):
        async def scenario():
            integration_manager = mock.Mock()
            integration_manager.public_state.return_value = {
                "integrations": [{"id": "home_assistant", "status": "configured"}],
            }
            integration_manager.edit_state.return_value = {
                "integration": {"id": "home_assistant", "label": "Home Assistant"},
                "fields": [{"key": "HA_URL"}, {"key": "HA_TOKEN"}],
                "revisions": {"private_config.py": "rev"},
            }
            integration_manager.test_connection.return_value = {
                "integration_id": "home_assistant",
                "status": "success",
                "summary": "Connection successful",
                "detail": "Home Assistant accepted the configured token.",
            }
            app = console_server.create_app(
                console_key="console-passphrase",
                api_key="api-key",
                live_api_url="http://127.0.0.1:8765/command",
                integration_manager=integration_manager,
            )
            client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
            await client.start_server()
            try:
                self.assertEqual((await client.get("/api/integrations")).status, 401)
                self.assertEqual(
                    (await client.post("/api/integrations/edit-state", json={"integration_id": "home_assistant"})).status,
                    401,
                )
                await client.post("/api/login", json={"key": "console-passphrase"})

                response = await client.get("/api/integrations")
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["integrations"][0]["status"], "configured")

                response = await client.post(
                    "/api/integrations/edit-state",
                    json={"integration_id": "home_assistant"},
                )
                self.assertEqual(response.status, 200)
                self.assertEqual(len((await response.json())["fields"]), 2)

                response = await client.post(
                    "/api/integrations/test",
                    json={"integration_id": "home_assistant"},
                )
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["status"], "success")
                integration_manager.public_state.assert_called_once_with()
                integration_manager.edit_state.assert_called_once_with("home_assistant")
                integration_manager.test_connection.assert_called_once_with("home_assistant")
            finally:
                await client.close()

        asyncio.run(scenario())

    def test_authenticated_support_bundle_download(self):
        async def scenario():
            builder = mock.Mock(
                return_value=SimpleNamespace(
                    filename="homesuite-support-test.tar.gz",
                    content=b"safe-bundle",
                )
            )
            app = console_server.create_app(
                console_key="console-passphrase",
                api_key="api-key",
                live_api_url="http://127.0.0.1:8765/command",
                support_bundle_builder=builder,
            )
            client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
            await client.start_server()
            try:
                self.assertEqual((await client.get("/api/support-bundle")).status, 401)
                await client.post("/api/login", json={"key": "console-passphrase"})

                response = await client.get("/api/support-bundle?live=1")
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.read(), b"safe-bundle")
                self.assertEqual(response.content_type, "application/gzip")
                self.assertIn(
                    'filename="homesuite-support-test.tar.gz"',
                    response.headers["Content-Disposition"],
                )
                builder.assert_called_once_with(live=True)
            finally:
                await client.close()

        asyncio.run(scenario())

    def test_same_host_origin_allows_https_reverse_proxy(self):
        request = SimpleNamespace(
            headers={"Origin": "https://homesuite.example"},
            host="homesuite.example",
        )
        self.assertTrue(console_server._same_origin(request))

    def test_forwarded_https_sets_secure_session_cookie(self):
        async def scenario():
            app = console_server.create_app(
                console_key="console-passphrase",
                api_key="api-key",
                live_api_url="http://127.0.0.1:8765/command",
            )
            client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
            await client.start_server()
            try:
                response = await client.post(
                    "/api/login",
                    json={"key": "console-passphrase"},
                    headers={"X-Forwarded-Proto": "https"},
                )
                self.assertEqual(response.status, 200)
                cookies = response.headers.getall("Set-Cookie")
                self.assertTrue(any("Secure" in value for value in cookies))
            finally:
                await client.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
