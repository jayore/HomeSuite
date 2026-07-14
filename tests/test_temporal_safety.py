from __future__ import annotations

import unittest
from unittest import mock

import app_config
import confirmation_controls
import schedule_controls
import temporal_safety
from dialogue_state import reset_dialogue_state


class TemporalSafetyTests(unittest.TestCase):
    def tearDown(self):
        from request_context import clear_current_request_context

        clear_current_request_context()
        reset_dialogue_state(all_scopes=True)
        confirmation_controls.reset_confirmation_state()

    def test_unconsumed_timed_actions_fail_closed(self):
        for text in (
            "set the stair light to blue for 1 year",
            "in 1 year set the stair light to red",
            "set the stair light to red tomorrow",
            "set the stair light to red for one fortnight",
            "next Thursday set the stair light to red",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    temporal_safety.guard_unconsumed_temporal_action(text),
                    temporal_safety.TEMPORAL_SAFETY_RESPONSE,
                )

    def test_normal_immediate_action_and_media_title_are_not_blocked(self):
        self.assertIsNone(
            temporal_safety.guard_unconsumed_temporal_action("set the stair light to red")
        )
        self.assertIsNone(temporal_safety.guard_unconsumed_temporal_action("play Tomorrow"))

    def test_one_year_schedule_is_rejected_before_validation(self):
        validate = mock.Mock()
        response = schedule_controls.handle_schedule_controls(
            tl="in 1 year set the stair light to red",
            validate_command=validate,
        )

        self.assertIn("up to 30 days", response)
        validate.assert_not_called()

    def test_long_schedule_requires_confirmation_after_validation(self):
        validate = mock.Mock(return_value=(True, "validated", {"writes": []}))
        response = schedule_controls.handle_schedule_controls(
            tl="in 2 days turn off the porch light",
            validate_command=validate,
        )

        self.assertIn("Should I continue", response)
        validate.assert_called_once()

    def test_policy_override_can_enable_unlock_confirmation(self):
        with mock.patch.object(
            app_config,
            "COMMAND_CONFIRMATION_POLICY_OVERRIDES",
            {"unlock": {"enabled": True}},
        ):
            self.assertTrue(
                confirmation_controls.policy_requires_confirmation("unlock")
            )

    def test_policy_can_be_limited_to_specific_sources(self):
        from request_context import RequestContext, set_current_request_context

        with mock.patch.object(
            app_config,
            "COMMAND_CONFIRMATION_POLICY_OVERRIDES",
            {"unlock": {"enabled": True, "confirm_source_ids": ["telegram"]}},
        ):
            set_current_request_context(RequestContext(source_id="default_piphone"))
            self.assertFalse(confirmation_controls.policy_requires_confirmation("unlock"))
            set_current_request_context(RequestContext(source_id="telegram"))
            self.assertTrue(confirmation_controls.policy_requires_confirmation("unlock"))


if __name__ == "__main__":
    unittest.main()
