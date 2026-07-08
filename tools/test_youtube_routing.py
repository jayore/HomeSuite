"""test_youtube_routing.py — isolation tests for handle_youtube_controls.

Verifies the dispatch contract for YouTube routing WITHOUT touching the network,
the real channel registry, or a paired Lounge session. The three collaborator
modules (youtube_channels, youtube_feed, youtube_lounge) are monkeypatched with
spies/fakes, so this runs anywhere (no pairing, no Google account).

Asserts (plan verification step 5):
  * "watch <known channel>"  -> launch script + play_video, claimed
  * "play my daily reel"     -> set_queue, claimed
  * "next video"             -> next_video, claimed
  * "add <ch> to my digest"  -> set_in_digest(cid, True), claimed
  * "watch <unknown title>"  -> None (so Plex can claim it)
  * "play <artist>"          -> None (so Spotify/Plex music routing is untouched)
  * "watch <unknown> on youtube" -> claimed with a hint (explicit qualifier)

Run: .venv/bin/python tools/test_youtube_routing.py
"""

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import youtube_channels
import youtube_feed
import youtube_lounge
import youtube_oauth
import youtube_playlist
import youtube_reels
from youtube_controls import handle_youtube_controls

KNOWN_NAME = "john oliver"
KNOWN_CID = "UCfakeJohnOliver"

# --- spies / fakes ---

calls = {"ha": [], "play_video": [], "set_queue": [], "next": [], "prev": [],
         "set_in_digest": [], "mark_played": [], "digest_group": [],
         "add_to_group": [], "remove_from_group": [], "play_playlist": [],
         "pl_scope": [], "mark_action": []}

# Toggled per-test: when None, get_playlist_id returns None to exercise the
# ephemeral-queue fallback path.
_PLAYLIST_AVAILABLE = True


def reset_calls():
    for k in calls:
        calls[k] = []


def fake_resolve_channel(name):
    return KNOWN_CID if (name or "").strip().lower() == KNOWN_NAME else None


def fake_channel_title(cid):
    return "John Oliver" if cid == KNOWN_CID else None


def fake_latest_videos(cid, limit=1):
    if cid == KNOWN_CID:
        return [{"video_id": "vid123", "title": "Latest Bit",
                 "channel_title": "John Oliver"}]
    return []


def fake_build_digest(*, include_watched=False, group=None, **kw):
    calls["digest_group"].append(group)
    if group == "latenight":
        return [{"video_id": "ln1", "title": "Late Night One"}]
    return [{"video_id": "a1", "title": "Reel One"},
            {"video_id": "b2", "title": "Reel Two"}]


def fake_resolve_group(name):
    return "latenight" if (name or "").strip().lower() in ("late night", "latenight") else None


def install_fakes():
    youtube_channels.resolve_channel = fake_resolve_channel
    youtube_channels.channel_title = fake_channel_title
    youtube_channels.digest_channels = lambda: [KNOWN_CID]
    youtube_channels.set_in_digest = lambda cid, val: calls["set_in_digest"].append((cid, val)) or True
    youtube_channels.resolve_group = fake_resolve_group
    youtube_channels.channels_in_group = lambda g: [KNOWN_CID] if fake_resolve_group(g) else []
    youtube_channels.add_to_group = lambda cid, g: calls["add_to_group"].append((cid, g)) or True
    youtube_channels.remove_from_group = lambda cid, g: calls["remove_from_group"].append((cid, g)) or True

    youtube_feed.latest_videos = fake_latest_videos
    youtube_feed.newest_qualifying = lambda cid, **kw: (fake_latest_videos(cid, limit=1) or [None])[0]
    youtube_feed.build_digest = fake_build_digest
    youtube_feed.last_digest = lambda: []
    youtube_feed.mark_played = lambda ids, **kw: calls["mark_played"].append((list(ids), kw))

    youtube_lounge.play_video = lambda vid: calls["play_video"].append(vid) or True
    youtube_lounge.set_queue = lambda ids: calls["set_queue"].append(list(ids)) or True
    youtube_lounge.play_playlist = lambda pid, vid=None: calls["play_playlist"].append(pid) or True
    youtube_lounge.next_video = lambda: calls["next"].append(1) or True
    youtube_lounge.previous_video = lambda: calls["prev"].append(1) or True

    # Static real-playlist path: get_playlist_id records the scope and returns a
    # fake id (unless _PLAYLIST_AVAILABLE is off, to exercise the queue fallback).
    def fake_get_pid(scope, **kw):
        calls["pl_scope"].append(scope)
        return f"PL_{scope or 'daily'}" if _PLAYLIST_AVAILABLE else None
    youtube_reels.get_playlist_id = fake_get_pid
    # Default: no cache hit, so tests exercise the get_playlist_id slow path/assertions.
    youtube_reels.cached_play_target = lambda scope: (None, None)
    youtube_reels.note_playlist_ids = lambda *a, **k: None
    youtube_playlist.list_video_ids = lambda pid: ["x"]  # non-empty
    # play-any-playlist-by-name
    youtube_oauth.is_authed = lambda: True
    youtube_playlist.search_by_title = lambda q: (
        ("PLfake", "Workout") if "workout" in (q or "").lower() else None)


def fake_ha(service, data):
    calls["ha"].append((service, data))


def maybe_say(text):
    # Mirrors the real maybe_say contract: returns the text (handled, non-None).
    return text


def run(tl):
    reset_calls()
    return handle_youtube_controls(tl=tl, call_ha_service=fake_ha, maybe_say=maybe_say,
                                   mark_action=lambda: calls["mark_action"].append(1))


# --- cases ---

def main():
    install_fakes()
    failures = []

    def check(name, cond):
        print(f"{'OK' if cond else 'FAIL'} | {name}")
        if not cond:
            failures.append(name)

    # 1. known channel -> launch + play_video, claimed
    r = run("watch john oliver")
    check("watch known channel -> claimed", r is not None)
    check("watch known channel -> launched app",
          ("script/turn_on", {"entity_id": "script.apple_tv_launch_youtube"}) in calls["ha"])
    check("watch known channel -> play_video('vid123')", calls["play_video"] == ["vid123"])

    # 2. daily reel -> static playlist (scope None), claimed
    r = run("play my daily reel")
    check("daily reel -> claimed", r is not None)
    check("daily reel -> play_playlist('PL_daily')", calls["play_playlist"] == ["PL_daily"])
    check("daily reel -> scope None", calls["pl_scope"] == [None])
    check("daily reel -> mark_action fired (success tone)", calls["mark_action"] == [1])

    # 3. next video -> next_video
    r = run("next video")
    check("next video -> claimed", r is not None)
    check("next video -> next_video()", calls["next"] == [1])

    # 4. add to digest -> set_in_digest(cid, True)
    r = run("add john oliver to my digest")
    check("add to digest -> claimed", r is not None)
    check("add to digest -> set_in_digest(cid, True)", calls["set_in_digest"] == [(KNOWN_CID, True)])

    # 5. unknown title, bare watch -> None (Plex fallthrough)
    r = run("watch the irishman")
    check("watch unknown title -> None (Plex)", r is None)
    check("watch unknown title -> no play_video", calls["play_video"] == [])

    # 6. music play -> None (Spotify/Plex untouched)
    r = run("play taylor swift")
    check("play artist -> None (music)", r is None)
    check("play artist -> no lounge calls",
          calls["play_video"] == [] and calls["set_queue"] == [])
    check("play artist -> no mark_action", calls["mark_action"] == [])

    # 7. explicit youtube qualifier on unknown -> claimed with hint
    r = run("watch the irishman on youtube")
    check("explicit youtube + unknown -> claimed (hint)", r is not None)
    check("explicit youtube + unknown -> no play_video", calls["play_video"] == [])

    # 8. group roundup playback -> static playlist for that group
    r = run("play my late night roundup")
    check("late night roundup -> claimed", r is not None)
    check("late night roundup -> scope 'latenight'", calls["pl_scope"] == ["latenight"])
    check("late night roundup -> play_playlist('PL_latenight')", calls["play_playlist"] == ["PL_latenight"])

    # 9. group roundup playback without my/the
    r = run("watch late night roundup")
    check("bare late night roundup -> play_playlist('PL_latenight')", calls["play_playlist"] == ["PL_latenight"])

    # 10. unknown group -> claimed with hint, no playback
    r = run("play my sports roundup")
    check("unknown roundup -> claimed (hint)", r is not None)
    check("unknown roundup -> no playback", calls["play_playlist"] == [] and calls["set_queue"] == [])

    # 11. daily reel still routes global (scope None), not a roundup
    r = run("play my daily reel")
    check("daily reel -> scope None (not a group)", calls["pl_scope"] == [None])

    # 11b. fallback: no Data API playlist -> ephemeral cast queue from the digest
    global _PLAYLIST_AVAILABLE
    _PLAYLIST_AVAILABLE = False
    r = run("play my daily reel")
    check("fallback daily reel -> set_queue(['a1','b2'])", calls["set_queue"] == [["a1", "b2"]])
    r = run("play my late night roundup")
    check("fallback late night -> set_queue(['ln1'])", calls["set_queue"] == [["ln1"]])
    _PLAYLIST_AVAILABLE = True

    # 12. add to a named roundup -> add_to_group(cid, 'late night')
    r = run("add john oliver to my late night roundup")
    check("add to roundup -> claimed", r is not None)
    check("add to roundup -> add_to_group(cid,'late night')", calls["add_to_group"] == [(KNOWN_CID, "late night")])
    check("add to roundup -> not set_in_digest (global untouched)", calls["set_in_digest"] == [])

    # 13. list a named roundup -> claimed
    r = run("what's in my late night roundup")
    check("list roundup -> claimed", r is not None)

    # 14. play any account playlist by name (watch-scoped)
    r = run("watch my workout playlist")
    check("watch named playlist -> claimed", r is not None)
    check("watch named playlist -> play_playlist('PLfake')", calls["play_playlist"] == ["PLfake"])

    # 14b. 'play <x> playlist' (no 'on youtube') stays with music (Spotify)
    r = run("play my chill playlist")
    check("play named playlist -> None (Spotify, not claimed)", r is None)

    # 14c. unknown playlist name -> claimed with 'couldn't find'
    r = run("watch my nonexistent playlist")
    check("watch unknown playlist -> claimed (not found)", r is not None)
    check("watch unknown playlist -> no play_playlist", calls["play_playlist"] == [])

    print(f"\n{len(failures)} failure(s)" if failures else "\nAll passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
