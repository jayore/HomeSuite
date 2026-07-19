from __future__ import annotations

from contextlib import contextmanager, ExitStack
import os
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dialogue_state
import app_config
from request_context import clear_current_request_context
from semantic_router import RouteOutcome, route_utterance


class ConversationalDispatchTests(unittest.TestCase):
    def setUp(self):
        clear_current_request_context()
        dialogue_state.reset_dialogue_state(all_scopes=True)
        import command_dispatch

        command_dispatch.reset_dispatch_state()

    def tearDown(self):
        clear_current_request_context()
        dialogue_state.reset_dialogue_state(all_scopes=True)

    @contextmanager
    def _dispatch(self, states):
        import command_dispatch

        service = mock.Mock(return_value=True)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {"PIPHONE_LIVE": "1"}, clear=False))
            stack.enter_context(mock.patch.object(command_dispatch, "ha_get_states", return_value=states))
            stack.enter_context(mock.patch.object(command_dispatch, "call_ha_service", service))
            stack.enter_context(mock.patch.object(command_dispatch, "handle_temporary_action", return_value=None))
            stack.enter_context(mock.patch.object(command_dispatch, "handle_schedule_controls", return_value=None))
            stack.enter_context(mock.patch.object(command_dispatch, "handle_stock_quote_query", return_value=None))
            stack.enter_context(mock.patch.object(command_dispatch, "try_run_runnable_from_text", return_value=None))
            yield command_dispatch, service

    def test_router_uses_the_same_conversational_shell_without_stealing_chat(self):
        device_phrases = (
            "Could you turn the floor lamp off for me?",
            "Would you mind turning off the floor lamp?",
            "Give me five minutes",
        )
        for phrase in device_phrases:
            with self.subTest(phrase=phrase):
                self.assertEqual(route_utterance(text=phrase).outcome, RouteOutcome.DEVICE)

        self.assertEqual(
            route_utterance(text="Could you explain photosynthesis?").outcome,
            RouteOutcome.CHATGPT,
        )

    def test_router_keeps_voice_fragments_bounded_by_ai_recency(self):
        import semantic_router

        window = float(semantic_router.CHATGPT_CONTINUATION_WINDOW_SECONDS)

        self.assertEqual(
            route_utterance(text="Neo", source_type="wakeword").outcome,
            RouteOutcome.ERROR,
        )
        self.assertEqual(
            route_utterance(
                text="Neo",
                source_type="wakeword",
                now_ts=100.0 + window,
                last_chatgpt_ts=100.0,
            ).outcome,
            RouteOutcome.CHATGPT,
        )
        self.assertEqual(
            route_utterance(
                text="Neo",
                source_type="wakeword",
                now_ts=100.001 + window,
                last_chatgpt_ts=100.0,
            ).outcome,
            RouteOutcome.ERROR,
        )

    def test_router_rejects_voice_debris_and_protects_unknown_actions(self):
        for phrase in ("um", "uh", "and the"):
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    route_utterance(text=phrase, source_type="ptt").outcome,
                    RouteOutcome.ERROR,
                )

        self.assertEqual(
            route_utterance(text="activate the mystery lamp").outcome,
            RouteOutcome.DEVICE,
        )

    def test_polite_binary_command_then_target_transfer(self):
        states = [
            {"entity_id": "light.floor_lamp", "state": "on", "attributes": {"friendly_name": "Floor Lamp"}},
            {"entity_id": "light.desk_lamp", "state": "on", "attributes": {"friendly_name": "Desk Lamp"}},
        ]
        with self._dispatch(states) as (dispatch, service):
            first = dispatch.process_device_commands(
                "Could you turn the floor lamp off for me?"
            )
            second = dispatch.process_device_commands("and the desk lamp too")

        self.assertEqual(first, "")
        self.assertEqual(second, "")
        self.assertEqual(
            service.call_args_list,
            [
                mock.call("light/turn_off", {"entity_id": "light.floor_lamp"}),
                mock.call("light/turn_off", {"entity_id": "light.desk_lamp"}),
            ],
        )

    def test_color_correction_and_transfer_reenter_normal_handlers(self):
        states = [
            {"entity_id": "light.stair", "state": "on", "attributes": {"friendly_name": "Stair Light"}},
            {"entity_id": "light.desk_lamp", "state": "on", "attributes": {"friendly_name": "Desk Lamp"}},
        ]
        with self._dispatch(states) as (dispatch, service):
            dispatch.process_device_commands("make the stair light red")
            dispatch.process_device_commands("actually, make it blue")
            dispatch.process_device_commands("and the desk lamp too")
            dispatch.process_device_commands("and the stair light?")

        self.assertEqual(
            service.call_args_list,
            [
                mock.call("light/turn_on", {"entity_id": "light.stair", "color_name": "red"}),
                mock.call("light/turn_on", {"entity_id": "light.stair", "color_name": "blue"}),
                mock.call("light/turn_on", {"entity_id": "light.desk_lamp", "color_name": "blue"}),
                mock.call("light/turn_on", {"entity_id": "light.stair", "color_name": "blue"}),
            ],
        )

    def test_bare_also_too_and_now_transfer_the_recent_light_action(self):
        states = [
            {"entity_id": "light.stair", "state": "on", "attributes": {"friendly_name": "Stair Light"}},
            {"entity_id": "light.side_lamp", "state": "on", "attributes": {"friendly_name": "Side Lamp"}},
        ]
        with self._dispatch(states) as (dispatch, service):
            dispatch.process_device_commands("set stair light to red")
            dispatch.process_device_commands("side lamp too")
            dispatch.process_device_commands("stair light also")
            dispatch.process_device_commands("now the side lamp")

        self.assertEqual(
            service.call_args_list,
            [
                mock.call("light/turn_on", {"entity_id": "light.stair", "color_name": "red"}),
                mock.call("light/turn_on", {"entity_id": "light.side_lamp", "color_name": "red"}),
                mock.call("light/turn_on", {"entity_id": "light.stair", "color_name": "red"}),
                mock.call("light/turn_on", {"entity_id": "light.side_lamp", "color_name": "red"}),
            ],
        )

    def test_direct_multi_target_light_commands_reach_each_device(self):
        states = [
            {"entity_id": "light.stair", "state": "on", "attributes": {"friendly_name": "Stair Light"}},
            {"entity_id": "light.side_lamp", "state": "on", "attributes": {"friendly_name": "Side Lamp"}},
        ]
        with self._dispatch(states) as (dispatch, service):
            dispatch.process_device_commands("turn off stair light and side lamp")
            dispatch.process_device_commands("set stair light and side lamp to red")
            dispatch.process_device_commands("set stair light and side lamp to 30%")

        self.assertEqual(
            service.call_args_list,
            [
                mock.call("light/turn_off", {"entity_id": "light.stair"}),
                mock.call("light/turn_off", {"entity_id": "light.side_lamp"}),
                mock.call("light/turn_on", {"entity_id": "light.stair", "color_name": "red"}),
                mock.call("light/turn_on", {"entity_id": "light.side_lamp", "color_name": "red"}),
                mock.call("light/turn_on", {"entity_id": "light.stair", "brightness_pct": 30}),
                mock.call("light/turn_on", {"entity_id": "light.side_lamp", "brightness_pct": 30}),
            ],
        )

    def test_now_color_value_updates_every_target_from_the_prior_action(self):
        states = [
            {"entity_id": "light.stair", "state": "on", "attributes": {"friendly_name": "Stair Light"}},
            {"entity_id": "light.side_lamp", "state": "on", "attributes": {"friendly_name": "Side Lamp"}},
        ]
        with self._dispatch(states) as (dispatch, service):
            dispatch.process_device_commands(
                "set side lamp and stair light to orange"
            )
            dispatch.process_device_commands("now white")

        self.assertEqual(
            service.call_args_list,
            [
                mock.call("light/turn_on", {"entity_id": "light.side_lamp", "color_name": "orange"}),
                mock.call("light/turn_on", {"entity_id": "light.stair", "color_name": "orange"}),
                mock.call("light/turn_on", {"entity_id": "light.side_lamp", "color_name": "white"}),
                mock.call("light/turn_on", {"entity_id": "light.stair", "color_name": "white"}),
            ],
        )

    def test_more_repeats_recent_volume_direction_through_normal_handler(self):
        states = [
            {
                "entity_id": "number.living_room_volume",
                "state": "50",
                "attributes": {"friendly_name": "Living Room Volume"},
            },
        ]
        with self._dispatch(states) as (dispatch, service):
            first = dispatch.process_device_commands("volume down")
            second = dispatch.process_device_commands("more")

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(
            service.call_args_list,
            [
                mock.call(
                    "number/set_value",
                    {"entity_id": "number.living_room_volume", "value": 45},
                ),
                mock.call(
                    "number/set_value",
                    {"entity_id": "number.living_room_volume", "value": 45},
                ),
            ],
        )

    def test_more_volume_confirmation_describes_effective_command(self):
        from interaction_flow import handle_text_interaction

        states = [
            {
                "entity_id": "number.living_room_volume",
                "state": "50",
                "attributes": {"friendly_name": "Living Room Volume"},
            },
        ]
        with self._dispatch(states) as (dispatch, service):
            def mark_volume_action(*_args, **_kwargs):
                dispatch._ACTION_OCCURRED = True
                return True

            service.side_effect = mark_volume_action
            first = handle_text_interaction(dispatch, "volume down")
            second = handle_text_interaction(dispatch, "more")

        self.assertEqual(first.response_text, "Decreased volume.")
        self.assertEqual(second.response_text, "Decreased volume.")
        self.assertEqual(second.source, "device_confirm")

    def test_more_repeats_recent_brightness_direction_through_normal_handler(self):
        states = [
            {
                "entity_id": "light.living_room_brightness",
                "state": "on",
                "attributes": {"friendly_name": "Living Room Brightness"},
            },
        ]
        with self._dispatch(states) as (dispatch, service):
            first = dispatch.process_device_commands("brightness down")
            second = dispatch.process_device_commands("more")

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(
            service.call_args_list,
            [
                mock.call(
                    "light/turn_on",
                    {
                        "entity_id": "light.living_room_brightness",
                        "brightness_step_pct": -10,
                    },
                ),
                mock.call(
                    "light/turn_on",
                    {
                        "entity_id": "light.living_room_brightness",
                        "brightness_step_pct": -10,
                    },
                ),
            ],
        )

    def test_more_brightness_confirmation_describes_effective_command(self):
        from interaction_flow import handle_text_interaction

        states = [
            {
                "entity_id": "light.living_room_brightness",
                "state": "on",
                "attributes": {"friendly_name": "Living Room Brightness"},
            },
        ]
        with self._dispatch(states) as (dispatch, service):
            def mark_brightness_action(*_args, **_kwargs):
                dispatch._ACTION_OCCURRED = True
                return True

            service.side_effect = mark_brightness_action
            first = handle_text_interaction(dispatch, "brightness down")
            second = handle_text_interaction(dispatch, "more")

        self.assertEqual(first.response_text, "Decreased brightness.")
        self.assertEqual(second.response_text, "Decreased brightness.")
        self.assertEqual(second.source, "device_confirm")

    def test_weather_followup_reuses_location_and_changes_day(self):
        seen = []

        def weather_response(location, *, query, states_snapshot):
            seen.append((location, query))
            return "Weather answer."

        with self._dispatch([]) as (dispatch, _service), mock.patch.object(
            dispatch,
            "handle_weather_query",
            side_effect=weather_response,
        ):
            dispatch.process_device_commands("what's the weather tomorrow in Seattle?")
            dispatch.process_device_commands("what about Thursday?")

        self.assertEqual(seen[0][0].lower(), "seattle")
        self.assertEqual(seen[0][1].day_offset, 1)
        self.assertEqual(seen[1][0].lower(), "seattle")
        self.assertEqual(seen[1][1].weekday, 3)

    def test_astronomy_followup_preserves_the_prior_event(self):
        seen = []

        def astronomy_response(text, **_kwargs):
            seen.append(text)
            return "Astronomy answer."

        with self._dispatch([]) as (dispatch, _service), mock.patch.object(
            dispatch,
            "handle_astronomy_query",
            side_effect=astronomy_response,
        ):
            dispatch.process_device_commands("when does Jupiter rise?")
            dispatch.process_device_commands("what about Saturn?")

        self.assertEqual(seen, ["when does jupiter rise", "when does saturn rise"])

    def test_calendar_followup_stays_in_the_calendar_domain(self):
        seen = []

        def calendar_response(*, tl, **_kwargs):
            seen.append(tl)
            return "Calendar answer."

        with self._dispatch([]) as (dispatch, _service), mock.patch.object(
            dispatch,
            "handle_calendar_controls",
            side_effect=calendar_response,
        ):
            dispatch.process_device_commands("what's on my calendar today?")
            dispatch.process_device_commands("and tomorrow?")

        self.assertEqual(
            seen,
            ["what's on my calendar today", "what's on my calendar tomorrow"],
        )

    def test_calendar_confirmation_can_be_revised_through_dispatch(self):
        calendar_config = mock.patch.multiple(
            app_config,
            CALENDARS={
                "personal": {
                    "entity_id": "calendar.personal",
                    "label": "Personal",
                    "writable": True,
                    "include_in_agenda": True,
                },
            },
            DEFAULT_CALENDAR="personal",
            CALENDAR_WRITES_ENABLED=True,
            CALENDAR_CONFIRM_WRITES=True,
            CALENDAR_DEFAULT_EVENT_DURATION_MINUTES=60,
            CALENDAR_DRAFT_TTL_SECONDS=120,
        )
        with calendar_config, self._dispatch([]) as (dispatch, service):
            prompt = dispatch.process_device_commands(
                "add dentist appointment to my calendar tomorrow at 4:30 pm"
            )
            rejection = dispatch.process_device_commands("no")
            revised = dispatch.process_device_commands("at 10:45 pm")
            result = dispatch.process_device_commands("yes")

        self.assertTrue(prompt.endswith("Is that right?"))
        self.assertEqual(
            rejection,
            "What should I change: the title, day, time, duration, or calendar?",
        )
        self.assertIn("10:45 PM", revised)
        self.assertIn("Added Dentist Appointment", result)
        service.assert_called_once()
        self.assertEqual(service.call_args.args[0], "calendar/create_event")
        self.assertEqual(service.call_args.args[1]["summary"], "Dentist Appointment")
        self.assertIn("T22:45:00", service.call_args.args[1]["start_date_time"])

    def test_timer_correction_reaches_existing_timer_edit_contract(self):
        seen = []

        def alarms(*, tl, **_kwargs):
            seen.append(tl)
            if tl == "set a timer for five minutes":
                dialogue_state.remember_referent(
                    "timer",
                    "timer-1",
                    capabilities={"adjust_duration"},
                )
                return "Timer set."
            if tl == "set it to ten minutes":
                return "Timer updated."
            return None

        with self._dispatch([]) as (dispatch, _service), mock.patch.object(
            dispatch,
            "handle_alarm_controls",
            side_effect=alarms,
        ):
            first = dispatch.process_device_commands("set a timer for five minutes")
            second = dispatch.process_device_commands("actually, make that ten")

        self.assertEqual(first, "Timer set.")
        self.assertEqual(second, "Timer updated.")
        self.assertEqual(seen, ["set a timer for five minutes", "set it to ten minutes"])

    def test_unrelated_deterministic_claim_supersedes_the_prior_intent(self):
        states = [
            {"entity_id": "light.stair", "state": "on", "attributes": {"friendly_name": "Stair Light"}},
            {"entity_id": "light.desk_lamp", "state": "on", "attributes": {"friendly_name": "Desk Lamp"}},
        ]
        with self._dispatch(states) as (dispatch, service):
            dispatch.process_device_commands("make the stair light red")
            self.assertIsNotNone(dialogue_state.resolve_intent_frame())

            time_response = dispatch.process_device_commands("what time is it")
            self.assertIsNotNone(time_response)
            self.assertIsNone(dialogue_state.resolve_intent_frame())

            stale_followup = dispatch.process_device_commands("and the desk lamp too")

        self.assertIsNone(stale_followup)
        service.assert_called_once_with(
            "light/turn_on",
            {"entity_id": "light.stair", "color_name": "red"},
        )

    def test_ambiguous_device_waits_for_a_short_selection(self):
        states = [
            {"entity_id": "light.floor_lamp", "state": "on", "attributes": {"friendly_name": "Floor Lamp"}},
            {"entity_id": "light.desk_lamp", "state": "on", "attributes": {"friendly_name": "Desk Lamp"}},
        ]
        with self._dispatch(states) as (dispatch, service):
            prompt = dispatch.process_device_commands("turn off the lamp")
            self.assertEqual(service.call_count, 0)
            result = dispatch.process_device_commands("floor")

        self.assertIn("Which lamp", prompt)
        self.assertEqual(result, "")
        service.assert_called_once_with(
            "light/turn_off",
            {"entity_id": "light.floor_lamp"},
        )

    def test_ambiguous_color_target_uses_the_same_clarification_contract(self):
        states = [
            {"entity_id": "light.floor_lamp", "state": "on", "attributes": {"friendly_name": "Floor Lamp"}},
            {"entity_id": "light.desk_lamp", "state": "on", "attributes": {"friendly_name": "Desk Lamp"}},
        ]
        with self._dispatch(states) as (dispatch, service):
            prompt = dispatch.process_device_commands("make the lamp red")
            self.assertEqual(service.call_count, 0)
            result = dispatch.process_device_commands("desk")

        self.assertIn("Which lamp", prompt)
        self.assertEqual(result, "")
        service.assert_called_once_with(
            "light/turn_on",
            {"entity_id": "light.desk_lamp", "color_name": "red"},
        )


if __name__ == "__main__":
    unittest.main()
