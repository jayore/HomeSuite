"""youtube_reel_scheduler.py — evening refresh of the YouTube reel playlists.

Registers a self-throttling periodic task with scheduler.py. Within the configured
evening window (default 16:30–22:30 local) it calls youtube_reels.refresh_tick()
every YOUTUBE_REEL_REFRESH_INTERVAL_S, which:
  * wipes + rebuilds every "PiPhone · X" playlist on the first run each day, then
  * diff-adds newly-posted episodes on later runs (cheap).

The scheduler loop ticks every second, so this gates on both the window and the
interval. The daily-wipe bookkeeping lives in youtube_reels (state file), so a
mid-window restart resumes correctly without re-wiping.
"""

from __future__ import annotations

import datetime
import logging
import time

log = logging.getLogger("youtube_reel_scheduler")

_last_run = 0.0


def _cfg():
    import app_config as prefs
    enabled = getattr(prefs, "YOUTUBE_REEL_REFRESH_ENABLED", True)
    window = getattr(prefs, "YOUTUBE_REEL_WINDOW", (16, 30, 22, 30))
    interval = getattr(prefs, "YOUTUBE_REEL_REFRESH_INTERVAL_S", 300)
    return enabled, window, int(interval)


def _in_window(now: datetime.datetime, window) -> bool:
    sh, sm, eh, em = window
    start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    return start <= now <= end


def tick() -> None:
    """One scheduler poll tick — runs the reel refresh when in-window and due."""
    global _last_run
    enabled, window, interval = _cfg()
    if not enabled:
        return
    if not _in_window(datetime.datetime.now(), window):
        return
    t = time.time()
    if t - _last_run < interval:
        return
    _last_run = t
    try:
        import youtube_reels
        res = youtube_reels.refresh_tick()
        added = sum((r or {}).get("added", 0) for r in res.values())
        if added:
            log.info("YT_REEL_REFRESH added=%d across %d playlists", added, len(res))
    except Exception:
        log.exception("YT_REEL_REFRESH_FAIL")


def register() -> None:
    """Register tick() with the scheduler's periodic-task loop."""
    import scheduler
    scheduler.register_periodic(tick)
    log.info("YT_REEL_SCHEDULER_REGISTERED")
