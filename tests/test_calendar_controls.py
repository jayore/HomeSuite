from __future__ import annotations

import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import app_config
import calendar_controls
import confirmation_controls
from dialogue_state import reset_dialogue_state


PACIFIC = ZoneInfo("America/Los_Angeles")


class CalendarControlsTests(unittest.TestCase):
    def setUp(self):
        reset_dialogue_state(all_scopes=True)
        self.now = datetime(2026, 7, 13, 10, 0, tzinfo=PACIFIC)
        self.config = mock.patch.multiple(
            app_config,
            CALENDARS={
                "personal": {
                    "entity_id": "calendar.personal",
                    "label": "Personal",
                    "aliases": ["personal"],
                    "writable": True,
                    "include_in_agenda": True,
                },
                "family": {
                    "entity_id": "calendar.family",
                    "label": "Family",
                    "aliases": ["family"],
                    "writable": True,
                    "include_in_agenda": True,
                },
            },
            DEFAULT_CALENDAR="personal",
            CALENDAR_READS_ENABLED=True,
            CALENDAR_WRITES_ENABLED=True,
            CALENDAR_CONFIRM_WRITES=True,
            CALENDAR_DEFAULT_EVENT_DURATION_MINUTES=60,
            CALENDAR_DRAFT_TTL_SECONDS=120,
            CALENDAR_QUERY_MAX_EVENTS=6,
        )
        self.config.start()

    def tearDown(self):
        self.config.stop()
        reset_dialogue_state(all_scopes=True)

    def test_tomorrow_query_uses_bounded_ha_window(self):
        observed = {}

        def get_events(entity_ids, **kwargs):
            observed["entity_ids"] = entity_ids
            observed.update(kwargs)
            return {
                "calendar.personal": [
                    {
                        "summary": "Dentist",
                        "start": "2026-07-14T16:30:00-07:00",
                        "end": "2026-07-14T17:30:00-07:00",
                    }
                ],
                "calendar.family": [],
            }

        response = calendar_controls.handle_calendar_controls(
            tl="what's on my calendar tomorrow",
            get_events=get_events,
            call_service=mock.Mock(),
            mark_action=mock.Mock(),
            now=self.now,
        )

        self.assertIn("Dentist at 4:30 PM", response)
        self.assertEqual(
            observed["entity_ids"],
            ["calendar.personal", "calendar.family"],
        )
        self.assertTrue(observed["start_date_time"].startswith("2026-07-14T00:00:00"))
        self.assertTrue(observed["end_date_time"].startswith("2026-07-15T00:00:00"))

    def test_named_appointment_query_finds_next_match(self):
        response = calendar_controls.handle_calendar_controls(
            tl="when is my dentist appointment",
            get_events=lambda *_args, **_kwargs: {
                "calendar.personal": [
                    {
                        "summary": "Dentist appointment",
                        "start": "2026-07-20T16:30:00-07:00",
                    }
                ],
                "calendar.family": [],
            },
            call_service=mock.Mock(),
            mark_action=mock.Mock(),
            now=self.now,
        )

        self.assertIn("Monday at 4:30 PM", response)

    def test_next_event_language_is_recognized_without_calendar_word(self):
        query = calendar_controls.parse_calendar_query(
            "what is my next event",
            now=self.now,
        )

        self.assertTrue(calendar_controls.looks_like_calendar_request("what is my next event"))
        self.assertTrue(query.next_only)

    def test_complete_event_requires_confirmation_before_write(self):
        calls = []
        mark_action = mock.Mock()

        response = calendar_controls.handle_calendar_controls(
            tl="add dentist appointment to my calendar on July 20 at 4:30 pm",
            get_events=mock.Mock(),
            call_service=lambda service, payload: calls.append((service, payload)) or True,
            mark_action=mark_action,
            now=self.now,
        )
        self.assertEqual(
            response,
            "Add Dentist Appointment to Personal on Monday, July 20 at 4:30 PM for 1 hour?",
        )
        self.assertEqual(calls, [])

        response = confirmation_controls.handle_confirmation_controls(
            tl="yes",
            execute_command=mock.Mock(),
            typed_executors={
                "calendar_create": lambda payload: calendar_controls.execute_calendar_confirmation(
                    payload,
                    call_service=lambda service, body: calls.append((service, body)) or True,
                    mark_action=mark_action,
                )
            },
        )

        self.assertEqual(response, "Added Dentist Appointment to Personal on Monday at 4:30 PM.")
        self.assertEqual(calls[0][0], "calendar/create_event")
        self.assertEqual(calls[0][1]["entity_id"], "calendar.personal")
        self.assertEqual(calls[0][1]["summary"], "Dentist Appointment")
        self.assertEqual(calls[0][1]["start_date_time"], "2026-07-20T16:30:00-07:00")
        self.assertEqual(calls[0][1]["end_date_time"], "2026-07-20T17:30:00-07:00")
        mark_action.assert_called_once_with()

    def test_date_first_draft_collects_title_then_confirms(self):
        first = calendar_controls.handle_calendar_controls(
            tl="add an event to my calendar on July 20 at 4:30 pm",
            get_events=mock.Mock(),
            call_service=mock.Mock(),
            mark_action=mock.Mock(),
            now=self.now,
        )
        second = calendar_controls.handle_calendar_controls(
            tl="dentist appointment",
            get_events=mock.Mock(),
            call_service=mock.Mock(),
            mark_action=mock.Mock(),
            now=self.now,
        )

        self.assertEqual(first, "What should I call the event?")
        self.assertIn("Add Dentist Appointment to Personal", second)

    def test_name_first_draft_collects_date_and_time(self):
        first = calendar_controls.handle_calendar_controls(
            tl="add dentist appointment to my calendar",
            get_events=mock.Mock(),
            call_service=mock.Mock(),
            mark_action=mock.Mock(),
            now=self.now,
        )
        second = calendar_controls.handle_calendar_controls(
            tl="July 20 at 4:30 pm",
            get_events=mock.Mock(),
            call_service=mock.Mock(),
            mark_action=mock.Mock(),
            now=self.now,
        )

        self.assertEqual(first, "What day should I schedule Dentist Appointment?")
        self.assertIn("Monday, July 20 at 4:30 PM", second)

    def test_writes_disabled_fails_before_draft(self):
        with mock.patch.object(app_config, "CALENDAR_WRITES_ENABLED", False):
            response = calendar_controls.handle_calendar_controls(
                tl="add dentist appointment to my calendar on July 20 at 4:30 pm",
                get_events=mock.Mock(),
                call_service=mock.Mock(),
                mark_action=mock.Mock(),
                now=self.now,
            )

        self.assertIn("creation is disabled", response)

if __name__ == "__main__":
    unittest.main()
