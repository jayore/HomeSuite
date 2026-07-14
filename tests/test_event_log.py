from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import event_log


class EventLogTests(unittest.TestCase):
    def _write(self, phrase: str) -> None:
        event_log.log_command_event(
            phrase,
            SimpleNamespace(source_id="test", source_type="test", origin="test", source_room=None, effective_target_room=None),
            SimpleNamespace(handled=True, action_occurred=True, source="test"),
            12,
        )

    def test_metadata_log_omits_command_text_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            with (
                mock.patch.object(event_log, "_LOG_DIR", tmp),
                mock.patch.object(event_log, "_LOG_FILE", str(path)),
                mock.patch.object(event_log, "_pref", side_effect=lambda _name, default: default),
            ):
                self._write("turn on the very private bedroom light")

            entry = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(entry["text_recorded"])
            self.assertEqual(entry["text_length"], len("turn on the very private bedroom light"))
            self.assertNotIn("text", entry)

    def test_event_log_rotates_before_exceeding_configured_size(self):
        values = {
            "COMMAND_EVENT_LOG_ENABLED": True,
            "COMMAND_EVENT_LOG_STORE_TEXT": False,
            "COMMAND_EVENT_LOG_MAX_BYTES": 1,
            "COMMAND_EVENT_LOG_BACKUP_COUNT": 1,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            with (
                mock.patch.object(event_log, "_LOG_DIR", tmp),
                mock.patch.object(event_log, "_LOG_FILE", str(path)),
                mock.patch.object(event_log, "_pref", side_effect=lambda name, default: values.get(name, default)),
            ):
                self._write("first")
                self._write("second")

            self.assertTrue(path.exists())
            self.assertTrue(Path(f"{path}.1").exists())
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
