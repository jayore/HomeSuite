"""Keep every documented spoken example on its intended routing path.

Focused module tests prove parsing and state mutation. This suite owns the
cross-cutting contract: every backticked bullet in ``docs/COMMANDS.md`` must be
classified as deterministic device work, intentional AI conversation, or a
non-spoken external interface. Adding documentation without choosing one of
those lanes fails the suite.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from semantic_router import RouteOutcome, route_utterance


COMMANDS_DOC = ROOT / "docs" / "COMMANDS.md"
AI_SECTION = "Chat, AI Fallback, And Follow-Ups"
EXTERNAL_SECTION = "External Interfaces"

AI_COMMANDS = {
    "what's the latest news?",
    "what is the most popular Beatles song?",
    "what movie has Darth Vader telling Luke he is his father?",
    "what is this movie about?",
    "tell me more about that",
    "how far is that by car?",
    "how far is that to drive?",
    "how long would that take?",
}

CONTEXTUAL_DEVICE_COMMANDS = {
    "play it",
    "watch it",
}


def _documented_examples() -> list[tuple[str, str]]:
    section = ""
    examples = []
    for line in COMMANDS_DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        match = re.match(r"^\* `([^`]+)`", line)
        if match:
            examples.append((section, match.group(1)))
    return examples


class DocumentedCommandRoutingContractTests(unittest.TestCase):
    def test_every_spoken_example_has_the_documented_routing_contract(self):
        examples = _documented_examples()
        self.assertGreaterEqual(len(examples), 150)

        for section, command in examples:
            with self.subTest(section=section, command=command):
                if section == EXTERNAL_SECTION:
                    continue
                if section == AI_SECTION:
                    self.assertIn(command, AI_COMMANDS | CONTEXTUAL_DEVICE_COMMANDS)
                    expected = (
                        RouteOutcome.CHATGPT
                        if command in AI_COMMANDS
                        else RouteOutcome.DEVICE
                    )
                else:
                    expected = RouteOutcome.DEVICE

                self.assertEqual(route_utterance(text=command).outcome, expected)

    def test_ai_section_cannot_grow_without_an_explicit_contract(self):
        documented = {
            command
            for section, command in _documented_examples()
            if section == AI_SECTION
        }

        self.assertEqual(documented, AI_COMMANDS | CONTEXTUAL_DEVICE_COMMANDS)


if __name__ == "__main__":
    unittest.main()
