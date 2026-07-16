"""Regression tests for direct side-effect guards in preview runtimes."""

from __future__ import annotations

import unittest
from unittest import mock

import applet_controls
import homelab_controls
import spotify_controls
import youtube_controls


class RuntimeEffectGuardTests(unittest.TestCase):
    def test_applet_lifecycle_is_previewed_without_starting_or_stopping(self):
        with (
            mock.patch.object(applet_controls, "allow_real_effects", return_value=False),
            mock.patch.object(applet_controls, "is_running", return_value=False),
            mock.patch.object(applet_controls, "start_applet") as start_applet,
            mock.patch.object(applet_controls, "stop_applet") as stop_applet,
        ):
            result = applet_controls._drive_lifecycle("toggle", "note_lights")

        self.assertEqual(result, "Test preview: would start note lights.")
        start_applet.assert_not_called()
        stop_applet.assert_not_called()

    def test_spotify_library_mutations_are_previewed(self):
        maybe_say = lambda text: text

        with (
            mock.patch.object(spotify_controls, "spotify_web_configured", return_value=True),
            mock.patch.object(spotify_controls, "allow_real_effects", return_value=False),
            mock.patch.object(spotify_controls, "like_current_track") as like_current_track,
        ):
            result = spotify_controls.handle_spotify_controls(
                "like this song",
                maybe_say=maybe_say,
            )

        self.assertEqual(result, "Test preview: would save the current track.")
        like_current_track.assert_not_called()

        with (
            mock.patch.object(spotify_controls, "spotify_web_configured", return_value=True),
            mock.patch.object(spotify_controls, "allow_real_effects", return_value=False),
            mock.patch.object(
                spotify_controls,
                "add_current_track_to_playlist",
            ) as add_to_playlist,
        ):
            result = spotify_controls.handle_spotify_controls(
                "add this song to road trip",
                maybe_say=maybe_say,
            )

        self.assertEqual(
            result,
            "Test preview: would add the current track to road trip.",
        )
        add_to_playlist.assert_not_called()

    def test_youtube_playback_and_registry_mutations_are_previewed(self):
        maybe_say = lambda text: text

        with (
            mock.patch.object(youtube_controls, "allow_real_effects", return_value=False),
            mock.patch.object(youtube_controls.youtube_lounge, "next_video") as next_video,
        ):
            result = youtube_controls.handle_youtube_controls(
                tl="next video",
                call_ha_service=mock.Mock(),
                maybe_say=maybe_say,
            )

        self.assertEqual(
            result,
            "Test preview: would skip to the next YouTube video.",
        )
        next_video.assert_not_called()

        with (
            mock.patch.object(youtube_controls, "allow_real_effects", return_value=False),
            mock.patch.object(
                youtube_controls.youtube_channels,
                "resolve_handle_to_id",
            ) as resolve_handle,
            mock.patch.object(
                youtube_controls.youtube_channels,
                "upsert_channel",
            ) as upsert_channel,
        ):
            result = youtube_controls._add_channel("@example", maybe_say)

        self.assertEqual(
            result,
            "Test preview: would add YouTube channel @example.",
        )
        resolve_handle.assert_not_called()
        upsert_channel.assert_not_called()

    def test_qbittorrent_mutation_is_previewed(self):
        with (
            mock.patch.object(homelab_controls, "allow_real_effects", return_value=False),
            mock.patch.object(
                homelab_controls,
                "qbittorrent_pause_completed",
            ) as pause_completed,
        ):
            result = homelab_controls._pause_completed_response(
                "pause completed torrents"
            )

        self.assertEqual(
            result,
            "Test preview: would pause completed qBittorrent downloads.",
        )
        pause_completed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
