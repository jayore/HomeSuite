from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InstallContractTests(unittest.TestCase):
    def test_existing_environment_is_checked_before_fetch(self):
        script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

        preflight = script.index(
            'require_supported_python "$INSTALL_DIR/.venv/bin/python"'
        )
        fetch = script.index('git -C "$INSTALL_DIR" fetch origin "$BRANCH"')

        self.assertLess(preflight, fetch)

    def test_installer_keeps_setuptools_compatible_with_webrtcvad(self):
        script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

        self.assertIn('pip install --upgrade pip wheel "setuptools<81"', script)

    def test_systemd_install_includes_separate_management_console(self):
        script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

        self.assertIn("homesuite-console.service.template", script)
        self.assertIn("/etc/systemd/system/homesuite-console.service", script)
        self.assertIn("enable homesuite.service homesuite-console.service", script)


if __name__ == "__main__":
    unittest.main()
