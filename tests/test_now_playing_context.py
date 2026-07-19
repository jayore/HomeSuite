from __future__ import annotations

import unittest


class NowPlayingContextTests(unittest.TestCase):
    def setUp(self):
        from response_context import clear_response_context

        clear_response_context()

    def tearDown(self):
        from response_context import clear_response_context

        clear_response_context()

    def _answer(self, states, *, sonos_players=None, apple_tv_entity=""):
        from now_playing_controls import handle_now_playing_controls

        return handle_now_playing_controls(
            tl="What's playing?",
            states_snapshot=states,
            sonos_players=sonos_players or {},
            default_sonos_room="living room",
            apple_tv_entity=apple_tv_entity,
            get_transport_focus=lambda: (None, None),
        )

    def _context(self):
        from response_context import consume_response_context

        response_context = consume_response_context() or {}
        self.assertEqual(response_context.get("kind"), "now_playing")
        return response_context.get("data") or {}

    def test_sonos_song_preserves_artist_and_album(self):
        response = self._answer(
            [
                {
                    "entity_id": "media_player.living_room",
                    "state": "playing",
                    "attributes": {
                        "media_content_type": "music",
                        "media_title": "Everything in Its Right Place",
                        "media_artist": "Radiohead",
                        "media_album_name": "Kid A",
                    },
                }
            ],
            sonos_players={"living room": "media_player.living_room"},
        )

        self.assertIn("Everything in Its Right Place by Radiohead", response)
        context = self._context()

        self.assertEqual(context["media_kind"], "song")
        self.assertEqual(context["title"], "Everything in Its Right Place")
        self.assertEqual(context["artist"], "Radiohead")
        self.assertEqual(context["album"], "Kid A")

    def test_plex_title_only_video_is_typed_as_movie(self):
        self._answer(
            [
                {
                    "entity_id": "media_player.apple_tv",
                    "state": "playing",
                    "attributes": {
                        "app_name": "Plex",
                        "media_content_type": "video",
                        "media_title": "The Matrix",
                    },
                }
            ],
            apple_tv_entity="media_player.apple_tv",
        )
        context = self._context()

        self.assertEqual(context["media_kind"], "movie")
        self.assertEqual(context["title"], "The Matrix")
        self.assertEqual(context["app"], "Plex")

    def test_plex_episode_preserves_series_and_episode(self):
        self._answer(
            [
                {
                    "entity_id": "media_player.apple_tv",
                    "state": "playing",
                    "attributes": {
                        "app_name": "Plex",
                        "media_content_type": "video",
                        "media_title": "S2 · E3: The Treasure",
                        "media_artist": "The Amazing World of Gumball",
                        "media_season": 2,
                        "media_episode": 3,
                    },
                }
            ],
            apple_tv_entity="media_player.apple_tv",
        )
        context = self._context()

        self.assertEqual(context["media_kind"], "tv_episode")
        self.assertEqual(context["series"], "The Amazing World of Gumball")
        self.assertEqual(context["episode_title"], "The Treasure")
        self.assertEqual(context["season"], 2)
        self.assertEqual(context["episode"], 3)

    def test_apple_music_song_preserves_artist_and_album(self):
        self._answer(
            [
                {
                    "entity_id": "media_player.apple_tv",
                    "state": "playing",
                    "attributes": {
                        "app_name": "Music",
                        "media_content_type": "music",
                        "media_title": "Dreams",
                        "media_artist": "Fleetwood Mac",
                        "media_album_name": "Rumours",
                    },
                }
            ],
            apple_tv_entity="media_player.apple_tv",
        )
        context = self._context()

        self.assertEqual(context["media_kind"], "song")
        self.assertEqual(context["artist"], "Fleetwood Mac")
        self.assertEqual(context["album"], "Rumours")


if __name__ == "__main__":
    unittest.main()
