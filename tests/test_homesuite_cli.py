from __future__ import annotations

import unittest
from unittest import mock

from tools import homesuite_cli


class HomeSuiteCliTests(unittest.TestCase):
    def test_test_subcommand_forwards_capture_mode_arguments_unchanged(self):
        with mock.patch.object(homesuite_cli, "_run_script", return_value=0) as run:
            result = homesuite_cli.main(["test", "what lights are on?"])

        self.assertEqual(result, 0)
        self.assertEqual(run.call_args.args[1], ["what lights are on?"])

    def test_repl_subcommand_accepts_live_flag(self):
        with mock.patch.object(homesuite_cli, "_run_script", return_value=0) as run:
            result = homesuite_cli.main(["repl", "--live"])

        self.assertEqual(result, 0)
        self.assertEqual(run.call_args.args[1], ["--live"])

    def test_status_delegates_to_live_doctor(self):
        with mock.patch.object(homesuite_cli, "_status", return_value=0) as status:
            result = homesuite_cli.main(["status"])

        self.assertEqual(result, 0)
        status.assert_called_once_with()

    def test_logs_help_uses_logs_subcommand_parser(self):
        with mock.patch.object(homesuite_cli, "_logs", return_value=0) as logs:
            result = homesuite_cli.main(["logs", "--help"])

        self.assertEqual(result, 0)
        logs.assert_called_once_with(["--help"])


if __name__ == "__main__":
    unittest.main()
