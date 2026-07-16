"""youtube_controls.py — natural-language control of the Apple TV YouTube app.

Routes a handful of YouTube intents to the Lounge session (youtube_lounge),
backed by the channel registry (youtube_channels) and RSS feeds (youtube_feed):

    * "watch my daily reel" / "play my youtube digest" / "what's new on youtube"
    * "continue my reel"
    * "watch <channel>" (known channel) / "watch <x> on youtube" / "youtube <x>"
    * "next video" / "next channel" / "previous video"
    * "add <channel> to my digest" / "remove <channel> from my digest"
    * "what's in my digest"
    * "add youtube channel <@handle | url>"

Verb split (matches Plex): "watch" = video, "play" = music. YouTube shares the
"watch" verb with Plex and is checked first, but a bare "watch <x>" is only
claimed when <x> resolves to a *known* channel (otherwise it falls through to
Plex). An explicit "youtube"/"on the tv" qualifier claims even an unknown name
(with a hint). "play <x>" is never claimed here, so music routing is untouched.
The reel/digest verbs are accepted with either "watch" or "play" since
"my reel"/"my digest" can't collide with a song.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import youtube_channels
import youtube_feed
import youtube_lounge
import youtube_meta
import youtube_oauth
import youtube_playlist
import youtube_reels
from runtime_mode import allow_real_effects

log = logging.getLogger("youtube_controls")

_LAUNCH_SCRIPT = "script.apple_tv_launch_youtube"
_NO_TV = "I couldn't reach the YouTube app. Is the Apple TV on and the app open?"

# Set per-request by handle_youtube_controls. Called on a genuine success (Lounge
# playback or a registry change) so the dispatcher's success tone fires even when
# the spoken confirmation is silent and the preflight made no HA call (the Lounge
# cast isn't an HA call, so it wouldn't otherwise mark ACTION_OCCURRED). Commands
# are processed serially, so a module-level hook is safe here.
_mark_action = lambda: None  # noqa: E731


def _duration_getter():
    """youtube_meta.get_duration when the Data API is authed, else None so
    min-duration filtering is simply skipped (title filters still apply)."""
    try:
        return youtube_meta.get_duration if youtube_oauth.is_authed() else None
    except Exception:
        return None


def _launch(call_ha_service) -> None:
    try:
        call_ha_service("script/turn_on", {"entity_id": _LAUNCH_SCRIPT})
    except Exception:
        pass


def _clean_target(s: str) -> str:
    s = (s or "").strip().strip("'\"")
    s = re.sub(r"^(?:the\s+)?", "", s)
    s = re.sub(r"\s+(?:channel|please|now)$", "", s).strip()
    return s


# Phrases that sit before "reel/roundup/digest" but name the *global* reel rather
# than a themed group, e.g. "my daily reel", "the reel", "youtube digest".
_GLOBAL_REEL_WORDS = {"", "daily", "my", "the", "youtube", "you tube", "new",
                      "today", "today's", "todays", "latest"}


def _reel_group_raw(raw: Optional[str]) -> Optional[str]:
    """Clean a captured pre-keyword phrase into a group name, or None for the
    global reel. Returns the spoken phrase (not yet resolved to a stored key)."""
    g = (raw or "").strip().strip("'\"")
    g = re.sub(r"^(?:my|the)\s+", "", g).strip()
    if g.lower() in _GLOBAL_REEL_WORDS:
        return None
    return g or None


# --- actions ---

def _play_channel(cid: str, launch, maybe_say) -> Optional[str]:
    # newest_qualifying applies the channel's filters (e.g. Seth's "a closer look"
    # include) so 'watch <channel>' lands on the proper segment, not a Short.
    v = youtube_feed.newest_qualifying(cid, get_duration=_duration_getter())
    title = youtube_channels.channel_title(cid) or "that channel"
    if not v:
        return maybe_say(f"I couldn't find a recent video from {title}.")
    if not allow_real_effects():
        return maybe_say(f"Test preview: would play {v['title']} from {v['channel_title'] or title}.")
    launch()
    if youtube_lounge.play_video(v["video_id"]):
        _mark_action()
        youtube_feed.mark_played([v["video_id"]])
        return maybe_say(f"Playing {v['title']} from {v['channel_title'] or title}.")
    return maybe_say(_NO_TV)


def _play_named_playlist(name: str, launch, maybe_say) -> Optional[str]:
    """Play any playlist on the account by (fuzzy) title — incl. the PiPhone · X
    ones, since their titles contain the group word."""
    if not youtube_oauth.is_authed():
        return maybe_say("I need YouTube account access for playlists — run the youtube login first.")
    res = youtube_playlist.search_by_title(name)
    if not res:
        return maybe_say(f"I couldn't find a playlist called {name}.")
    pid, title = res
    if not allow_real_effects():
        return maybe_say(f"Test preview: would play the {title} playlist.")
    vids = youtube_playlist.list_video_ids(pid)
    launch()
    if youtube_lounge.play_playlist(pid, vids[0] if vids else None):
        _mark_action()
        return maybe_say(f"Playing the {title} playlist.")
    return maybe_say(_NO_TV)


def _play_digest(launch, maybe_say, *, resume: bool,
                 group: Optional[str] = None, label: str = "daily reel") -> Optional[str]:
    if not allow_real_effects():
        return maybe_say(f"Test preview: would play your {label}.")

    # Static real-playlist playback: play the scope's persistent "PiPhone · X"
    # playlist (as fresh as the last scheduled refresh). The YouTube app resumes
    # the playlist itself, so 'continue' just replays it. Falls back to an
    # on-the-fly cast queue when the Data API isn't authed/available.
    # Fast path: cached playlist id + first video (zero API calls), kept current by
    # the evening scheduler refresh / a prior play.
    cpid, cfirst = youtube_reels.cached_play_target(group)
    if cpid and cfirst:
        launch()
        if youtube_lounge.play_playlist(cpid, cfirst):
            _mark_action()
            return maybe_say(f"Playing your {label}.")
        return maybe_say(_NO_TV)

    pid = youtube_reels.get_playlist_id(group)  # cached id, no API call
    if pid:
        vids = youtube_playlist.list_video_ids(pid)  # one API read (then cached)
        if not vids:
            # Never populated (e.g. first run) — build it once, then re-read.
            youtube_reels.sync_playlist(group, wipe=True)
            vids = youtube_playlist.list_video_ids(pid)
        if not vids:
            return maybe_say(f"Your {label} is empty right now — nothing new yet.")
        youtube_reels.note_playlist_ids(group, pid, vids)  # warm the cache for next time
        launch()
        if youtube_lounge.play_playlist(pid, vids[0]):
            _mark_action()
            return maybe_say(f"Playing your {label}.")
        return maybe_say(_NO_TV)

    # Fallback: ephemeral cast queue built from the live digest.
    if resume and not group:
        ids = youtube_feed.last_digest()
        first_title = None
    else:
        vids = youtube_feed.build_digest(include_watched=False, group=group,
                                         get_duration=_duration_getter())
        ids = [v["video_id"] for v in vids]
        first_title = vids[0]["title"] if vids else None
    if not ids:
        return maybe_say(f"I didn't find any new videos for your {label}.")
    launch()
    if youtube_lounge.set_queue(ids):
        _mark_action()
        if not (resume and not group):
            youtube_feed.mark_played(ids, digest=ids)
        n = len(ids)
        plural = "s" if n != 1 else ""
        if first_title:
            return maybe_say(f"Queued {n} video{plural}, starting with {first_title}.")
        return maybe_say(f"Resuming your {label} — {n} video{plural}.")
    return maybe_say(_NO_TV)


def _list_digest(maybe_say, *, group: Optional[str] = None, label: str = "daily reel") -> Optional[str]:
    cids = youtube_channels.channels_in_group(group) if group else youtube_channels.digest_channels()
    if not cids:
        return maybe_say(f"Your {label} is empty. Add channels with 'add <channel> to my {label}'.")
    names = [youtube_channels.channel_title(c) or c for c in cids]
    _mark_action()  # a successful query — don't error-tone it
    return maybe_say(f"Your {label}: " + ", ".join(names) + ".")


def _add_to_digest(name: str, maybe_say, *, group: Optional[str] = None) -> Optional[str]:
    cid = youtube_channels.resolve_channel(name)
    if not cid:
        return maybe_say(f"I don't have a channel called {name}. Add it first with "
                         f"'add youtube channel @handle'.")
    title = youtube_channels.channel_title(cid) or name
    if not allow_real_effects():
        label = f"{group} roundup" if group else "reel"
        return maybe_say(f"Test preview: would add {title} to your {label}.")
    if group:
        youtube_channels.add_to_group(cid, group)
        _mark_action()
        return maybe_say(f"Added {title} to your {group} roundup.")
    youtube_channels.set_in_digest(cid, True)
    _mark_action()
    return maybe_say(f"Added {title} to your reel.")


def _remove_from_digest(name: str, maybe_say, *, group: Optional[str] = None) -> Optional[str]:
    cid = youtube_channels.resolve_channel(name)
    if not cid:
        return maybe_say(f"I don't have a channel called {name}.")
    title = youtube_channels.channel_title(cid) or name
    if not allow_real_effects():
        label = f"{group} roundup" if group else "reel"
        return maybe_say(f"Test preview: would remove {title} from your {label}.")
    if group:
        youtube_channels.remove_from_group(cid, group)
        _mark_action()
        return maybe_say(f"Removed {title} from your {group} roundup.")
    youtube_channels.set_in_digest(cid, False)
    _mark_action()
    return maybe_say(f"Removed {title} from your reel.")


def _add_channel(handle_or_url: str, maybe_say) -> Optional[str]:
    if not allow_real_effects():
        return maybe_say(f"Test preview: would add YouTube channel {handle_or_url}.")
    cid = youtube_channels.resolve_handle_to_id(handle_or_url)
    if not cid:
        return maybe_say(f"I couldn't find a channel for {handle_or_url}.")
    title = ""
    vids = youtube_feed.latest_videos(cid, limit=1)
    if vids:
        title = vids[0].get("channel_title") or ""
    youtube_channels.upsert_channel(cid, title=title, handle=handle_or_url.strip())
    _mark_action()
    return maybe_say(f"Added channel {title or cid}. Say 'add {title or 'it'} to my digest' "
                     f"to include it in your reel.")


# --- main router ---

def handle_youtube_controls(*, tl: str, call_ha_service, maybe_say,
                            preflight=None, mark_action=None) -> Optional[str]:
    t = (tl or "").strip().lower()
    if not t:
        return None

    global _mark_action
    _mark_action = mark_action or (lambda: None)

    # Confirmations are voiced via the media-confirmation path (the dispatcher
    # passes _maybe_say_media, gated by SPEAK_MEDIA_CONFIRMATIONS — default on).

    # Full TV preflight (turn on TV scene + wake ATV + launch YouTube app) when the
    # dispatcher supplies one; otherwise just launch the app. Only ever runs once a
    # command is actually claimed below — music 'play <x>' returns None untouched.
    def launch():
        if preflight:
            preflight()
        else:
            _launch(call_ha_service)

    has_yt = bool(re.search(r"\b(youtube|you tube|on the tv)\b", t))

    # --- add a channel by handle / url ---
    m = re.search(r"\badd\s+(?:youtube\s+)?channel\s+(.+)$", t)
    if m:
        return _add_channel(m.group(1).strip(), maybe_say)

    # --- digest / roundup membership management ---
    # The optional (.+?\s+)? before the keyword captures a group name ("late
    # night") for a themed roundup; absent/global words -> the global reel.
    m = re.search(r"\badd\s+(.+?)\s+to\s+(?:my\s+|the\s+)?(?:youtube\s+)?"
                  r"(.+?\s+)?(?:digest|reel|roundup)\b", t)
    if m:
        return _add_to_digest(_clean_target(m.group(1)), maybe_say,
                              group=_reel_group_raw(m.group(2)))
    m = re.search(r"\bremove\s+(.+?)\s+from\s+(?:my\s+|the\s+)?(?:youtube\s+)?"
                  r"(.+?\s+)?(?:digest|reel|roundup)\b", t)
    if m:
        return _remove_from_digest(_clean_target(m.group(1)), maybe_say,
                                   group=_reel_group_raw(m.group(2)))
    m = re.search(r"\bwhat'?s\s+in\s+my\s+(?:youtube\s+)?(.+?\s+)?(?:digest|reel|roundup)\b", t)
    if not m:
        m = re.search(r"\b(?:list|show)\s+(?:my\s+)?(?:youtube\s+)?"
                      r"(.+?\s+)?(?:digest|reel|roundup)(?:\s+channels)?\b", t)
    if m:
        grp = _reel_group_raw(m.group(1))
        return _list_digest(maybe_say, group=grp,
                            label=f"{grp} roundup" if grp else "reel")

    # --- play any account playlist by name: "watch my <name> playlist" ---
    # Scoped to the watch-family verb (Spotify owns "play <x> playlist"); an
    # explicit "on youtube" lets "play" through too.
    m = re.search(r"\b(?:watch|put on|cue|queue|start)\s+(?:my\s+|the\s+)?(.+?)\s+playlist\b", t)
    if not m:
        m = re.search(r"\b(?:play|watch)\s+(?:my\s+|the\s+)?(.+?)\s+playlist\s+on\s+"
                      r"(?:youtube|the\s+tv)\b", t)
    if m:
        return _play_named_playlist(_clean_target(m.group(1)), launch, maybe_say)

    # --- digest / roundup playback ---
    resume = bool(re.search(r"\b(?:continue|resume)\b", t))
    # 1) "(play|watch) my/the [<group>] reel|roundup|digest"
    m = re.search(r"\b(?:my|the)\s+(?:youtube\s+)?(.+?\s+)?(?:roundup|reel|digest)\b", t)
    # 2) "(play|watch) <group> roundup|reel|digest" (no my/the; group required)
    if not m:
        m = re.search(r"\b(?:play|watch|start|put on|cue|queue)\s+(?:my\s+|the\s+)?"
                      r"(.+?)\s+(?:roundup|reel|digest)\b", t)
    if m:
        grp = _reel_group_raw(m.group(1))
        if grp is None:
            return _play_digest(launch, maybe_say, resume=resume)
        key = youtube_channels.resolve_group(grp)
        if key:
            return _play_digest(launch, maybe_say, resume=resume,
                                group=key, label=f"{grp} roundup")
        return maybe_say(f"I don't have a {grp} roundup yet. Add channels with "
                         f"'add <channel> to my {grp} roundup'.")
    # 3) bare "digest"/"reel"/"roundup" or "what's new on youtube" -> global reel
    if re.search(r"\b(?:digest|reel|roundup)\b", t) or \
       (("what's new" in t or "whats new" in t) and has_yt):
        return _play_digest(launch, maybe_say, resume=resume)

    # --- queue navigation ---
    if re.search(r"\bnext\s+(?:channel|video)\b", t):
        if not allow_real_effects():
            return maybe_say("Test preview: would skip to the next YouTube video.")
        return maybe_say("Next.") if youtube_lounge.next_video() else maybe_say(_NO_TV)
    if re.search(r"\b(?:previous|last)\s+(?:channel|video)\b", t):
        if not allow_real_effects():
            return maybe_say("Test preview: would return to the previous YouTube video.")
        return maybe_say("Previous.") if youtube_lounge.previous_video() else maybe_say(_NO_TV)

    # --- watch a channel's latest ---
    # "watch" is the video verb (shared with Plex). Bare "watch <x>" is
    # registry-gated: known channel → YouTube, otherwise fall through to Plex.
    # Only an explicit youtube qualifier claims an *unknown* name (with a hint).
    target: Optional[str] = None
    explicit_yt = False
    m = re.search(r"\bwatch\s+(?:the\s+)?latest\s+(?:from\s+|video\s+from\s+)?(.+)$", t)  # "watch (the) latest from X"
    if m:
        target = m.group(1)
    if target is None:
        m = re.search(r"\bwatch\s+(.+?)(?:'s)?\s+latest\b", t)             # "watch X('s) latest"
        if m:
            target = m.group(1)
    if target is None:
        m = re.search(r"\b(?:watch|play)\s+(.+?)\s+on\s+(?:youtube|the\s+tv)\b", t)  # "... X on youtube" (explicit)
        if m:
            target, explicit_yt = m.group(1), True
    if target is None:
        m = re.search(r"\b(?:youtube|you tube)\s+(.+)$", t)               # "youtube X" (explicit)
        if m and m.group(1).strip() not in ("tv", ""):
            target, explicit_yt = m.group(1), True
    if target is None:
        m = re.search(r"\bwatch\s+(.+)$", t)                              # bare "watch X" — known channel only
        if m:
            target = m.group(1)

    if target:
        target = _clean_target(target)
        cid = youtube_channels.resolve_channel(target)
        if cid:
            return _play_channel(cid, launch, maybe_say)
        # Explicit youtube intent but unknown channel → claim with a hint.
        # Bare "watch <unknown>" returns None so Plex can try the title.
        if explicit_yt:
            return maybe_say(f"I don't have a channel called {target}.")

    return None
