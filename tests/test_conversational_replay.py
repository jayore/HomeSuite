"""Replay conversational language contracts from a human-editable corpus."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from conversational_nl import (
    build_intent_frame,
    normalize_conversational_command,
    resolve_intent_followup,
)


CORPUS = ROOT / "tests" / "fixtures" / "conversational_nl_replay.jsonl"


def _cases():
    with CORPUS.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            case = json.loads(line)
            case["line_number"] = line_number
            yield case


class ConversationalReplayTests(unittest.TestCase):
    def test_normalization_and_followup_corpus(self):
        cases = list(_cases())
        self.assertGreaterEqual(len(cases), 25)

        for case in (item for item in cases if item["kind"] != "route"):
            with self.subTest(
                line=case["line_number"],
                name=case["name"],
                kind=case["kind"],
            ):
                if case["kind"] == "normalize":
                    actual = normalize_conversational_command(case["input"])
                    self.assertEqual(actual, case["expected"])
                    continue

                self.assertEqual(case["kind"], "followup")
                frame_data = case["frame"]
                frame = build_intent_frame(
                    frame_data["claim"],
                    frame_data["text"],
                    metadata=frame_data.get("metadata"),
                    target_keys=frame_data.get("target_keys", ()),
                )
                self.assertIsNotNone(frame)
                resolution = resolve_intent_followup(
                    case["input"],
                    frame,
                    room_targets=case.get("room_targets", ()),
                )
                if case["expected"] is None:
                    self.assertIsNone(resolution)
                    continue
                self.assertIsNotNone(resolution)
                self.assertEqual(resolution.rewritten_text, case["expected"])
                self.assertEqual(resolution.kind, case["expected_kind"])

    def test_routing_corpus(self):
        try:
            from semantic_router import route_utterance
        except ModuleNotFoundError as exc:
            self.skipTest(f"full project dependencies are unavailable: {exc}")

        route_cases = [case for case in _cases() if case["kind"] == "route"]
        self.assertGreaterEqual(len(route_cases), 4)
        for case in route_cases:
            with self.subTest(line=case["line_number"], name=case["name"]):
                actual = route_utterance(text=case["input"]).outcome.value
                self.assertEqual(actual, case["expected"])


if __name__ == "__main__":
    unittest.main()
