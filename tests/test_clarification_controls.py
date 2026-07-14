from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import clarification_controls
import dialogue_state
from clarification_controls import ClarificationOption
from request_context import (
    RequestContext,
    clear_current_request_context,
    set_current_request_context,
)


class ClarificationControlsTests(unittest.TestCase):
    def setUp(self):
        clear_current_request_context()
        dialogue_state.reset_dialogue_state(all_scopes=True)

    def tearDown(self):
        clear_current_request_context()
        dialogue_state.reset_dialogue_state(all_scopes=True)

    def _request(self):
        return clarification_controls.request_command_clarification(
            prompt="Which lamp: Floor Lamp or Desk Lamp?",
            original_command="turn off the lamp",
            options=(
                ClarificationOption(
                    label="Floor Lamp",
                    command="turn floor lamp off",
                    aliases=("floor",),
                ),
                ClarificationOption(
                    label="Desk Lamp",
                    command="turn desk lamp off",
                    aliases=("desk",),
                ),
            ),
        )

    def test_short_selection_replays_the_normal_command(self):
        self._request()
        seen = []
        response = clarification_controls.handle_clarification_controls(
            tl="the floor one",
            execute_command=lambda command: seen.append(command) or "Done.",
        )
        self.assertEqual(response, "Done.")
        self.assertEqual(seen, ["turn floor lamp off"])
        self.assertIsNone(clarification_controls.pending_clarification())

    def test_natural_correction_shell_can_select_an_option(self):
        self._request()
        seen = []
        response = clarification_controls.handle_clarification_controls(
            tl="No, I meant the desk lamp",
            execute_command=lambda command: seen.append(command) or "Done.",
        )
        self.assertEqual(response, "Done.")
        self.assertEqual(seen, ["turn desk lamp off"])

    def test_unrelated_complete_command_supersedes_the_prompt(self):
        self._request()
        response = clarification_controls.handle_clarification_controls(
            tl="what's the weather tomorrow",
            execute_command=lambda command: self.fail(command),
        )
        self.assertIsNone(response)
        self.assertIsNone(clarification_controls.pending_clarification())

    def test_unclear_short_selection_gets_one_concise_retry(self):
        self._request()
        response = clarification_controls.handle_clarification_controls(
            tl="the other one",
            execute_command=lambda command: self.fail(command),
        )
        self.assertEqual(response, "Which one: Floor Lamp or Desk Lamp?")
        self.assertIsNotNone(clarification_controls.pending_clarification())

    def test_clarifications_are_exact_source_scoped(self):
        set_current_request_context(RequestContext(source_id="telegram"))
        self._request()

        set_current_request_context(RequestContext(source_id="default_piphone"))
        self.assertIsNone(clarification_controls.pending_clarification())

        set_current_request_context(RequestContext(source_id="telegram"))
        self.assertIsNotNone(clarification_controls.pending_clarification())


if __name__ == "__main__":
    unittest.main()
