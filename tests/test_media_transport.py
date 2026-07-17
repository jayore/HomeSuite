from __future__ import annotations

from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dialogue_state
from request_context import clear_current_request_context


class BareMediaTransportTests(unittest.TestCase):
    def setUp(self):
        clear_current_request_context()
        dialogue_state.reset_dialogue_state(all_scopes=True)
        import command_dispatch

        command_dispatch.reset_dispatch_state()

    def tearDown(self):
        clear_current_request_context()
        dialogue_state.reset_dialogue_state(all_scopes=True)

    @contextmanager
    def _dispatch(self, states):
        import command_dispatch

        service = mock.Mock(return_value=True)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {"PIPHONE_LIVE": "1"}, clear=False))
            stack.enter_context(mock.patch.object(command_dispatch, "ha_get_states", return_value=states))
            stack.enter_context(mock.patch.object(command_dispatch, "call_ha_service", service))
            stack.enter_context(mock.patch.object(command_dispatch, "handle_temporary_action", return_value=None))
            stack.enter_context(mock.patch.object(command_dispatch, "handle_schedule_controls", return_value=None))
            stack.enter_context(mock.patch.object(command_dispatch, "handle_stock_quote_query", return_value=None))
            stack.enter_context(mock.patch.object(command_dispatch, "try_run_runnable_from_text", return_value=None))
            yield command_dispatch, service

    @staticmethod
    def _tv_passthrough_states():
        return [
            {
                "entity_id": "media_player.living_room_apple_tv",
                "state": "off",
                "attributes": {},
            },
            {
                "entity_id": "remote.living_room_apple_tv",
                "state": "on",
                "attributes": {},
            },
            {
                "entity_id": "media_player.living_room",
                "state": "playing",
                "attributes": {
                    "source": "TV",
                    "media_title": "TV",
                    "media_content_id": "x-sonos-htastream:RINCON_TEST:spdif",
                },
            },
        ]

    def test_pause_and_play_use_tv_remote_when_media_state_is_stale(self):
        with self._dispatch(self._tv_passthrough_states()) as (dispatch, service):
            paused = dispatch.process_device_commands("pause")
            resumed = dispatch.process_device_commands("play")

        self.assertEqual(paused, "")
        self.assertEqual(resumed, "")
        self.assertEqual(
            service.call_args_list,
            [
                mock.call(
                    "remote/send_command",
                    {
                        "entity_id": "remote.living_room_apple_tv",
                        "command": "pause",
                    },
                ),
                mock.call(
                    "remote/send_command",
                    {
                        "entity_id": "remote.living_room_apple_tv",
                        "command": "play",
                    },
                ),
            ],
        )

    def test_real_sonos_music_still_wins_over_tv_remote_fallback(self):
        states = self._tv_passthrough_states()
        states[-1] = {
            "entity_id": "media_player.living_room",
            "state": "playing",
            "attributes": {
                "source": "Spotify",
                "media_title": "A Song",
                "media_artist": "An Artist",
                "media_content_id": "x-sonos-spotify:test",
            },
        }

        with self._dispatch(states) as (dispatch, service):
            response = dispatch.process_device_commands("pause")

        self.assertEqual(response, "")
        service.assert_called_once_with(
            "media_player/media_pause",
            {"entity_id": "media_player.living_room"},
        )

    def test_bare_pause_does_not_guess_without_playback_evidence(self):
        states = self._tv_passthrough_states()
        states[-1] = {
            "entity_id": "media_player.living_room",
            "state": "idle",
            "attributes": {},
        }

        with self._dispatch(states) as (dispatch, service):
            response = dispatch.process_device_commands("pause")

        self.assertIsNone(response)
        service.assert_not_called()


if __name__ == "__main__":
    unittest.main()
