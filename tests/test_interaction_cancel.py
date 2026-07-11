from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class InteractionCancelMatcherTests(unittest.TestCase):
    def test_exact_cancel_phrases_match(self):
        from interaction_flow import is_interaction_cancel

        for text in (
            "cancel",
            "Cancel.",
            "never mind",
            "Nevermind!",
            "please cancel",
            "never mind please",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_interaction_cancel(text))

    def test_action_cancellation_commands_are_not_stolen(self):
        from interaction_flow import is_interaction_cancel

        for text in (
            "cancel my timer",
            "cancel the alarm",
            "cancel that schedule",
            "never mind the bedroom lights",
            "stop",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_interaction_cancel(text))


class InteractionCancelTextFlowTests(unittest.TestCase):
    def test_cancel_is_silent_and_bypasses_routing(self):
        from interaction_flow import handle_text_interaction

        runtime = mock.Mock()
        runtime._ACTION_OCCURRED = True

        result = handle_text_interaction(runtime, "never mind")

        self.assertTrue(result.handled)
        self.assertFalse(result.action_occurred)
        self.assertEqual(result.response_text, "")
        self.assertEqual(result.source, "cancelled")
        runtime.process_device_commands.assert_not_called()
        runtime.get_chatgpt_response.assert_not_called()


class InteractionCancelVoiceFlowTests(unittest.TestCase):
    def test_voice_cancel_bypasses_routing_and_all_outcome_audio(self):
        with mock.patch.dict(os.environ, {"PIPHONE_NO_RUNTIME_INIT": "1"}):
            import main

        for trigger in ("wakeword", "ptt"):
            with self.subTest(trigger=trigger):
                with (
                    mock.patch.object(main, "touch_session"),
                    mock.patch.object(main, "refresh_runnable_cache"),
                    mock.patch.object(main, "_perf"),
                    mock.patch.object(main, "_trace_audio_event"),
                    mock.patch.object(main, "transcribe_audio", return_value="never mind"),
                    mock.patch.object(main, "_strip_wakeword_prefix", side_effect=lambda text: text),
                    mock.patch.object(main, "process_device_commands") as route,
                    mock.patch.object(main, "get_chatgpt_response") as chat,
                    mock.patch.object(main, "_speak_text_for_trigger") as speak,
                    mock.patch.object(main, "play_error_sound") as error_tone,
                    mock.patch.object(main, "play_sound") as success_tone,
                ):
                    main.process_audio("ignored.wav", trigger=trigger)

                route.assert_not_called()
                chat.assert_not_called()
                speak.assert_not_called()
                error_tone.assert_not_called()
                success_tone.assert_not_called()


if __name__ == "__main__":
    unittest.main()
