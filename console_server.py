#!/usr/bin/env python3
"""Serve the authenticated Home Suite configuration and test console."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import importlib
import logging
import os
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from audio_config_editor import AudioConfigEditor, normalize_audio_profile
from config_editor import ConfigEditError, ConfigEditor
from console_audio_runtime import ConsoleAudioRuntime, ConsoleAudioRuntimeError
from console_bootstrap import ConsoleBootstrap, ConsoleBootstrapError
from console_integrations import ConsoleIntegrationError, ConsoleIntegrationManager
from console_runtime import ConsoleCommandRuntime, ConsoleRuntimeError
from console_service_manager import (
    CONSOLE_SERVICE,
    RUNTIME_SERVICE,
    ConsoleServiceError,
    ConsoleServiceManager,
)
from console_setup import ConsoleSetupError, ConsoleSetupManager
from console_snapshot import build_doctor_report, build_snapshot
from console_support import ConsoleSupportError, build_console_support_bundle
from console_wakewords import (
    MAX_WAKEWORD_MODEL_BYTES,
    ConsoleWakewordError,
    ConsoleWakewordManager,
)
from room_config_editor import RoomConfigEditor


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "console_static"
DOCS_DIR = ROOT / "docs"
SESSION_COOKIE = "homesuite_console_session"
SESSION_SECONDS = 12 * 60 * 60
log = logging.getLogger("console_server")
_CONFIG_REFRESH_LOCK = threading.RLock()


def _startup_console_key(
    configured_key: str,
    api_key: str,
    *,
    bootstrap_pending: bool,
) -> str:
    value = str(configured_key or "").strip()
    if value:
        return value
    if bootstrap_pending:
        # Login is disabled until the one-time claim replaces this process-local key.
        return secrets.token_urlsafe(32)
    legacy_value = str(api_key or "").strip()
    if legacy_value:
        return legacy_value
    raise ValueError(
        "Set HOMESUITE_CONSOLE_KEY or HOMESUITE_HTTP_API_KEY in private_config.py before starting the console."
    )


def _refresh_config_modules() -> None:
    """Reload the console's configuration view after a validated file write."""

    with _CONFIG_REFRESH_LOCK:
        importlib.invalidate_caches()
        for name in ("private_config", "deployment_config", "local_prefs"):
            module = sys.modules.get(name)
            if module is not None:
                importlib.reload(module)
        app_module = sys.modules.get("app_config")
        if app_module is not None:
            importlib.reload(app_module)


def _load_settings() -> tuple[str, int, str, str, str]:
    import app_config
    import private_config

    host = str(getattr(app_config, "CONSOLE_HOST", "0.0.0.0") or "0.0.0.0").strip()
    port = int(getattr(app_config, "CONSOLE_PORT", 8766) or 8766)
    console_key = str(
        os.environ.get("HOMESUITE_CONSOLE_KEY")
        or getattr(private_config, "HOMESUITE_CONSOLE_KEY", "")
        or ""
    ).strip()
    api_key = str(
        os.environ.get("HOMESUITE_HTTP_API_KEY")
        or getattr(private_config, "HOMESUITE_HTTP_API_KEY", "")
        or getattr(private_config, "PIPHONE_HTTP_API_KEY", "")
        or ""
    ).strip()
    console_key = _startup_console_key(
        console_key,
        api_key,
        bootstrap_pending=(not console_key and ConsoleBootstrap(root=ROOT).pending()),
    )
    api_port = int(getattr(app_config, "UNIFIED_SERVER_PORT", 8765) or 8765)
    return host, port, console_key, api_key, f"http://127.0.0.1:{api_port}/command"


class SessionStore:
    """Small in-memory browser-session store; service restart signs users out."""

    def __init__(self, *, lifetime_seconds: int = SESSION_SECONDS) -> None:
        self.lifetime_seconds = int(lifetime_seconds)
        self._sessions: dict[str, float] = {}

    def create(self) -> str:
        self.prune()
        token = secrets.token_urlsafe(32)
        self._sessions[token] = time.time() + self.lifetime_seconds
        return token

    def valid(self, token: Optional[str]) -> bool:
        value = str(token or "")
        expires = self._sessions.get(value)
        if not expires:
            return False
        if expires <= time.time():
            self._sessions.pop(value, None)
            return False
        return True

    def remove(self, token: Optional[str]) -> None:
        if token:
            self._sessions.pop(str(token), None)

    def prune(self) -> None:
        now = time.time()
        for token, expires in list(self._sessions.items()):
            if expires <= now:
                self._sessions.pop(token, None)


CONSOLE_KEY = web.AppKey("console_key", dict)
SESSIONS_KEY = web.AppKey("sessions", SessionStore)
RUNTIME_KEY = web.AppKey("runtime", ConsoleCommandRuntime)
EDITOR_KEY = web.AppKey("editor", dict)
ROOM_EDITOR_KEY = web.AppKey("room_editor", dict)
AUDIO_EDITOR_KEY = web.AppKey("audio_editor", dict)
AUDIO_RUNTIME_KEY = web.AppKey("audio_runtime", ConsoleAudioRuntime)
SERVICE_MANAGER_KEY = web.AppKey("service_manager", ConsoleServiceManager)
SERVICE_HEALTH_KEY = web.AppKey("service_health", dict)
INTEGRATION_MANAGER_KEY = web.AppKey("integration_manager", dict)
WAKEWORD_MANAGER_KEY = web.AppKey("wakeword_manager", dict)
SUPPORT_BUNDLE_KEY = web.AppKey("support_bundle_builder", dict)
BOOTSTRAP_KEY = web.AppKey("console_bootstrap", ConsoleBootstrap)
BOOTSTRAP_LOCK_KEY = web.AppKey("console_bootstrap_lock", asyncio.Lock)
SETUP_MANAGER_KEY = web.AppKey("console_setup", ConsoleSetupManager)


def _same_origin(request: web.Request) -> bool:
    origin = str(request.headers.get("Origin", "") or "").strip()
    if not origin:
        return True
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    return hmac.compare_digest(parsed.netloc.lower(), request.host.lower())


@web.middleware
async def _security_headers(request: web.Request, handler):
    response = await handler(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store" if request.path.startswith("/api/") else "no-cache"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    )
    return response


def create_app(
    *,
    console_key: str,
    api_key: str,
    live_api_url: str,
    runtime: Optional[ConsoleCommandRuntime] = None,
    editor: Optional[ConfigEditor] = None,
    room_editor: Optional[RoomConfigEditor] = None,
    audio_editor: Optional[AudioConfigEditor] = None,
    audio_runtime: Optional[ConsoleAudioRuntime] = None,
    service_manager: Optional[ConsoleServiceManager] = None,
    runtime_health_probe: Optional[Callable[[], Awaitable[bool]]] = None,
    integration_manager: Optional[ConsoleIntegrationManager] = None,
    wakeword_manager: Optional[ConsoleWakewordManager] = None,
    support_bundle_builder: Optional[Callable[..., object]] = None,
    bootstrap_manager: Optional[ConsoleBootstrap] = None,
    setup_manager: Optional[ConsoleSetupManager] = None,
    config_refresher: Optional[Callable[[], None]] = None,
) -> web.Application:
    if not str(console_key or "").strip():
        raise ValueError("console_key must not be blank")
    app = web.Application(
        middlewares=[_security_headers],
        client_max_size=MAX_WAKEWORD_MODEL_BYTES + (2 * 1024 * 1024),
    )
    app[CONSOLE_KEY] = {"value": str(console_key).strip()}
    app[SESSIONS_KEY] = SessionStore()
    app[RUNTIME_KEY] = runtime or ConsoleCommandRuntime(api_key=api_key, live_api_url=live_api_url)
    app[EDITOR_KEY] = {"value": editor, "injected": editor is not None}
    app[ROOM_EDITOR_KEY] = {"value": room_editor, "injected": room_editor is not None}
    app[AUDIO_EDITOR_KEY] = {"value": audio_editor, "injected": audio_editor is not None}
    app[AUDIO_RUNTIME_KEY] = audio_runtime or ConsoleAudioRuntime(
        api_key=api_key,
        live_api_url=live_api_url,
    )
    app[SERVICE_MANAGER_KEY] = service_manager or ConsoleServiceManager(root=ROOT)
    app[INTEGRATION_MANAGER_KEY] = {
        "value": integration_manager,
        "injected": integration_manager is not None,
    }
    app[WAKEWORD_MANAGER_KEY] = {
        "value": wakeword_manager,
        "injected": wakeword_manager is not None,
    }
    app[SUPPORT_BUNDLE_KEY] = {
        "value": support_bundle_builder or build_console_support_bundle,
    }
    app[BOOTSTRAP_KEY] = bootstrap_manager or ConsoleBootstrap(root=ROOT)
    app[BOOTSTRAP_LOCK_KEY] = asyncio.Lock()
    app[SETUP_MANAGER_KEY] = setup_manager or ConsoleSetupManager(root=ROOT)
    refresh_modules = config_refresher or _refresh_config_modules

    async def default_runtime_health_probe() -> bool:
        base_url = str(live_api_url or "").strip().rstrip("/").rsplit("/", 1)[0]
        if not base_url:
            return False
        timeout = aiohttp.ClientTimeout(total=1.5)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(base_url + "/health") as response:
                    return response.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    app[SERVICE_HEALTH_KEY] = {
        "probe": runtime_health_probe or default_runtime_health_probe,
    }

    def config_editor() -> ConfigEditor:
        holder = app[EDITOR_KEY]
        current = holder["value"]
        if current is None:
            current = ConfigEditor(root=ROOT)
            holder["value"] = current
        return current

    def rooms_editor() -> RoomConfigEditor:
        holder = app[ROOM_EDITOR_KEY]
        current = holder["value"]
        if current is None:
            current = RoomConfigEditor(root=ROOT)
            holder["value"] = current
        return current

    def audio_config_editor() -> AudioConfigEditor:
        holder = app[AUDIO_EDITOR_KEY]
        current = holder["value"]
        if current is None:
            current = AudioConfigEditor(root=ROOT)
            holder["value"] = current
        return current

    def integrations_manager() -> ConsoleIntegrationManager:
        holder = app[INTEGRATION_MANAGER_KEY]
        current = holder["value"]
        if current is None:
            current = ConsoleIntegrationManager(editor=config_editor())
            holder["value"] = current
        return current

    def wakewords_manager() -> ConsoleWakewordManager:
        holder = app[WAKEWORD_MANAGER_KEY]
        current = holder["value"]
        if current is None:
            current = ConsoleWakewordManager(root=ROOT, editor=config_editor())
            holder["value"] = current
        return current

    def refresh_config_context() -> None:
        refresh_modules()
        for key in (
            EDITOR_KEY,
            ROOM_EDITOR_KEY,
            AUDIO_EDITOR_KEY,
            INTEGRATION_MANAGER_KEY,
            WAKEWORD_MANAGER_KEY,
        ):
            holder = app[key]
            if not holder["injected"]:
                holder["value"] = None

    def restart_reasons(payload: dict, fallback: str) -> list[str]:
        reasons: list[str] = []
        for change in payload.get("changes") or []:
            if not isinstance(change, dict):
                continue
            label = change.get("label") or change.get("room_id") or change.get("key")
            if label:
                reasons.append(str(label))
        return reasons or [fallback]

    async def track_required_restarts(payload: dict, fallback: str) -> None:
        if not payload.get("applied"):
            return
        services = payload.get("restart_services") or []
        if not services:
            return
        try:
            await asyncio.to_thread(
                app[SERVICE_MANAGER_KEY].mark_required,
                services,
                restart_reasons(payload, fallback),
            )
        except Exception:
            log.exception("CONSOLE_RESTART_TRACK_FAIL services=%r", services)

    async def service_status_payload() -> dict:
        manager = app[SERVICE_MANAGER_KEY]
        payload = await asyncio.to_thread(manager.public_status)
        try:
            runtime_healthy = bool(await app[SERVICE_HEALTH_KEY]["probe"]())
        except Exception:
            runtime_healthy = False
        health = {
            RUNTIME_SERVICE: runtime_healthy,
            CONSOLE_SERVICE: True,
        }
        reconciled = False
        for row in payload.get("services") or []:
            service = row.get("service")
            row["healthy"] = bool(health.get(service))
            if row.get("restart_required"):
                reconciled = bool(
                    await asyncio.to_thread(
                        manager.reconcile,
                        service,
                        healthy=bool(row["healthy"]),
                    )
                ) or reconciled
        if reconciled:
            payload = await asyncio.to_thread(manager.public_status)
            for row in payload.get("services") or []:
                row["healthy"] = bool(health.get(row.get("service")))
        return payload

    def authenticated(request: web.Request) -> bool:
        return app[SESSIONS_KEY].valid(request.cookies.get(SESSION_COOKIE))

    def bootstrap_pending() -> bool:
        try:
            return bool(app[BOOTSTRAP_KEY].pending())
        except Exception:
            log.exception("CONSOLE_BOOTSTRAP_STATUS_FAIL")
            return False

    def add_authenticated_session(request: web.Request, response: web.StreamResponse) -> web.StreamResponse:
        token = app[SESSIONS_KEY].create()
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_SECONDS,
            httponly=True,
            samesite="Strict",
            secure=(
                request.secure
                or str(request.headers.get("X-Forwarded-Proto", ""))
                .split(",", 1)[0]
                .strip()
                .lower()
                == "https"
            ),
            path="/",
        )
        return response

    def authenticated_response(request: web.Request) -> web.Response:
        response = web.json_response({"ok": True, "authenticated": True})
        return add_authenticated_session(request, response)

    async def require_auth(request: web.Request) -> Optional[web.Response]:
        if not authenticated(request):
            return web.json_response({"ok": False, "error": "authentication_required"}, status=401)
        if request.method not in {"GET", "HEAD"} and not _same_origin(request):
            return web.json_response({"ok": False, "error": "origin_mismatch"}, status=403)
        return None

    async def index(_request: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC_DIR / "index.html")

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "service": "homesuite_console"})

    async def documentation(request: web.Request) -> web.StreamResponse:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        name = str(request.match_info.get("name") or "")
        path = (DOCS_DIR / name).resolve()
        if (
            not name
            or Path(name).name != name
            or path.suffix.lower() != ".md"
            or path.parent != DOCS_DIR.resolve()
            or not path.is_file()
        ):
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def favicon(_request: web.Request) -> web.Response:
        return web.Response(status=204)

    async def session_status(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "authenticated": authenticated(request),
                "bootstrap_required": bootstrap_pending(),
            }
        )

    async def bootstrap_claim(request: web.Request) -> web.Response:
        if not _same_origin(request):
            return web.json_response({"ok": False, "error": "origin_mismatch"}, status=403)
        if not bootstrap_pending():
            return web.json_response(
                {"ok": False, "error": "This console has already been claimed."},
                status=409,
            )
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        try:
            async with app[BOOTSTRAP_LOCK_KEY]:
                payload = await asyncio.to_thread(
                    app[BOOTSTRAP_KEY].claim,
                    data.get("passphrase"),
                    data.get("confirmation"),
                )
                app[CONSOLE_KEY]["value"] = str(payload.pop("console_key"))
                await asyncio.to_thread(refresh_config_context)
        except ConsoleBootstrapError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_BOOTSTRAP_CLAIM_FAIL")
            return web.json_response(
                {"ok": False, "error": "The console passphrase could not be saved."},
                status=500,
            )
        response = authenticated_response(request)
        response.headers["X-HomeSuite-Bootstrap"] = "claimed"
        return response

    async def login(request: web.Request) -> web.Response:
        if not _same_origin(request):
            return web.json_response({"ok": False, "error": "origin_mismatch"}, status=403)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if bootstrap_pending():
            return web.json_response(
                {"ok": False, "error": "Create the console passphrase before signing in."},
                status=409,
            )
        supplied = str(data.get("key", "") if isinstance(data, dict) else "").strip()
        expected = app[CONSOLE_KEY]["value"]
        if not supplied or not hmac.compare_digest(supplied, expected):
            await asyncio.sleep(0.25)
            return web.json_response({"ok": False, "error": "invalid_key"}, status=403)
        return authenticated_response(request)

    async def browser_login(request: web.Request) -> web.StreamResponse:
        """Handle a native form login so browser password managers see it."""
        if not _same_origin(request):
            return web.Response(status=403)
        if bootstrap_pending():
            return web.Response(status=303, headers={"Location": "/?login=bootstrap_required"})
        try:
            data = await request.post()
        except Exception:
            return web.Response(status=303, headers={"Location": "/?login=invalid"})
        supplied = str(data.get("password", "")).strip()
        expected = app[CONSOLE_KEY]["value"]
        if not supplied or not hmac.compare_digest(supplied, expected):
            await asyncio.sleep(0.25)
            return web.Response(status=303, headers={"Location": "/?login=invalid"})
        response = web.Response(status=303, headers={"Location": "/"})
        return add_authenticated_session(request, response)

    async def logout(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        app[SESSIONS_KEY].remove(request.cookies.get(SESSION_COOKIE))
        response = web.json_response({"ok": True})
        response.del_cookie(SESSION_COOKIE, path="/")
        return response

    async def snapshot(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            payload = build_snapshot()
        except Exception as exc:
            log.exception("CONSOLE_SNAPSHOT_FAIL")
            return web.json_response({"ok": False, "error": str(exc)}, status=500)
        return web.json_response({"ok": True, **payload})

    async def diagnostics(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        live = str(request.rel_url.query.get("live", "0")).lower() in {"1", "true", "yes"}
        loop = asyncio.get_running_loop()
        try:
            report = await loop.run_in_executor(None, lambda: build_doctor_report(live=live))
        except Exception as exc:
            log.exception("CONSOLE_DOCTOR_FAIL")
            return web.json_response({"ok": False, "error": str(exc)}, status=500)
        return web.json_response({"ok": True, "report": report})

    async def integrations_state(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            payload = await asyncio.to_thread(integrations_manager().public_state)
        except ConsoleIntegrationError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_INTEGRATIONS_STATE_FAIL")
            return web.json_response(
                {"ok": False, "error": "Integration setup is unavailable. Check console logs."},
                status=500,
            )
        return web.json_response({"ok": True, **payload})

    async def integration_edit_state(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        try:
            payload = await asyncio.to_thread(
                integrations_manager().edit_state,
                data.get("integration_id"),
            )
        except ConsoleIntegrationError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_INTEGRATION_EDIT_STATE_FAIL")
            return web.json_response(
                {"ok": False, "error": "Integration settings are unavailable. Check console logs."},
                status=500,
            )
        return web.json_response({"ok": True, **payload})

    async def integration_test(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        try:
            payload = await asyncio.to_thread(
                integrations_manager().test_connection,
                data.get("integration_id"),
            )
        except ConsoleIntegrationError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_INTEGRATION_TEST_FAIL")
            return web.json_response(
                {"ok": False, "error": "The connection test could not run. Check console logs."},
                status=500,
            )
        return web.json_response({"ok": True, **payload})

    async def support_bundle(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        live = str(request.rel_url.query.get("live", "0")).lower() in {"1", "true", "yes"}
        try:
            bundle = await asyncio.to_thread(
                app[SUPPORT_BUNDLE_KEY]["value"],
                live=live,
            )
        except ConsoleSupportError as exc:
            log.error("CONSOLE_SUPPORT_BUNDLE_REJECTED reason=%s", exc)
            return web.json_response(
                {"ok": False, "error": "The support bundle failed its privacy validation."},
                status=500,
            )
        except Exception:
            log.exception("CONSOLE_SUPPORT_BUNDLE_FAIL")
            return web.json_response(
                {"ok": False, "error": "The support bundle could not be generated. Check console logs."},
                status=500,
            )
        return web.Response(
            body=bundle.content,
            content_type="application/gzip",
            headers={
                "Content-Disposition": f'attachment; filename="{bundle.filename}"',
            },
        )

    async def service_status(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            payload = await service_status_payload()
        except Exception:
            log.exception("CONSOLE_SERVICE_STATUS_FAIL")
            return web.json_response(
                {"ok": False, "error": "Service status is unavailable. Check console logs."},
                status=500,
            )
        return web.json_response({"ok": True, **payload})

    async def setup_status(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            runtime_healthy = bool(await app[SERVICE_HEALTH_KEY]["probe"]())
            if runtime_healthy:
                await asyncio.to_thread(
                    app[SETUP_MANAGER_KEY].record_running_installation,
                )
            payload = await asyncio.to_thread(
                app[SETUP_MANAGER_KEY].public_status,
                runtime_healthy=runtime_healthy,
            )
        except Exception:
            log.exception("CONSOLE_SETUP_STATUS_FAIL")
            return web.json_response(
                {"ok": False, "error": "Setup status is unavailable. Check console logs."},
                status=500,
            )
        return web.json_response({"ok": True, **payload})

    async def setup_activate(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            report = await asyncio.to_thread(build_doctor_report, live=True)
            if not report.get("ok"):
                return web.json_response(
                    {
                        "ok": False,
                        "error": "Finish the required setup checks before activating Home Suite.",
                        "report": report,
                    },
                    status=409,
                )
            payload = await asyncio.to_thread(app[SETUP_MANAGER_KEY].request_activation)
        except ConsoleSetupError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_SETUP_ACTIVATE_FAIL")
            return web.json_response(
                {"ok": False, "error": "Home Suite activation could not be requested."},
                status=500,
            )
        return web.json_response({"ok": True, "report": report, **payload})

    async def service_restart(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        service = str(data.get("service") or "").strip()
        try:
            try:
                audio_status = await app[AUDIO_RUNTIME_KEY].status()
            except ConsoleAudioRuntimeError:
                audio_status = {}
            if audio_status.get("active") or audio_status.get("busy_reason"):
                raise ConsoleServiceError(
                    "Wait for calibration, capture, or assistant audio to finish before restarting.",
                    status=409,
                )
            payload = await asyncio.to_thread(
                app[SERVICE_MANAGER_KEY].request_restart,
                service,
                delay_seconds=0.35 if service == CONSOLE_SERVICE else 0.0,
            )
        except ConsoleServiceError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_SERVICE_RESTART_FAIL service=%r", service)
            return web.json_response(
                {"ok": False, "error": "The service restart could not be requested."},
                status=500,
            )
        return web.json_response({"ok": True, **payload})

    async def editable_config(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            payload = await asyncio.to_thread(config_editor().public_state)
        except ConfigEditError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_CONFIG_STATE_FAIL")
            return web.json_response(
                {"ok": False, "error": "Editable configuration is unavailable. Check console logs."},
                status=500,
            )
        return web.json_response({"ok": True, **payload})

    async def editable_config_with_secrets(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            payload = await asyncio.to_thread(
                config_editor().public_state,
                include_secrets=True,
            )
        except ConfigEditError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_CONFIG_EDIT_STATE_FAIL")
            return web.json_response(
                {"ok": False, "error": "Editable configuration is unavailable. Check console logs."},
                status=500,
            )
        return web.json_response({"ok": True, **payload})

    async def config_preview(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        try:
            payload = await asyncio.to_thread(config_editor().preview, data.get("changes"))
        except ConfigEditError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_CONFIG_PREVIEW_FAIL")
            return web.json_response(
                {"ok": False, "error": "Configuration preview failed. Check console logs."},
                status=500,
            )
        return web.json_response({"ok": True, **payload})

    async def config_apply(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        try:
            payload = await asyncio.to_thread(
                config_editor().apply,
                data.get("changes"),
                data.get("revisions"),
            )
        except ConfigEditError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_CONFIG_APPLY_FAIL")
            return web.json_response(
                {"ok": False, "error": "Configuration update failed. Check console logs."},
                status=500,
            )
        if payload.get("applied"):
            await asyncio.to_thread(refresh_config_context)
        await track_required_restarts(payload, "Configuration")
        return web.json_response({"ok": True, **payload})

    async def wakeword_state(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            payload = await asyncio.to_thread(wakewords_manager().public_state)
        except ConsoleWakewordError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_WAKEWORD_STATE_FAIL")
            return web.json_response(
                {"ok": False, "error": "Wake-word models are unavailable. Check console logs."},
                status=500,
            )
        return web.json_response({"ok": True, **payload})

    async def wakeword_preview(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        try:
            payload = await asyncio.to_thread(
                wakewords_manager().preview,
                active_ids=data.get("active_ids"),
                enabled=data.get("enabled"),
            )
        except ConsoleWakewordError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_WAKEWORD_PREVIEW_FAIL")
            return web.json_response(
                {"ok": False, "error": "Wake-word changes could not be reviewed. Check console logs."},
                status=500,
            )
        return web.json_response({"ok": True, **payload})

    async def wakeword_apply(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        try:
            payload = await asyncio.to_thread(
                wakewords_manager().apply,
                active_ids=data.get("active_ids"),
                enabled=data.get("enabled"),
                revisions=data.get("revisions"),
            )
        except ConsoleWakewordError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_WAKEWORD_APPLY_FAIL")
            return web.json_response(
                {"ok": False, "error": "Wake-word settings could not be saved. Check console logs."},
                status=500,
            )
        if payload.get("applied"):
            await asyncio.to_thread(refresh_config_context)
        await track_required_restarts(payload, "Wake-word selection")
        return web.json_response({"ok": True, **payload})

    async def wakeword_upload(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        if not request.content_type.startswith("multipart/"):
            return web.json_response(
                {"ok": False, "error": "Upload one .onnx model using multipart form data."},
                status=400,
            )
        manager = wakewords_manager()
        temporary: Optional[Path] = None
        try:
            reader = await request.multipart()
            field = await reader.next()
            if field is None or field.name != "model" or not field.filename:
                raise ConsoleWakewordError("Choose an OpenWakeWord .onnx model file.")
            if Path(field.filename).suffix.lower() != ".onnx":
                raise ConsoleWakewordError("Choose an OpenWakeWord .onnx model file.")
            temporary = await asyncio.to_thread(manager.create_upload_path)
            size = 0
            with temporary.open("wb") as handle:
                while True:
                    chunk = await field.read_chunk(size=64 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_WAKEWORD_MODEL_BYTES:
                        raise ConsoleWakewordError("The model exceeds the 24 MB upload limit.", status=413)
                    handle.write(chunk)
            payload = await asyncio.to_thread(
                manager.install_uploaded_file,
                temporary,
                field.filename,
            )
            temporary = None
        except ConsoleWakewordError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except (OSError, ValueError):
            log.exception("CONSOLE_WAKEWORD_UPLOAD_FAIL")
            return web.json_response(
                {"ok": False, "error": "The wake-word model could not be uploaded."},
                status=500,
            )
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return web.json_response({"ok": True, **payload})

    async def wakeword_remove(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        try:
            payload = await asyncio.to_thread(
                wakewords_manager().remove,
                str(data.get("model_id") or ""),
            )
        except ConsoleWakewordError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_WAKEWORD_REMOVE_FAIL")
            return web.json_response(
                {"ok": False, "error": "The wake-word model could not be removed."},
                status=500,
            )
        return web.json_response({"ok": True, **payload})

    async def rooms_state(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            payload = await asyncio.to_thread(rooms_editor().public_state)
        except ConfigEditError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_ROOMS_STATE_FAIL")
            return web.json_response(
                {"ok": False, "error": "Room configuration is unavailable. Check console logs."},
                status=500,
            )
        return web.json_response({"ok": True, **payload})

    async def rooms_catalog(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            data = {}
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        try:
            payload = await asyncio.to_thread(
                rooms_editor().catalog,
                force=bool(data.get("force")),
            )
        except Exception:
            log.exception("CONSOLE_ROOMS_CATALOG_FAIL")
            return web.json_response(
                {"ok": False, "error": "Home Assistant choices are unavailable."},
                status=500,
            )
        return web.json_response({"ok": True, **payload})

    async def rooms_preview(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        try:
            payload = await asyncio.to_thread(rooms_editor().preview, data.get("rooms"))
        except ConfigEditError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_ROOMS_PREVIEW_FAIL")
            return web.json_response(
                {"ok": False, "error": "Room review failed. Check console logs."},
                status=500,
            )
        return web.json_response({"ok": True, **payload})

    async def rooms_apply(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        try:
            payload = await asyncio.to_thread(
                rooms_editor().apply,
                data.get("rooms"),
                data.get("revision"),
            )
        except ConfigEditError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_ROOMS_APPLY_FAIL")
            return web.json_response(
                {"ok": False, "error": "Room configuration update failed. Check console logs."},
                status=500,
            )
        if payload.get("applied"):
            await asyncio.to_thread(refresh_config_context)
        await track_required_restarts(payload, "Room configuration")
        return web.json_response({"ok": True, **payload})

    async def audio_state(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            payload = await asyncio.to_thread(audio_config_editor().public_state)
        except ConfigEditError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_AUDIO_STATE_FAIL")
            return web.json_response(
                {"ok": False, "error": "Audio configuration is unavailable. Check console logs."},
                status=500,
            )
        try:
            runtime_status = await app[AUDIO_RUNTIME_KEY].status()
        except ConsoleAudioRuntimeError as exc:
            runtime_status = {"available": False, "active": False, "error": str(exc)}
        return web.json_response({"ok": True, **payload, "runtime": runtime_status})

    async def audio_preview(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        try:
            payload = await asyncio.to_thread(
                audio_config_editor().preview,
                data.get("profile"),
                data.get("output_override"),
            )
        except ConfigEditError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_AUDIO_PREVIEW_FAIL")
            return web.json_response(
                {"ok": False, "error": "Audio review failed. Check console logs."}, status=500
            )
        return web.json_response({"ok": True, **payload})

    async def audio_apply(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        try:
            payload = await asyncio.to_thread(
                audio_config_editor().apply,
                data.get("profile"),
                data.get("output_override"),
                data.get("revision"),
            )
        except ConfigEditError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception:
            log.exception("CONSOLE_AUDIO_APPLY_FAIL")
            return web.json_response(
                {"ok": False, "error": "Audio configuration update failed. Check console logs."},
                status=500,
            )
        if payload.get("applied"):
            await asyncio.to_thread(refresh_config_context)
        await track_required_restarts(payload, "Audio configuration")
        return web.json_response({"ok": True, **payload})

    async def audio_calibration_acquire(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            payload = await app[AUDIO_RUNTIME_KEY].acquire()
        except ConsoleAudioRuntimeError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        return web.json_response(payload)

    async def audio_calibration_capture(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        try:
            profile = normalize_audio_profile(data.get("profile"))
            phase = str(data.get("phase") or "").strip().lower()
            if phase not in {"noise", "speech"}:
                raise ConfigEditError("Calibration phase must be noise or speech.")
            seconds = 3.0 if phase == "noise" else 5.0
            payload = await app[AUDIO_RUNTIME_KEY].capture(
                token=str(data.get("token") or ""),
                phase=phase,
                seconds=seconds,
                profile=profile,
                noise_metrics=data.get("noise_metrics") if isinstance(data.get("noise_metrics"), dict) else None,
            )
        except ConfigEditError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except ConsoleAudioRuntimeError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        return web.json_response(payload)

    async def audio_calibration_release(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            data = {}
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        try:
            payload = await app[AUDIO_RUNTIME_KEY].release(
                token=str(data.get("token") or ""),
                reason=str(data.get("reason") or "complete")[:80],
            )
        except ConsoleAudioRuntimeError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        return web.json_response(payload)

    async def audio_test_output(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        device = str(data.get("device") or "").strip()
        if not device or len(device) > 200:
            return web.json_response({"ok": False, "error": "Choose a local playback device."}, status=400)
        try:
            payload = await app[AUDIO_RUNTIME_KEY].test_output(device=device)
        except ConsoleAudioRuntimeError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        return web.json_response(payload)

    async def command(request: web.Request) -> web.Response:
        blocked = await require_auth(request)
        if blocked is not None:
            return blocked
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "json_object_required"}, status=400)
        if "mode" in data:
            return web.json_response(
                {
                    "ok": False,
                    "error": "Refresh this page before chatting. Chat now uses the live Home Suite runtime.",
                },
                status=409,
            )
        try:
            payload = await app[RUNTIME_KEY].execute(
                text=data.get("text", ""),
                session_id=data.get("session_id"),
                room=data.get("room"),
            )
        except ConsoleRuntimeError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        return web.json_response(payload)

    async def cleanup(_app: web.Application) -> None:
        _app[RUNTIME_KEY].close()

    app.router.add_get("/", index)
    app.router.add_get("/favicon.ico", favicon)
    app.router.add_get("/health", health)
    app.router.add_get("/docs/{name}", documentation)
    app.router.add_get("/api/session", session_status)
    app.router.add_post("/api/bootstrap", bootstrap_claim)
    app.router.add_post("/login", browser_login)
    app.router.add_post("/api/login", login)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/api/snapshot", snapshot)
    app.router.add_get("/api/diagnostics", diagnostics)
    app.router.add_get("/api/integrations", integrations_state)
    app.router.add_post("/api/integrations/edit-state", integration_edit_state)
    app.router.add_post("/api/integrations/test", integration_test)
    app.router.add_get("/api/support-bundle", support_bundle)
    app.router.add_get("/api/services", service_status)
    app.router.add_post("/api/services/restart", service_restart)
    app.router.add_get("/api/setup", setup_status)
    app.router.add_post("/api/setup/activate", setup_activate)
    app.router.add_get("/api/config", editable_config)
    app.router.add_post("/api/config/edit-state", editable_config_with_secrets)
    app.router.add_post("/api/config/preview", config_preview)
    app.router.add_post("/api/config/apply", config_apply)
    app.router.add_get("/api/wakewords", wakeword_state)
    app.router.add_post("/api/wakewords/preview", wakeword_preview)
    app.router.add_post("/api/wakewords/apply", wakeword_apply)
    app.router.add_post("/api/wakewords/upload", wakeword_upload)
    app.router.add_post("/api/wakewords/remove", wakeword_remove)
    app.router.add_get("/api/rooms", rooms_state)
    app.router.add_post("/api/rooms/catalog", rooms_catalog)
    app.router.add_post("/api/rooms/preview", rooms_preview)
    app.router.add_post("/api/rooms/apply", rooms_apply)
    app.router.add_get("/api/audio", audio_state)
    app.router.add_post("/api/audio/preview", audio_preview)
    app.router.add_post("/api/audio/apply", audio_apply)
    app.router.add_post("/api/audio/calibration/acquire", audio_calibration_acquire)
    app.router.add_post("/api/audio/calibration/capture", audio_calibration_capture)
    app.router.add_post("/api/audio/calibration/release", audio_calibration_release)
    app.router.add_post("/api/audio/test-output", audio_test_output)
    app.router.add_post("/api/command", command)
    app.router.add_static("/static/", STATIC_DIR, show_index=False)
    app.on_cleanup.append(cleanup)
    return app


def main(argv: Optional[list[str]] = None) -> int:
    settings_host, settings_port, console_key, api_key, live_api_url = _load_settings()
    parser = argparse.ArgumentParser(description="Run the Home Suite management console.")
    parser.add_argument("--host", default=settings_host, help=f"listen address (default: {settings_host})")
    parser.add_argument("--port", type=int, default=settings_port, help=f"listen port (default: {settings_port})")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = create_app(console_key=console_key, api_key=api_key, live_api_url=live_api_url)
    log.info("CONSOLE_LISTENING host=%s port=%d", args.host, args.port)
    web.run_app(app, host=args.host, port=args.port, access_log=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
