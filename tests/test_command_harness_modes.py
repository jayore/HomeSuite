from __future__ import annotations

import unittest

import command_repl
from tools import test_commands


class CommandHarnessModeTests(unittest.TestCase):
    def test_interactive_shell_defaults_to_live_reads_and_blocked_writes(self):
        self.assertEqual(command_repl.parse_mode([]), "capture")
        self.assertEqual(command_repl.parse_mode(["--capture"]), "capture")

    def test_interactive_shell_keeps_live_and_isolated_modes_explicit(self):
        self.assertEqual(command_repl.parse_mode(["--live"]), "live")
        self.assertEqual(command_repl.parse_mode(["--isolated"]), "test")
        self.assertEqual(command_repl.parse_mode(["--test"]), "test")
        with self.assertRaises(ValueError):
            command_repl.parse_mode(["--live", "--isolated"])

    def test_one_shot_harness_defaults_to_capture_mode(self):
        options = test_commands._parse_cli(["what lights are on?"])
        self.assertEqual(options["mode"], "capture")

    def test_one_shot_harness_allows_explicit_isolated_mode(self):
        options = test_commands._parse_cli(["what lights are on?", "--isolated"])
        self.assertEqual(options["mode"], "test")


if __name__ == "__main__":
    unittest.main()
