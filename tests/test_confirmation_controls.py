from __future__ import annotations

import unittest
from unittest import mock

import confirmation_controls
from dialogue_state import reset_dialogue_state
from request_context import RequestContext, clear_current_request_context, set_current_request_context


class ConfirmationControlsTests(unittest.TestCase):
    def setUp(self):
        clear_current_request_context()
        reset_dialogue_state(all_scopes=True)
        confirmation_controls.reset_confirmation_state()

    def tearDown(self):
        clear_current_request_context()
        reset_dialogue_state(all_scopes=True)
        confirmation_controls.reset_confirmation_state()

    def test_affirmative_replays_command_with_one_use_authorization(self):
        set_current_request_context(RequestContext(source_id="telegram"))
        confirmation_controls.request_command_confirmation(
            policy="test_policy",
            command="unlock the front door",
            prompt="Are you sure?",
        )
        observed = []

        def execute(command):
            observed.append(
                confirmation_controls.consume_command_authorization("test_policy", command)
            )
            observed.append(
                confirmation_controls.consume_command_authorization("test_policy", command)
            )
            return "Done."

        response = confirmation_controls.handle_confirmation_controls(
            tl="yes",
            execute_command=execute,
        )

        self.assertEqual(response, "Done.")
        self.assertEqual(observed, [True, False])
        self.assertIsNone(confirmation_controls.pending_confirmation())

    def test_confirmation_is_source_scoped(self):
        set_current_request_context(RequestContext(source_id="telegram"))
        confirmation_controls.request_command_confirmation(
            policy="test_policy",
            command="do something",
            prompt="Continue?",
        )

        set_current_request_context(RequestContext(source_id="default_piphone"))
        route = mock.Mock()
        self.assertIsNone(
            confirmation_controls.handle_confirmation_controls(
                tl="yes",
                execute_command=route,
            )
        )
        route.assert_not_called()

        set_current_request_context(RequestContext(source_id="telegram"))
        self.assertIsNotNone(confirmation_controls.pending_confirmation())

    def test_shared_continuity_group_does_not_share_approvals(self):
        set_current_request_context(RequestContext(source_id="menubar"))
        confirmation_controls.request_command_confirmation(
            policy="test_policy",
            command="do something",
            prompt="Continue?",
        )

        set_current_request_context(RequestContext(source_id="raycast"))
        self.assertIsNone(confirmation_controls.pending_confirmation())

        set_current_request_context(RequestContext(source_id="menubar"))
        self.assertIsNotNone(confirmation_controls.pending_confirmation())

    def test_negative_clears_without_executing(self):
        confirmation_controls.request_command_confirmation(
            policy="test_policy",
            command="do something",
            prompt="Continue?",
            cancel_response="Canceled safely.",
        )
        route = mock.Mock()

        response = confirmation_controls.handle_confirmation_controls(
            tl="no thanks",
            execute_command=route,
        )

        self.assertEqual(response, "Canceled safely.")
        route.assert_not_called()
        self.assertIsNone(confirmation_controls.pending_confirmation())

    def test_unrelated_command_supersedes_pending_confirmation(self):
        confirmation_controls.request_command_confirmation(
            policy="test_policy",
            command="do something",
            prompt="Continue?",
        )
        route = mock.Mock()

        self.assertIsNone(
            confirmation_controls.handle_confirmation_controls(
                tl="turn off the kitchen light",
                execute_command=route,
            )
        )
        self.assertIsNone(
            confirmation_controls.handle_confirmation_controls(
                tl="yes",
                execute_command=route,
            )
        )
        route.assert_not_called()

    def test_typed_confirmation_uses_registered_executor(self):
        confirmation_controls.request_typed_confirmation(
            policy="calendar_write",
            action_type="calendar_create",
            payload={"title": "Dentist"},
            prompt="Add Dentist?",
        )
        executor = mock.Mock(return_value="Added Dentist.")

        response = confirmation_controls.handle_confirmation_controls(
            tl="go ahead",
            execute_command=mock.Mock(),
            typed_executors={"calendar_create": executor},
        )

        self.assertEqual(response, "Added Dentist.")
        executor.assert_called_once_with({"title": "Dentist"})


if __name__ == "__main__":
    unittest.main()
