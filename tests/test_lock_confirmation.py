from __future__ import annotations

import unittest
from unittest import mock

import app_config
import confirmation_controls
from dialogue_state import reset_dialogue_state
from lock_controls import handle_lock_controls


class LockConfirmationTests(unittest.TestCase):
    def setUp(self):
        reset_dialogue_state(all_scopes=True)
        confirmation_controls.reset_confirmation_state()

    def tearDown(self):
        reset_dialogue_state(all_scopes=True)
        confirmation_controls.reset_confirmation_state()

    @staticmethod
    def _resolve(_target):
        return "lock.front_door", "lock"

    def test_unlock_can_use_generic_confirmation_policy(self):
        calls = []

        def invoke(command):
            return handle_lock_controls(
                tl=command,
                call_ha_service=lambda service, payload: calls.append((service, payload)) or True,
                maybe_say=lambda text: text,
                resolve_device_entity=self._resolve,
            )

        with mock.patch.object(
            app_config,
            "COMMAND_CONFIRMATION_POLICY_OVERRIDES",
            {"unlock": {"enabled": True}},
        ):
            first = invoke("unlock the front door")
            self.assertEqual(first, "Are you sure you want to unlock the front door?")
            self.assertEqual(calls, [])

            second = confirmation_controls.handle_confirmation_controls(
                tl="yes",
                execute_command=invoke,
            )

        self.assertEqual(second, "Unlocking front door.")
        self.assertEqual(calls, [("lock/unlock", {"entity_id": "lock.front_door"})])

    def test_unlock_remains_immediate_when_policy_is_disabled(self):
        calls = []
        response = handle_lock_controls(
            tl="unlock the front door",
            call_ha_service=lambda service, payload: calls.append((service, payload)) or True,
            maybe_say=lambda text: text,
            resolve_device_entity=self._resolve,
        )

        self.assertEqual(response, "Unlocking front door.")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
