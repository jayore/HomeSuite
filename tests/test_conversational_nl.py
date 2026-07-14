from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from conversational_nl import (
    IntentFrame,
    build_intent_frame,
    normalize_conversational_command,
    normalize_conversational_shell,
    resolve_intent_followup,
)
from device_clarification import (
    build_binary_device_clarification,
    build_light_device_clarification,
)


class ConversationalShellTests(unittest.TestCase):
    def test_polite_request_wrappers_collapse_to_existing_command_shape(self):
        cases = {
            "Could you turn the stair light off for me?": "turn the stair light off",
            "Okay, please go ahead and set the bedroom to 30 percent.": "set the bedroom to 30 percent.",
            "Would you mind turning off the floor lamp, please?": "turn off the floor lamp",
            "I need you to switch on the desk lamp": "turn desk lamp on",
            "Power the fan off": "turn fan off",
            "Shut off the coffee maker": "turn coffee maker off",
        }
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(normalize_conversational_command(phrase), expected)

    def test_content_bearing_command_keeps_trailing_words(self):
        self.assertEqual(
            normalize_conversational_shell("Could you say thanks for me"),
            "say thanks for me",
        )
        self.assertEqual(
            normalize_conversational_shell("Could you play Dance for Me"),
            "play Dance for Me",
        )

    def test_timing_language_is_not_removed(self):
        self.assertEqual(
            normalize_conversational_shell(
                "Could you turn off the lamp when you get a chance?"
            ),
            "turn off the lamp when you get a chance?",
        )
        self.assertEqual(
            normalize_conversational_shell("right now turn off the right lamp"),
            "right now turn off the right lamp",
        )

    def test_qualitative_brightness_maps_to_canonical_percentages(self):
        self.assertEqual(
            normalize_conversational_command("Make the stair light half brightness"),
            "set stair light to 50%",
        )
        self.assertEqual(
            normalize_conversational_command("Turn the bedroom all the way up"),
            "set bedroom to 100%",
        )

    def test_timer_and_volume_shortcuts_keep_existing_canonical_contracts(self):
        self.assertEqual(
            normalize_conversational_command("Give me five minutes"),
            "set a timer for five minutes",
        )
        self.assertEqual(
            normalize_conversational_command("Start a ten minute timer"),
            "set a timer for ten minutes",
        )
        self.assertEqual(
            normalize_conversational_command("Set the kitchen volume to half"),
            "set kitchen volume to 50%",
        )
        self.assertEqual(
            normalize_conversational_command("Set the kitchen to half volume"),
            "set kitchen volume to 50%",
        )

    def test_explicit_actual_correction_becomes_an_ordinary_command(self):
        self.assertEqual(
            normalize_conversational_command("Actually, turn it off"),
            "turn it off",
        )


class IntentFrameTests(unittest.TestCase):
    def test_binary_action_can_transfer_to_another_target(self):
        frame = build_intent_frame("binary", "turn the stair light off")
        self.assertIsNotNone(frame)
        resolution = resolve_intent_followup("and the floor lamp too", frame)
        self.assertEqual(resolution.rewritten_text, "turn floor lamp off")
        self.assertEqual(resolution.kind, "target_transfer")
        self.assertEqual(
            resolve_intent_followup("and the floor lamp?", frame).rewritten_text,
            "turn floor lamp off",
        )
        self.assertEqual(
            resolve_intent_followup("also the floor lamp", frame).rewritten_text,
            "turn floor lamp off",
        )
        self.assertIsNone(resolve_intent_followup("and that's all", frame))
        self.assertIsNone(resolve_intent_followup("also make it brighter", frame))
        room_resolution = resolve_intent_followup(
            "same in the bedroom",
            frame,
            room_targets={"bedroom"},
        )
        self.assertEqual(room_resolution.rewritten_text, "turn bedroom lights off")

    def test_color_action_supports_target_transfer_and_correction(self):
        frame = build_intent_frame("color", "set stair light to red")
        self.assertEqual(
            resolve_intent_followup("same in the bedroom", frame).rewritten_text,
            "set bedroom to red",
        )
        self.assertEqual(
            resolve_intent_followup(
                "same in the bedroom",
                frame,
                room_targets={"bedroom"},
            ).rewritten_text,
            "set bedroom lights to red",
        )
        self.assertEqual(
            resolve_intent_followup("and in the office as well", frame).rewritten_text,
            "set office to red",
        )
        self.assertEqual(
            resolve_intent_followup("actually, make it blue", frame).rewritten_text,
            "set stair light to blue",
        )
        self.assertEqual(
            resolve_intent_followup("green", frame).rewritten_text,
            "set stair light to green",
        )
        self.assertEqual(
            resolve_intent_followup("make it purple instead", frame).rewritten_text,
            "set stair light to purple",
        )
        self.assertEqual(
            resolve_intent_followup("I meant yellow", frame).rewritten_text,
            "set stair light to yellow",
        )

    def test_brightness_action_accepts_human_level_corrections(self):
        frame = build_intent_frame("brightness", "set stair light to 30%")
        self.assertEqual(
            resolve_intent_followup("make that full", frame).rewritten_text,
            "set stair light brightness to 100%",
        )
        self.assertEqual(
            resolve_intent_followup("and the desk lamp too", frame).rewritten_text,
            "set desk lamp brightness to 30%",
        )

    def test_volume_action_supports_level_correction_and_room_transfer(self):
        frame = build_intent_frame("volume", "set kitchen volume to 30%")
        self.assertEqual(
            resolve_intent_followup("make that half", frame).rewritten_text,
            "set kitchen volume to 50%",
        )
        self.assertEqual(
            resolve_intent_followup("and the bedroom too", frame).rewritten_text,
            "set bedroom volume to 30%",
        )

        relative = build_intent_frame("volume", "make the kitchen quieter")
        self.assertEqual(
            resolve_intent_followup("and the bedroom too", relative).rewritten_text,
            "make the bedroom quieter",
        )

    def test_weather_refinement_preserves_location(self):
        frame = build_intent_frame(
            "weather",
            "what's the weather tomorrow in seattle",
            metadata={"location": "Seattle", "mode": "day", "day_offset": 1},
        )
        self.assertEqual(
            resolve_intent_followup("what about Thursday?", frame).rewritten_text,
            "weather thursday in seattle",
        )
        self.assertEqual(
            resolve_intent_followup("and Friday?", frame).rewritten_text,
            "weather friday in seattle",
        )

    def test_astronomy_refinement_preserves_query_shape(self):
        visible = build_intent_frame(
            "astronomy",
            "what planets are visible tonight",
            metadata={
                "intent": "visible_planets",
                "night_window": True,
                "day_offset": 0,
                "explicit_day": True,
            },
        )
        self.assertEqual(
            resolve_intent_followup("and tomorrow?", visible).rewritten_text,
            "what planets are visible tomorrow",
        )

        rise = build_intent_frame(
            "astronomy",
            "when does jupiter rise",
            metadata={"intent": "planet_event", "planet": "jupiter", "event": "rise"},
        )
        self.assertEqual(
            resolve_intent_followup("what about Saturn?", rise).rewritten_text,
            "when does saturn rise",
        )

    def test_calendar_refinement_preserves_the_query_domain(self):
        frame = build_intent_frame(
            "calendar",
            "what's on my calendar today",
            metadata={"label": "today", "next_only": False},
        )
        self.assertEqual(
            resolve_intent_followup("and tomorrow?", frame).rewritten_text,
            "what's on my calendar tomorrow",
        )
        self.assertEqual(
            resolve_intent_followup("what about Friday?", frame).rewritten_text,
            "what's on my calendar friday",
        )
        self.assertIsNone(resolve_intent_followup("How about Portland?", frame))
        self.assertEqual(
            resolve_intent_followup("I meant Friday", frame).rewritten_text,
            "what's on my calendar friday",
        )

    def test_timer_correction_reuses_the_original_unit(self):
        frame = build_intent_frame("timer", "set a five minute timer for five minutes")
        self.assertIsNotNone(frame)
        self.assertEqual(
            resolve_intent_followup("actually, make that ten", frame).rewritten_text,
            "set it to ten minutes",
        )
        self.assertEqual(
            resolve_intent_followup("another minute", frame).rewritten_text,
            "add one minute to it",
        )
        self.assertEqual(
            resolve_intent_followup("give me another five minutes", frame).rewritten_text,
            "add five minutes to it",
        )

    def test_complete_command_is_never_reinterpreted_as_a_followup(self):
        frame = IntentFrame(
            domain="light",
            intent="set_color",
            canonical_command="set stair light to red",
            slots={"target": "stair light", "value": "red"},
            followups=frozenset({"target_transfer", "value_correction"}),
        )
        self.assertIsNone(resolve_intent_followup("turn the desk lamp off", frame))


class DeviceClarificationDetectionTests(unittest.TestCase):
    STATES = [
        {
            "entity_id": "light.floor_lamp",
            "state": "on",
            "attributes": {"friendly_name": "Floor Lamp"},
        },
        {
            "entity_id": "light.desk_lamp",
            "state": "off",
            "attributes": {"friendly_name": "Desk Lamp"},
        },
        {
            "entity_id": "switch.coffee_maker",
            "state": "on",
            "attributes": {"friendly_name": "Coffee Maker"},
        },
    ]

    def test_generic_lamp_gets_two_replayable_options(self):
        result = build_binary_device_clarification(
            "turn off the lamp",
            states_snapshot=self.STATES,
        )
        self.assertIsNotNone(result)
        self.assertEqual(len(result.options), 2)
        self.assertIn("Floor Lamp", result.prompt)
        aliases = set(result.options[0].aliases) | set(result.options[1].aliases)
        self.assertIn("floor", aliases)

    def test_exact_device_name_remains_unambiguous(self):
        self.assertIsNone(
            build_binary_device_clarification(
                "turn off the floor lamp",
                states_snapshot=self.STATES,
            )
        )

    def test_exact_device_still_wins_after_many_partial_matches(self):
        states = [
            {
                "entity_id": f"light.desk_lamp_{index}",
                "state": "on",
                "attributes": {"friendly_name": f"Desk Lamp {index}"},
            }
            for index in range(8)
        ]
        states.append(
            {
                "entity_id": "light.desk_lamp",
                "state": "on",
                "attributes": {"friendly_name": "Desk Lamp"},
            }
        )

        self.assertIsNone(
            build_binary_device_clarification(
                "turn desk lamp off",
                states_snapshot=states,
            )
        )

    def test_configured_alias_remains_authoritative(self):
        self.assertIsNone(
            build_binary_device_clarification(
                "turn off the lamp",
                states_snapshot=self.STATES,
                aliases_by_entity={"light.floor_lamp": ["lamp"]},
            )
        )

    def test_contextual_bare_light_is_left_to_room_routing(self):
        self.assertIsNone(
            build_binary_device_clarification(
                "turn off the light",
                states_snapshot=self.STATES,
            )
        )

    def test_ambiguous_light_color_and_brightness_are_also_clarified(self):
        color = build_light_device_clarification(
            "make the lamp red",
            states_snapshot=self.STATES,
        )
        self.assertEqual(len(color.options), 2)
        self.assertEqual(
            {option.command for option in color.options},
            {"set floor lamp to red", "set desk lamp to red"},
        )

        brightness = build_light_device_clarification(
            "set the lamp to 40%",
            states_snapshot=self.STATES,
        )
        self.assertEqual(
            {option.command for option in brightness.options},
            {
                "set floor lamp brightness to 40%",
                "set desk lamp brightness to 40%",
            },
        )

    def test_room_targets_remain_authoritative_for_light_levels(self):
        self.assertIsNone(
            build_light_device_clarification(
                "set the bedroom to 40%",
                states_snapshot=[
                    {
                        "entity_id": "light.bedroom_floor",
                        "state": "on",
                        "attributes": {"friendly_name": "Bedroom Floor"},
                    },
                    {
                        "entity_id": "light.bedroom_desk",
                        "state": "on",
                        "attributes": {"friendly_name": "Bedroom Desk"},
                    },
                ],
                authoritative_targets={"bedroom"},
            )
        )


if __name__ == "__main__":
    unittest.main()
