from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from console_bootstrap import ConsoleBootstrap, ConsoleBootstrapError


class ConsoleBootstrapTests(unittest.TestCase):
    def make_root(self, temp: str, *, marker: bool = True, console_key: str = "") -> Path:
        root = Path(temp)
        (root / "state").mkdir()
        (root / "private_config.py").write_text(
            'HOMESUITE_HTTP_API_KEY = "generated-api-key"\n'
            f"HOMESUITE_CONSOLE_KEY = {console_key!r}\n",
            encoding="utf-8",
        )
        if marker:
            (root / "state" / "console_bootstrap_pending").write_text("pending\n", encoding="utf-8")
        return root

    def test_blank_key_without_installer_marker_is_not_claimable(self):
        with tempfile.TemporaryDirectory() as temp:
            bootstrap = ConsoleBootstrap(root=self.make_root(temp, marker=False))
            self.assertFalse(bootstrap.pending())
            with self.assertRaisesRegex(ConsoleBootstrapError, "already been claimed"):
                bootstrap.claim("a sufficiently long passphrase", "a sufficiently long passphrase")

    def test_claim_persists_key_removes_marker_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_root(temp)
            bootstrap = ConsoleBootstrap(root=root)
            self.assertTrue(bootstrap.pending())

            result = bootstrap.claim("correct horse battery", "correct horse battery")

            self.assertTrue(result["claimed"])
            self.assertFalse(bootstrap.pending())
            self.assertFalse(bootstrap.marker_path.exists())
            self.assertIn(
                "HOMESUITE_CONSOLE_KEY = 'correct horse battery'",
                bootstrap.config_path.read_text(encoding="utf-8"),
            )
            backup = Path(result["backup_dir"]) / "private_config.py"
            self.assertTrue(backup.is_file())
            self.assertIn("HOMESUITE_CONSOLE_KEY = ''", backup.read_text(encoding="utf-8"))

    def test_claim_validates_length_and_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            bootstrap = ConsoleBootstrap(root=self.make_root(temp))
            with self.assertRaisesRegex(ConsoleBootstrapError, "at least 12"):
                bootstrap.claim("too short", "too short")
            with self.assertRaisesRegex(ConsoleBootstrapError, "do not match"):
                bootstrap.claim("long enough passphrase", "different passphrase")
            self.assertTrue(bootstrap.pending())


if __name__ == "__main__":
    unittest.main()
