from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from joke_history import RecentJokeHistory


class RecentJokeHistoryTests(unittest.TestCase):
    def test_history_survives_reload_and_remains_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recent_jokes.json"
            history = RecentJokeHistory(path, max_entries=3)

            for joke in ("one", "two", "three", "four"):
                self.assertTrue(history.remember(joke))

            reloaded = RecentJokeHistory(path, max_entries=3)
            self.assertEqual(reloaded.snapshot(), ["two", "three", "four"])
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {
                    "version": 1,
                    "jokes": ["two", "three", "four"],
                },
            )

    def test_repeated_joke_moves_to_most_recent_without_growing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recent_jokes.json"
            history = RecentJokeHistory(path, max_entries=3)

            history.remember("one")
            history.remember("two")
            history.remember("one")

            self.assertEqual(history.snapshot(), ["two", "one"])
            self.assertEqual(RecentJokeHistory(path).snapshot(), ["two", "one"])

    def test_processes_merge_updates_instead_of_overwriting_stale_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recent_jokes.json"
            voice_history = RecentJokeHistory(path)
            telegram_history = RecentJokeHistory(path)

            voice_history.remember("voice joke")
            telegram_history.remember("telegram joke")

            self.assertEqual(
                voice_history.snapshot(),
                ["voice joke", "telegram joke"],
            )

    def test_malformed_state_falls_back_and_is_repaired_on_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recent_jokes.json"
            path.write_text("not json", encoding="utf-8")

            history = RecentJokeHistory(path)
            self.assertEqual(history.snapshot(), [])
            self.assertTrue(history.remember("a working joke"))

            self.assertEqual(
                RecentJokeHistory(path).snapshot(),
                ["a working joke"],
            )

    def test_clear_is_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recent_jokes.json"
            history = RecentJokeHistory(path)
            history.remember("one")

            history.clear()

            self.assertEqual(RecentJokeHistory(path).snapshot(), [])


if __name__ == "__main__":
    unittest.main()
