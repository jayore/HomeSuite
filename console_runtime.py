"""Execute Home Suite console messages in preview or live mode.

Preview requests use the established command-runtime capture mode in this
process. Live requests are forwarded to the production service's authenticated
HTTP command endpoint, keeping production dialogue state and command ordering
inside the process that owns them.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import aiohttp


_SESSION_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")


class ConsoleRuntimeError(RuntimeError):
    """A user-facing console execution error with an HTTP status."""

    def __init__(self, message: str, *, status: int = 500) -> None:
        super().__init__(message)
        self.status = status


class ConsoleCommandRuntime:
    """Own one sequential preview worker and proxy live commands."""

    def __init__(self, *, api_key: str, live_api_url: str) -> None:
        self.api_key = str(api_key or "").strip()
        self.live_api_url = str(live_api_url or "").strip()
        self._preview_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="homesuite_console_preview",
        )

    @staticmethod
    def _session_id(value: Optional[str]) -> str:
        candidate = str(value or "").strip()
        return candidate if _SESSION_RE.fullmatch(candidate) else uuid.uuid4().hex[:16]

    @staticmethod
    def _room(value: Optional[str]) -> Optional[str]:
        candidate = str(value or "").strip()
        if not candidate:
            return None
        try:
            from home_registry import ROOMS

            return candidate if candidate in ROOMS else None
        except Exception:
            return None

    def _preview_sync(self, text: str, session_id: str, room: Optional[str]) -> dict:
        preview_log = Path(__file__).resolve().parent / "logs" / "console-preview.log"
        preview_log.parent.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HOMESUITE_RUNTIME_LOG_PATH", str(preview_log))
        import command_runtime
        from interaction_flow import handle_text_interaction
        from request_context import build_request_context, replace_current_request_context, set_current_request_context

        started = time.monotonic()
        runtime_module = command_runtime.initialize_runtime("capture")
        context = build_request_context(
            source_id=f"console_{session_id}",
            source_type="console",
            origin="console_preview",
            source_room=room,
            effective_target_room=room,
        )
        previous = replace_current_request_context(context)
        try:
            result = handle_text_interaction(runtime_module, text)
        finally:
            set_current_request_context(previous)

        response = str(getattr(result, "response_text", "") or "").strip()
        return {
            "ok": True,
            "mode": "test",
            "simulated": True,
            "handled": bool(getattr(result, "handled", False)),
            "handler_reported_action": bool(getattr(result, "action_occurred", False)),
            "action_occurred": False,
            "response": response,
            "text": response or None,
            "source": str(getattr(result, "source", "") or "") or None,
            "context": context.to_log_dict(),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "session_id": session_id,
        }

    async def _preview(self, text: str, session_id: str, room: Optional[str]) -> dict:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._preview_executor,
            self._preview_sync,
            text,
            session_id,
            room,
        )

    async def _live(self, text: str, session_id: str, room: Optional[str]) -> dict:
        if not self.api_key or not self.live_api_url:
            raise ConsoleRuntimeError("The live command API is not configured.", status=503)
        payload = {
            "text": text,
            "request_id": uuid.uuid4().hex,
            "response_mode": "text",
            "source_id": f"console_{session_id}",
            "source_type": "console",
            "origin": "console_live",
            "source_room": room,
            "effective_target_room": room,
        }
        timeout = aiohttp.ClientTimeout(total=45)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.live_api_url,
                    json=payload,
                    headers={"X-API-Key": self.api_key},
                ) as response:
                    try:
                        body: Any = await response.json()
                    except Exception:
                        body = {"ok": False, "error": (await response.text())[:300]}
                    if response.status != 200:
                        detail = str(body.get("error") or f"HTTP {response.status}")
                        raise ConsoleRuntimeError(f"Live command failed: {detail}", status=502)
        except ConsoleRuntimeError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ConsoleRuntimeError(
                "The Home Suite runtime is unavailable. Check homesuite.service and try again.",
                status=503,
            ) from exc

        body["mode"] = "live"
        body["simulated"] = False
        body["session_id"] = session_id
        return body

    async def execute(
        self,
        *,
        text: str,
        mode: str,
        session_id: Optional[str],
        room: Optional[str],
    ) -> dict:
        command = str(text or "").strip()
        if not command:
            raise ConsoleRuntimeError("Enter a message first.", status=400)
        if len(command) > 4000:
            raise ConsoleRuntimeError("Message is too long.", status=400)
        normalized_mode = str(mode or "test").strip().lower()
        if normalized_mode not in {"test", "live"}:
            raise ConsoleRuntimeError("Mode must be test or live.", status=400)
        safe_session = self._session_id(session_id)
        safe_room = self._room(room)
        if room and not safe_room:
            raise ConsoleRuntimeError("Choose a configured room.", status=400)
        if normalized_mode == "live":
            return await self._live(command, safe_session, safe_room)
        return await self._preview(command, safe_session, safe_room)

    def close(self) -> None:
        self._preview_executor.shutdown(wait=False, cancel_futures=True)
