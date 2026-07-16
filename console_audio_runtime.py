"""Proxy management-console audio operations to the local Home Suite runtime."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import aiohttp


class ConsoleAudioRuntimeError(RuntimeError):
    def __init__(self, message: str, *, status: int = 500) -> None:
        super().__init__(message)
        self.status = int(status)


class ConsoleAudioRuntime:
    """Call fixed loopback-only audio endpoints without exposing the API key."""

    def __init__(self, *, api_key: str, live_api_url: str) -> None:
        self.api_key = str(api_key or "").strip()
        command_url = str(live_api_url or "").strip().rstrip("/")
        self.base_url = command_url.rsplit("/", 1)[0] if "/" in command_url else ""

    async def _request(self, path: str, *, method: str = "GET", body: Optional[dict] = None) -> dict[str, Any]:
        if not self.api_key or not self.base_url:
            raise ConsoleAudioRuntimeError("The local Home Suite runtime API is not configured.", status=503)
        timeout = aiohttp.ClientTimeout(total=25)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    self.base_url + path,
                    json=body if method != "GET" else None,
                    headers={"X-API-Key": self.api_key},
                ) as response:
                    try:
                        payload: Any = await response.json()
                    except Exception:
                        payload = {"ok": False, "error": (await response.text())[:300]}
                    if response.status != 200:
                        message = str(payload.get("error") or f"HTTP {response.status}")
                        if response.status == 404:
                            message = "Restart homesuite.service to enable guided audio calibration with this console version."
                        mapped = response.status if response.status in {400, 409, 503} else 502
                        raise ConsoleAudioRuntimeError(message, status=mapped)
                    if not isinstance(payload, dict):
                        raise ConsoleAudioRuntimeError("The audio runtime returned an invalid response.", status=502)
                    return payload
        except ConsoleAudioRuntimeError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ConsoleAudioRuntimeError(
                "The running Home Suite service is unavailable. Check homesuite.service and try again.",
                status=503,
            ) from exc

    async def status(self) -> dict[str, Any]:
        return await self._request("/internal/audio/status")

    async def acquire(self) -> dict[str, Any]:
        return await self._request(
            "/internal/audio/acquire",
            method="POST",
            body={"owner": "management_console", "lease_seconds": 45},
        )

    async def capture(
        self,
        *,
        token: str,
        phase: str,
        seconds: float,
        profile: dict,
        noise_metrics: Optional[dict] = None,
    ) -> dict[str, Any]:
        return await self._request(
            "/internal/audio/capture",
            method="POST",
            body={
                "token": token,
                "phase": phase,
                "seconds": seconds,
                "profile": profile,
                "noise_metrics": noise_metrics,
            },
        )

    async def release(self, *, token: str, reason: str = "complete") -> dict[str, Any]:
        return await self._request(
            "/internal/audio/release",
            method="POST",
            body={"token": token, "reason": reason},
        )

    async def test_output(self, *, device: str) -> dict[str, Any]:
        return await self._request(
            "/internal/audio/test-output",
            method="POST",
            body={"device": device},
        )
