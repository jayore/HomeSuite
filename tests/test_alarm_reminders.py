from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ReminderParserTests(unittest.TestCase):
    def _parse(self, text):
        import alarm_controls

        def prefs(name, default):
            return "local" if name == "ALARM_DEFAULT_OUTPUT" else default

        with mock.patch.object(alarm_controls, "_prefs", side_effect=prefs):
            return alarm_controls._parse_create_alarm(
                text,
                sonos_players={},
                default_sonos_room=None,
            )

    def test_action_first_relative_reminder(self):
        parsed = self._parse("remind me to check the laundry in 45 minutes")

        self.assertEqual(parsed["kind"], "reminder")
        self.assertEqual(parsed["label"], "check the laundry")
        self.assertEqual(parsed["phrase"], "in 45 minutes")
        self.assertEqual(parsed["delay_seconds"], 45 * 60)
        self.assertEqual(parsed["output"], {"mode": "local"})

    def test_time_first_absolute_reminder(self):
        parsed = self._parse("remind me tomorrow at 7 am to call mom")

        self.assertEqual(parsed["kind"], "reminder")
        self.assertEqual(parsed["label"], "call mom")
        self.assertRegex(parsed["phrase"].lower(), r"^tomorrow at 7(?::00)? am$")

    def test_alarm_accepts_clock_before_tomorrow_daypart(self):
        parsed = self._parse("set an alarm for 7 tomorrow morning")

        self.assertEqual(parsed["kind"], "alarm")
        self.assertRegex(parsed["phrase"].lower(), r"^tomorrow at 7(?::00)? am$")

    def test_wake_music_can_come_before_clock(self):
        parsed = self._parse("wake me up with music at 7 am")

        self.assertEqual(parsed["kind"], "alarm")
        self.assertEqual(parsed["label"], "wake up")
        self.assertEqual(parsed["music_command"], "play music")
        self.assertRegex(parsed["phrase"].lower(), r"^(?:tomorrow )?at 7(?::00)? am$")

    def test_an_article_does_not_leak_into_timer_label(self):
        parsed = self._parse("set an audit timer for 10 minutes")

        self.assertEqual(parsed["kind"], "timer")
        self.assertEqual(parsed["label"], "audit")


class ReminderLifecycleTests(unittest.TestCase):
    def test_reminder_fires_as_spoken_reminder(self):
        import alarm_controls

        self.assertEqual(
            alarm_controls._alarm_message(
                {"kind": "reminder", "label": "check the laundry"}
            ),
            "Reminder: check the laundry.",
        )

    def test_reminder_fire_is_voice_only_by_default(self):
        import alarm_controls

        row = {
            "id": "reminder-1",
            "kind": "reminder",
            "label": "check the laundry",
            "status": "pending",
            "output": {"mode": "local"},
        }

        def prefs(name, default):
            values = {
                "ALARM_SOUND_ENABLED": True,
                "ALARM_VOICE_ENABLED": True,
                "REMINDER_SOUND_ENABLED": False,
                "REMINDER_VOICE_ENABLED": True,
            }
            return values.get(name, default)

        with (
            mock.patch.object(alarm_controls, "_load_alarms", return_value=[row]),
            mock.patch.object(alarm_controls, "_update_alarm"),
            mock.patch.object(alarm_controls, "_prefs", side_effect=prefs),
            mock.patch.object(alarm_controls, "_resolve_sound_path") as sound,
            mock.patch.object(alarm_controls, "_play_local_file") as play,
            mock.patch.object(alarm_controls, "_speak_local", return_value=True) as speak,
        ):
            result = alarm_controls._fire_alarm("reminder-1")

        self.assertEqual(result, "")
        sound.assert_not_called()
        play.assert_not_called()
        speak.assert_called_once_with("Reminder: check the laundry.")

    def test_reminder_list_and_cancel_language_is_claimed(self):
        import alarm_controls

        self.assertEqual(
            alarm_controls._looks_like_alarm_list_request("what reminders are set"),
            "reminder",
        )
        self.assertEqual(
            alarm_controls._looks_like_alarm_cancel_request("cancel my reminder"),
            (True, "reminder", False),
        )
        self.assertEqual(
            alarm_controls._looks_like_alarm_cancel_request("cancel all reminders"),
            (True, "reminder", True),
        )

    def test_reminder_summary_is_named_and_timed(self):
        import alarm_controls

        row = {
            "kind": "reminder",
            "label": "check the laundry",
            "run_at": 2_000_000_000.0,
            "status": "pending",
        }
        with mock.patch.object(alarm_controls, "_active_alarms", return_value=[row]):
            result = alarm_controls._list_alarms_response(kind="reminder")

        self.assertIn("one reminder to check the laundry", result.lower())

    def test_dry_run_reminder_confirms_without_persistence(self):
        import alarm_controls

        def prefs(name, default):
            return "local" if name == "ALARM_DEFAULT_OUTPUT" else default

        with (
            mock.patch.dict(os.environ, {"PIPHONE_TEST_MODE": "1"}, clear=True),
            mock.patch.object(alarm_controls, "_prefs", side_effect=prefs),
            mock.patch.object(alarm_controls, "_save_new_alarm") as save,
            mock.patch.object(alarm_controls, "_schedule_alarm_fire") as schedule,
        ):
            result = alarm_controls.handle_alarm_controls(
                tl="remind me to check the laundry in 45 minutes"
            )

        self.assertEqual(
            result,
            "I'll remind you to check the laundry in 45 minutes.",
        )
        save.assert_not_called()
        schedule.assert_not_called()


if __name__ == "__main__":
    unittest.main()
