"""youtube_lounge.py — control the Apple TV YouTube app via the Lounge API.

Wraps `pyytlounge` (async) behind a small synchronous API so the rest of the
sync command pipeline can drive the TV. A single persistent asyncio loop runs in
a daemon thread (same pattern as unified_server) and owns one YtLoungeApi
session; sync wrappers submit coroutines to it via run_coroutine_threadsafe.

Pairing is a one-time step (Apple TV → YouTube → Settings → Link with TV code);
credentials are persisted to state/youtube_lounge.json and reloaded on startup,
so we reconnect without re-pairing (refreshing the lounge token if it expired).

Everything here is defensive — failures return False / None and never raise into
the command path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import List, Optional

log = logging.getLogger("youtube_lounge")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE_DIR = os.path.join(_BASE_DIR, "state")
_AUTH_PATH = os.path.join(_STATE_DIR, "youtube_lounge.json")

DEVICE_NAME = "PiPhone"
_CMD_TIMEOUT = 20.0
_PAIR_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Auth persistence (atomic, defensive)
# ---------------------------------------------------------------------------

def _load_auth() -> dict:
    try:
        with open(_AUTH_PATH, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("youtube_lounge: failed to load auth: %s", e)
        return {}


def _save_auth(data: dict) -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        tmp = _AUTH_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _AUTH_PATH)
    except Exception as e:
        log.warning("youtube_lounge: failed to save auth: %s", e)


def _normalize_auth(data: dict) -> dict:
    """Coerce stored auth into the format AuthState.deserialize() expects.

    pyytlounge 3.2.0's wrapper `store_auth_state()`/`load_auth_state()` are
    asymmetric — store emits snake_case keys with no `version`/`expiry`, while
    load (via AuthState.deserialize) requires `version` + camelCase + `expiry`.
    We serialize via AuthState.serialize() (correct format) going forward, and
    migrate any file written by the old buggy path here so existing pairings keep
    working without re-pairing.
    """
    if not data:
        return {}
    if "version" in data and "loungeIdToken" in data:
        return data  # already the correct serialize() format
    return {
        "version": 0,
        "screenId": data.get("screenId") or data.get("screen_id"),
        "loungeIdToken": data.get("loungeIdToken") or data.get("lounge_id_token"),
        "refreshToken": data.get("refreshToken") or data.get("refresh_token"),
        "expiry": data.get("expiry", 0),
    }


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------

class _LoungeManager:
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._api = None  # YtLoungeApi
        self._start_lock = threading.Lock()

    # --- background loop plumbing ---

    def _ensure_loop(self) -> None:
        if self._loop is not None and self._loop.is_running():
            return
        with self._start_lock:
            if self._loop is not None and self._loop.is_running():
                return
            loop = asyncio.new_event_loop()

            def _run() -> None:
                asyncio.set_event_loop(loop)
                loop.run_forever()

            t = threading.Thread(target=_run, name="yt-lounge", daemon=True)
            t.start()
            self._loop, self._thread = loop, t

    def _submit(self, coro, timeout: float):
        """Run a coroutine on the background loop and wait for the result."""
        try:
            self._ensure_loop()
            fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return fut.result(timeout)
        except Exception as e:
            log.warning("youtube_lounge: command failed: %s", e)
            return None

    # --- async internals (run on the background loop) ---

    async def _get_api(self):
        if self._api is None:
            from pyytlounge import YtLoungeApi
            api = YtLoungeApi(DEVICE_NAME, logger=log)
            await api.__aenter__()  # set up aiohttp session
            data = _load_auth()
            if data and (data.get("screenId") or data.get("screen_id")):
                try:
                    api.auth.deserialize(_normalize_auth(data))
                except Exception as e:
                    log.warning("youtube_lounge: load auth failed: %s", e)
            self._api = api
        return self._api

    async def _ensure_connected(self) -> bool:
        api = await self._get_api()
        if not api.linked():
            log.warning("youtube_lounge: not paired/linked — run tools/youtube_pair.py")
            return False
        if api.connected():
            return True
        # First attempt; on failure refresh the lounge token and retry once.
        try:
            if await api.connect() and api.connected():
                return True
        except Exception as e:
            log.info("youtube_lounge: connect failed (%s), refreshing auth", e)
        try:
            await api.refresh_auth()
            _save_auth(dict(api.auth.serialize()))
            return bool(await api.connect() and api.connected())
        except Exception as e:
            log.warning("youtube_lounge: reconnect after refresh failed: %s", e)
            return False

    async def _pair(self, code: str) -> bool:
        api = await self._get_api()
        try:
            await api.pair(code)
        except Exception as e:
            log.warning("youtube_lounge: pairing failed: %s", e)
        if api.linked():
            _save_auth(dict(api.auth.serialize()))
        return api.linked()

    async def _pair_screen_id(self, screen_id: str) -> bool:
        """Reuse a known screen id (e.g. from iSponsorBlockTV) — mints our own
        lounge token via refresh, no TV code needed."""
        api = await self._get_api()
        try:
            await api.pair_with_screen_id(screen_id)
        except Exception as e:
            log.warning("youtube_lounge: pair_with_screen_id failed: %s", e)
        if api.linked():
            _save_auth(dict(api.auth.serialize()))
        return api.linked()

    async def _command_retry(self, name: str, params: dict, *, attempts: int = 2) -> bool:
        """Send a Lounge command, retrying once on failure. A first attempt against
        a stale session (e.g. right after a Plex->YouTube app switch) returns False
        and the wrapper drops the session via _handle_session_result, so the retry's
        _ensure_connected reconnects fresh and lands — fixing the 'first ask after a
        switch reports couldn't-reach' flakiness."""
        for i in range(max(1, attempts)):
            if not await self._ensure_connected():
                await asyncio.sleep(0.8)
                continue
            try:
                if bool(await self._api._command(name, params)):
                    return True
            except Exception as e:
                log.info("youtube_lounge: %s attempt %d failed: %s", name, i + 1, e)
            await asyncio.sleep(0.8)
        return False

    async def _play_playlist(self, list_id: str, video_id: Optional[str] = None) -> bool:
        # setPlaylist with listId alone is accepted but doesn't start playback on
        # the TV; it needs the first videoId (+ currentIndex) to actually begin,
        # same shape as play_video/set_queue.
        if video_id:
            params = {"videoId": video_id, "listId": list_id, "currentIndex": 0}
        else:
            params = {"listId": list_id}
        return await self._command_retry("setPlaylist", params)

    async def _play_video(self, video_id: str) -> bool:
        # play_video is setPlaylist{videoId} under the hood; route via retry.
        return await self._command_retry("setPlaylist", {"videoId": video_id})

    async def _set_queue(self, ids: List[str]) -> bool:
        if not ids:
            return False
        # pyytlounge has no native multi-video method; setPlaylist accepts a
        # comma-separated videoIds list for a TV-native consecutive queue.
        return await self._command_retry("setPlaylist", {
            "videoId": ids[0],
            "videoIds": ",".join(ids),
            "currentIndex": 0,
        })

    async def _simple(self, name: str, *args) -> bool:
        if not await self._ensure_connected():
            return False
        method = getattr(self._api, name, None)
        if method is None:
            return False
        return bool(await method(*args))

    # --- sync public API ---

    def is_paired(self) -> bool:
        return bool(_load_auth().get("screenId"))

    def pair(self, code: str) -> bool:
        return bool(self._submit(self._pair(code), _PAIR_TIMEOUT))

    def pair_with_screen_id(self, screen_id: str) -> bool:
        return bool(self._submit(self._pair_screen_id(screen_id), _PAIR_TIMEOUT))

    def play_video(self, video_id: str) -> bool:
        return bool(self._submit(self._play_video(video_id), _CMD_TIMEOUT))

    def set_queue(self, ids: List[str]) -> bool:
        return bool(self._submit(self._set_queue(list(ids)), _CMD_TIMEOUT))

    def play_playlist(self, list_id: str, video_id: Optional[str] = None) -> bool:
        return bool(self._submit(self._play_playlist(list_id, video_id), _CMD_TIMEOUT))

    def next(self) -> bool:
        return bool(self._submit(self._simple("next"), _CMD_TIMEOUT))

    def previous(self) -> bool:
        return bool(self._submit(self._simple("previous"), _CMD_TIMEOUT))

    def pause(self) -> bool:
        return bool(self._submit(self._simple("pause"), _CMD_TIMEOUT))

    def play(self) -> bool:
        return bool(self._submit(self._simple("play"), _CMD_TIMEOUT))

    def seek_to(self, seconds: float) -> bool:
        return bool(self._submit(self._simple("seek_to", float(seconds)), _CMD_TIMEOUT))

    def set_volume(self, volume: int) -> bool:
        return bool(self._submit(self._simple("set_volume", int(volume)), _CMD_TIMEOUT))


# Module-level singleton + thin module functions.
_manager = _LoungeManager()

is_paired = _manager.is_paired
pair = _manager.pair
pair_with_screen_id = _manager.pair_with_screen_id
play_video = _manager.play_video
set_queue = _manager.set_queue
play_playlist = _manager.play_playlist
next_video = _manager.next
previous_video = _manager.previous
pause = _manager.pause
play = _manager.play
seek_to = _manager.seek_to
set_volume = _manager.set_volume
