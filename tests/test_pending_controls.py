from __future__ import annotations

import unittest

from pending_controls import handle_pending_controls, looks_like_pending_query


class PendingControlsTests(unittest.TestCase):
    def test_recognizes_bounded_pending_queries(self):
        self.assertTrue(looks_like_pending_query("what's pending?"))
        self.assertTrue(looks_like_pending_query("what do I have pending"))
        self.assertFalse(looks_like_pending_query("why is the update pending?"))

    def test_aggregate_counts_and_nearest_item(self):
        response = handle_pending_controls(
            "what's pending?",
            alarm_rows=[
                {"kind": "timer", "label": "pasta", "_run_at_float": 160.0},
                {"kind": "reminder", "label": "call mom", "_run_at_float": 300.0},
            ],
            schedule_rows=[{"command": "turn off the porch light", "run_at": 500.0}],
            temporary_rows=[{"label": "Stair Light", "expires_at": 200.0}],
            now_fn=lambda: 100.0,
        )

        self.assertIn("1 timer", response)
        self.assertIn("1 reminder", response)
        self.assertIn("1 scheduled action", response)
        self.assertIn("1 temporary change", response)
        self.assertIn("Next is your pasta timer in 60 seconds", response)

    def test_empty_pending_summary_is_concise(self):
        self.assertEqual(
            handle_pending_controls(
                "show me my pending items",
                alarm_rows=[],
                schedule_rows=[],
                temporary_rows=[],
            ),
            "You don't have anything pending.",
        )


if __name__ == "__main__":
    unittest.main()
