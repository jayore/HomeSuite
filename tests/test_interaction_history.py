from __future__ import annotations

import unittest


class _NowPlayingRuntime:
    def __init__(self):
        self._ACTION_OCCURRED = False
        self._responses = iter(("The Matrix.", None))
        self.history_seen_by_ai = []

    def process_device_commands(self, _text):
        return next(self._responses)

    def _looks_like_chatgpt_intent(self, _text):
        return True

    def _looks_like_joke_request(self, _text):
        return False

    def get_chatgpt_response(self, _text):
        from interaction_flow import get_history_snapshot

        self.history_seen_by_ai = get_history_snapshot()
        return "The Matrix is about a hacker who discovers his reality is simulated."


class InteractionHistoryTests(unittest.TestCase):
    def setUp(self):
        from interaction_flow import reset_history
        from request_context import RequestContext, set_current_request_context

        reset_history(all_scopes=True)
        set_current_request_context(RequestContext(source_id="telegram"))

    def tearDown(self):
        from interaction_flow import reset_history
        from request_context import clear_current_request_context

        clear_current_request_context()
        reset_history(all_scopes=True)

    def test_short_now_playing_reply_reaches_source_scoped_ai_followup(self):
        from interaction_flow import get_history_snapshot, handle_text_interaction
        from request_context import RequestContext, set_current_request_context

        runtime = _NowPlayingRuntime()
        first = handle_text_interaction(runtime, "What's playing?")

        self.assertEqual(first.response_text, "The Matrix.")
        self.assertEqual(
            get_history_snapshot()[-1],
            {"role": "assistant", "content": "Currently playing: The Matrix."},
        )

        set_current_request_context(RequestContext(source_id="default_piphone"))
        self.assertEqual(len(get_history_snapshot()), 1)

        set_current_request_context(RequestContext(source_id="telegram"))
        second = handle_text_interaction(runtime, "What's it about?")

        self.assertEqual(second.source, "chatgpt")
        self.assertIn(
            {"role": "assistant", "content": "Currently playing: The Matrix."},
            runtime.history_seen_by_ai,
        )

    def test_unrelated_short_device_text_stays_out_of_ai_history(self):
        from interaction_flow import get_history_snapshot, inject_device_response_history

        inject_device_response_history("pause", "Paused.")

        self.assertEqual(len(get_history_snapshot()), 1)

    def test_structured_now_playing_metadata_enriches_ai_history(self):
        from interaction_flow import get_history_snapshot, inject_device_response_history
        from response_context import set_response_context

        set_response_context(
            "now_playing",
            {
                "media_kind": "song",
                "title": "Everything in Its Right Place",
                "artist": "Radiohead",
                "album": "Kid A",
            },
        )
        inject_device_response_history(
            "What's playing?",
            "You're listening to Everything in Its Right Place by Radiohead.",
        )

        self.assertEqual(
            get_history_snapshot()[-1],
            {
                "role": "assistant",
                "content": (
                    'Current media for follow-up questions: song "Everything in Its Right Place" '
                    'by Radiohead, from the album "Kid A".'
                ),
            },
        )


if __name__ == "__main__":
    unittest.main()
