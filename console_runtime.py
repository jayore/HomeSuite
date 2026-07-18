"""Forward browser chat messages to the live Home Suite runtime.

The management console deliberately uses the production service's authenticated
HTTP command endpoint. This keeps dialogue state, command ordering, and side
effects inside the process that owns them. Safe capture remains available from
the command-line test harness rather than as a second browser-chat mode.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any, Optional

import aiohttp


_SESSION_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")


class ConsoleRuntimeError(RuntimeError):
    """A user-facing console execution error with an HTTP status."""

    def __init__(self, message: str, *, status: int = 500) -> None:
        super().__init__(message)
        self.status = status


class ConsoleCommandRuntime:
    """Proxy authenticated browser chat to the production command service."""

    def __init__(self, *, api_key: str, live_api_url: str) -> None:
        self.api_key = str(api_key or "").strip()
        self.live_api_url = str(live_api_url or "").strip()

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
        }
        if room:
            payload["source_room"] = room
            payload["effective_target_room"] = room
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
        session_id: Optional[str],
        room: Optional[str],
    ) -> dict:
        command = str(text or "").strip()
        if not command:
            raise ConsoleRuntimeError("Enter a message first.", status=400)
        if len(command) > 4000:
            raise ConsoleRuntimeError("Message is too long.", status=400)
        safe_session = self._session_id(session_id)
        safe_room = self._room(room)
        if room and not safe_room:
            raise ConsoleRuntimeError("Choose a configured room.", status=400)
        return await self._live(command, safe_session, safe_room)

    def close(self) -> None:
        """Retain a lifecycle hook for the aiohttp application."""
