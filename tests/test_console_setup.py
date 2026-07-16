from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from console_setup import ConsoleSetupError, ConsoleSetupManager


class ConsoleSetupManagerTests(unittest.TestCase):
    def test_activation_requires_installed_path_unit(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = ConsoleSetupManager(
                root=Path(temp),
                path_unit=Path(temp) / "missing.path",
            )
            with self.assertRaisesRegex(ConsoleSetupError, "not installed"):
                manager.request_activation()

    def test_activation_writes_one_fixed_private_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path_unit = root / "homesuite-runtime.path"
            path_unit.write_text("[Path]\n", encoding="utf-8")
            manager = ConsoleSetupManager(root=root, path_unit=path_unit)

            first = manager.request_activation()
            second = manager.request_activation()

            self.assertTrue(first["activation_requested"])
            self.assertFalse(first["already_requested"])
            self.assertTrue(second["already_requested"])
            self.assertEqual(
                manager.marker_path.parent.resolve(),
                (root / "state").resolve(),
            )
            self.assertTrue(manager.marker_path.is_file())
            status = manager.public_status(runtime_healthy=False)
            self.assertTrue(status["complete"])
            self.assertTrue(status["activation_supported"])

    def test_running_existing_node_is_complete_without_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = ConsoleSetupManager(root=Path(temp), path_unit=Path(temp) / "missing.path")
            status = manager.public_status(runtime_healthy=True)
            self.assertTrue(status["complete"])
            self.assertFalse(status["activation_requested"])


if __name__ == "__main__":
    unittest.main()
