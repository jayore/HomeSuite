from __future__ import annotations

import unittest
from unittest import mock


class DialogueStateTests(unittest.TestCase):
    def setUp(self):
        import dialogue_state
        from request_context import clear_current_request_context

        clear_current_request_context()
        dialogue_state.reset_dialogue_state(all_scopes=True)

    def tearDown(self):
        import dialogue_state
        from request_context import clear_current_request_context

        clear_current_request_context()
        dialogue_state.reset_dialogue_state(all_scopes=True)

    def test_referents_are_source_scoped(self):
        import dialogue_state
        from request_context import RequestContext, set_current_request_context

        set_current_request_context(RequestContext(source_id="telegram"))
        dialogue_state.remember_referent(
            "timer",
            "timer-1",
            capabilities={"adjust_duration"},
        )

        set_current_request_context(RequestContext(source_id="default_piphone"))
        self.assertIsNone(
            dialogue_state.resolve_referent(capability="adjust_duration")
        )

        set_current_request_context(RequestContext(source_id="telegram"))
        self.assertEqual(
            dialogue_state.resolve_referent(capability="adjust_duration")["key"],
            "timer-1",
        )

    def test_configured_device_group_shares_a_context_bubble(self):
        import dialogue_state
        from request_context import RequestContext, set_current_request_context

        set_current_request_context(RequestContext(source_id="menubar"))
        dialogue_state.remember_referent("location", "Tokyo", capabilities={"location"})

        set_current_request_context(RequestContext(source_id="raycast"))
        self.assertEqual(
            dialogue_state.resolve_referent(kinds={"location"})["key"],
            "Tokyo",
        )

    def test_resolution_filters_by_capability_not_only_recency(self):
        import dialogue_state

        with mock.patch.object(dialogue_state.time, "time", return_value=100.0) as now:
            dialogue_state.remember_referent(
                "timer",
                "timer-1",
                capabilities={"adjust_duration", "cancel_schedule"},
            )
            now.return_value = 101.0
            dialogue_state.remember_referent(
                "music",
                "track-1",
                capabilities={"play_media"},
            )
            now.return_value = 102.0
            resolved = dialogue_state.resolve_referent(capability="adjust_duration")

        self.assertEqual(resolved["kind"], "timer")
        self.assertEqual(resolved["key"], "timer-1")

    def test_expired_referent_is_not_returned(self):
        import dialogue_state

        with mock.patch.object(dialogue_state.time, "time", return_value=100.0) as now:
            dialogue_state.remember_referent("timer", "timer-1", ttl_seconds=5)
            now.return_value = 106.0
            self.assertIsNone(dialogue_state.resolve_referent(kinds={"timer"}))

    def test_snapshot_restore_prevents_dry_run_state_leaks(self):
        import dialogue_state

        dialogue_state.remember_referent("light", "light.stair")
        before = dialogue_state.snapshot_scope()
        dialogue_state.remember_referent("light", "light.desk")
        dialogue_state.restore_scope(before)

        self.assertEqual(
            dialogue_state.resolve_referent(kinds={"light"})["key"],
            "light.stair",
        )

    def test_typed_intent_frames_are_source_scoped_and_round_trip(self):
        import dialogue_state
        from conversational_nl import IntentFrame
        from request_context import RequestContext, set_current_request_context

        set_current_request_context(RequestContext(source_id="telegram"))
        dialogue_state.remember_intent_frame(
            IntentFrame(
                domain="light",
                intent="set_color",
                canonical_command="set stair light to red",
                slots={"target": "stair light", "value": "red"},
                target_keys=("light.stair",),
                followups=frozenset({"target_transfer", "value_correction"}),
            )
        )

        resolved = dialogue_state.resolve_intent_frame(
            required_followup="target_transfer"
        )
        self.assertEqual(resolved.slots["value"], "red")
        self.assertEqual(resolved.target_keys, ("light.stair",))

        set_current_request_context(RequestContext(source_id="default_piphone"))
        self.assertIsNone(dialogue_state.resolve_intent_frame())

    def test_forget_referents_can_clear_only_pending_interactions(self):
        import dialogue_state

        dialogue_state.remember_referent(
            "calendar_draft",
            "draft-1",
            capabilities={"calendar_create", "pending_interaction"},
        )
        dialogue_state.remember_referent(
            "light",
            "light.stair",
            capabilities={"binary_action"},
        )

        removed = dialogue_state.forget_referents(capability="pending_interaction")

        self.assertEqual(removed, 1)
        self.assertIsNone(dialogue_state.resolve_referent(kinds={"calendar_draft"}))
        self.assertEqual(
            dialogue_state.resolve_referent(kinds={"light"})["key"],
            "light.stair",
        )

    def test_ai_history_uses_the_same_source_scope(self):
        import interaction_flow
        from request_context import RequestContext, set_current_request_context

        interaction_flow.reset_history(all_scopes=True)
        set_current_request_context(RequestContext(source_id="telegram"))
        interaction_flow.inject_into_history(
            "What's the weather in Tokyo?",
            "Tokyo is clear and currently 72 degrees.",
        )

        set_current_request_context(RequestContext(source_id="default_piphone"))
        local_history = interaction_flow.get_history_snapshot()
        self.assertEqual(len(local_history), 1)

        set_current_request_context(RequestContext(source_id="telegram"))
        telegram_history = interaction_flow.get_history_snapshot()
        self.assertEqual(len(telegram_history), 3)
        self.assertEqual(telegram_history[-1]["role"], "assistant")

    def test_ai_media_breadcrumbs_are_source_scoped(self):
        import media_referents
        from request_context import RequestContext, set_current_request_context

        set_current_request_context(RequestContext(source_id="telegram"))
        self.assertTrue(
            media_referents.remember_video(
                kind="movie",
                title="The Matrix",
                confidence=0.9,
            )
        )
        self.assertEqual(
            media_referents.snapshot()["video"]["title"],
            "The Matrix",
        )

        set_current_request_context(RequestContext(source_id="default_piphone"))
        self.assertIsNone(media_referents.snapshot()["video"])


if __name__ == "__main__":
    unittest.main()
