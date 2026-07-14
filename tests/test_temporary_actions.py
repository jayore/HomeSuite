from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import temporary_actions
import confirmation_controls
from dialogue_state import reset_dialogue_state


def _light_state(
    *,
    state="on",
    brightness=150,
    hs_color=(0.0, 100.0),
    name="Stair Light",
):
    attrs = {
        "friendly_name": name,
        "brightness": brightness,
        "color_mode": "hs",
        "hs_color": list(hs_color),
    }
    return {"entity_id": "light.stair_light", "state": state, "attributes": attrs}


class TemporaryActionParserTests(unittest.TestCase):
    def test_parses_spoken_duration_suffix(self):
        parsed = temporary_actions.parse_temporary_action(
            "set the stair light to red for ten minutes"
        )

        self.assertEqual(parsed.command, "set the stair light to red")
        self.assertEqual(parsed.duration_seconds, 600)

    def test_does_not_claim_non_action_language(self):
        self.assertIsNone(
            temporary_actions.parse_temporary_action("weather for ten minutes")
        )

    def test_parses_extended_units_for_bounds_enforcement(self):
        day = temporary_actions.parse_temporary_action(
            "set the stair light to red for one day"
        )
        year = temporary_actions.parse_temporary_action(
            "set the stair light to red for one year"
        )

        self.assertEqual(day.duration_seconds, 86400)
        self.assertEqual(year.duration_seconds, 365 * 86400)

    def test_management_language_is_classified_for_deterministic_routing(self):
        for text in (
            "what temporary changes are active",
            "how long until the stair light restores",
            "is the stair light temporary",
            "restore the stair light now",
            "keep the stair light as it is",
        ):
            with self.subTest(text=text):
                self.assertTrue(
                    temporary_actions.looks_like_temporary_action_request(text)
                )


class TemporaryActionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "temporary_actions.json"
        self.now = 100.0
        self.current = _light_state(hs_color=(0.0, 100.0))
        self.calls = []
        self.store = temporary_actions.TemporaryActionStore(
            self.path,
            get_state=lambda _entity_id: self.current,
            call_service=lambda service, payload: self.calls.append((service, payload)) or True,
            now_fn=lambda: self.now,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_restores_original_state_when_temporary_state_is_unchanged(self):
        original = _light_state(brightness=120, hs_color=(30.0, 20.0))
        self.store.begin(
            entity_id="light.stair_light",
            label="Stair Light",
            original_state=original,
            duration_seconds=10,
            observe_delay_seconds=0,
        )

        self.current = _light_state(brightness=200, hs_color=(0.0, 100.0))
        self.store.tick(now_ts=100)
        self.store.tick(now_ts=111)

        self.assertEqual(
            self.calls,
            [
                (
                    "light/turn_on",
                    {
                        "entity_id": "light.stair_light",
                        "brightness": 120,
                        "hs_color": [30.0, 20.0],
                    },
                )
            ],
        )
        self.assertEqual(self.store.list_overrides(), [])

    def test_manual_change_wins_and_restore_is_discarded(self):
        self.store.begin(
            entity_id="light.stair_light",
            label="Stair Light",
            original_state=_light_state(hs_color=(30.0, 20.0)),
            duration_seconds=10,
            observe_delay_seconds=0,
        )
        self.current = _light_state(hs_color=(0.0, 100.0))
        self.store.tick(now_ts=100)
        self.current = _light_state(hs_color=(240.0, 100.0))
        self.store.tick(now_ts=111)

        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.list_overrides(), [])

    def test_observation_waits_for_ha_state_to_reflect_the_write(self):
        original = _light_state(brightness=120, hs_color=(30.0, 20.0))
        self.current = original
        self.store.begin(
            entity_id="light.stair_light",
            label="Stair Light",
            original_state=original,
            duration_seconds=10,
            observe_delay_seconds=0,
            observe_timeout_seconds=5,
        )

        self.store.tick(now_ts=101)
        self.assertIsNone(self.store.list_overrides()[0]["applied_signature"])

        self.current = _light_state(brightness=200, hs_color=(0.0, 100.0))
        self.store.tick(now_ts=102)
        self.assertIsNotNone(self.store.list_overrides()[0]["applied_signature"])

    def test_restart_can_reload_pending_override(self):
        self.store.begin(
            entity_id="light.stair_light",
            label="Stair Light",
            original_state=_light_state(),
            duration_seconds=10,
            observe_delay_seconds=0,
        )

        reloaded = temporary_actions.TemporaryActionStore(self.path)
        self.assertEqual(len(reloaded.list_overrides()), 1)

    def test_new_temporary_action_preserves_first_baseline(self):
        original = _light_state(brightness=100, hs_color=(30.0, 20.0))
        first = self.store.begin(
            entity_id="light.stair_light",
            label="Stair Light",
            original_state=original,
            duration_seconds=20,
            observe_delay_seconds=0,
        )
        self.now = 105
        second = self.store.begin(
            entity_id="light.stair_light",
            label="Stair Light",
            original_state=_light_state(brightness=220, hs_color=(0.0, 100.0)),
            duration_seconds=20,
            observe_delay_seconds=0,
        )

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["original"]["attributes"]["brightness"], 100)
        self.assertEqual(len(self.store.list_overrides()), 1)


class TemporaryActionHandlerTests(unittest.TestCase):
    def setUp(self):
        reset_dialogue_state(all_scopes=True)
        confirmation_controls.reset_confirmation_state()

    def tearDown(self):
        reset_dialogue_state(all_scopes=True)
        confirmation_controls.reset_confirmation_state()

    def test_handler_reuses_resolved_light_write(self):
        calls = []
        response = temporary_actions.handle_temporary_action(
            tl="set the stair light to red for ten minutes",
            preview_command=lambda _command: (
                True,
                "validated",
                {
                    "writes": [
                        {
                            "service": "light/turn_on",
                            "data": {
                                "entity_id": "light.stair_light",
                                "color_name": "red",
                            },
                        }
                    ]
                },
            ),
            get_state=lambda _entity_id: _light_state(),
            call_service=lambda service, payload: calls.append((service, payload)) or True,
            mark_action=mock.Mock(),
            remember_light=mock.Mock(),
            effects_are_live=False,
        )

        self.assertIn("restore the Stair Light in 10 minutes", response)
        self.assertEqual(
            calls,
            [
                (
                    "light/turn_on",
                    {"entity_id": "light.stair_light", "color_name": "red"},
                )
            ],
        )

    def test_handler_rejects_multi_light_preview(self):
        call_service = mock.Mock()
        response = temporary_actions.handle_temporary_action(
            tl="turn off the lights for ten minutes",
            preview_command=lambda _command: (
                True,
                "validated",
                {
                    "writes": [
                        {
                            "service": "light/turn_off",
                            "data": {"entity_id": ["light.one", "light.two"]},
                        }
                    ]
                },
            ),
            get_state=mock.Mock(),
            call_service=call_service,
            mark_action=mock.Mock(),
            remember_light=mock.Mock(),
            effects_are_live=False,
        )

        self.assertIn("one light at a time", response)
        call_service.assert_not_called()

    def test_excessive_duration_fails_before_preview_or_write(self):
        preview = mock.Mock()
        call_service = mock.Mock()

        response = temporary_actions.handle_temporary_action(
            tl="set the stair light to blue for one year",
            preview_command=preview,
            get_state=mock.Mock(),
            call_service=call_service,
            mark_action=mock.Mock(),
            remember_light=mock.Mock(),
            effects_are_live=False,
        )

        self.assertIn("up to 1 day", response)
        preview.assert_not_called()
        call_service.assert_not_called()

    def test_long_change_replays_only_after_confirmation(self):
        calls = []

        def invoke(command):
            return temporary_actions.handle_temporary_action(
                tl=command,
                preview_command=lambda _command: (
                    True,
                    "validated",
                    {
                        "writes": [
                            {
                                "service": "light/turn_on",
                                "data": {
                                    "entity_id": "light.stair_light",
                                    "color_name": "blue",
                                },
                            }
                        ]
                    },
                ),
                get_state=lambda _entity_id: _light_state(),
                call_service=lambda service, payload: calls.append((service, payload)) or True,
                mark_action=mock.Mock(),
                remember_light=mock.Mock(),
                effects_are_live=False,
            )

        first = invoke("set the stair light to blue for eight hours")
        self.assertIn("Should I apply it", first)
        self.assertEqual(calls, [])

        second = confirmation_controls.handle_confirmation_controls(
            tl="yes",
            execute_command=invoke,
        )

        self.assertIn("restore the Stair Light in 8 hours", second)
        self.assertEqual(len(calls), 1)


class TemporaryActionManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "temporary_actions.json"
        self.now = 100.0
        self.calls = []
        self.store = temporary_actions.TemporaryActionStore(
            self.path,
            call_service=lambda service, payload: self.calls.append((service, payload)) or True,
            now_fn=lambda: self.now,
        )
        self.row = self.store.begin(
            entity_id="light.stair_light",
            label="Stair Light",
            original_state=_light_state(brightness=100, hs_color=(30.0, 20.0)),
            duration_seconds=600,
            observe_delay_seconds=1,
        )
        self.store_patch = mock.patch.object(temporary_actions, "_STORE", self.store)
        self.store_patch.start()

    def tearDown(self):
        self.store_patch.stop()
        self.temp_dir.cleanup()

    def _handle(self, text):
        return temporary_actions.handle_temporary_action(
            tl=text,
            preview_command=mock.Mock(),
            get_state=mock.Mock(),
            call_service=lambda service, payload: self.calls.append((service, payload)) or True,
            mark_action=mock.Mock(),
            remember_light=mock.Mock(),
            effects_are_live=True,
        )

    def test_lists_and_queries_active_override(self):
        listed = self._handle("what temporary changes are active")
        remaining = self._handle("how long until the stair light restores")
        status = self._handle("is the stair light temporary")

        self.assertIn("one temporary change", listed)
        self.assertIn("10 minutes", listed)
        self.assertIn("restores in 10 minutes", remaining)
        self.assertIn("Yes", status)

    def test_restore_now_restores_baseline_and_removes_override(self):
        response = self._handle("restore the stair light now")

        self.assertEqual(response, "Restored the Stair Light.")
        self.assertEqual(self.store.list_overrides(), [])
        self.assertEqual(self.calls[0][0], "light/turn_on")
        self.assertEqual(self.calls[0][1]["brightness"], 100)

    def test_keep_current_removes_restore_without_writing(self):
        response = self._handle("keep the stair light as it is")

        self.assertEqual(response, "Okay. I'll keep the Stair Light as it is.")
        self.assertEqual(self.store.list_overrides(), [])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
