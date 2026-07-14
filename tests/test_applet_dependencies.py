from __future__ import annotations

import unittest
from unittest import mock

import applet_controls


class AppletDependencyTests(unittest.TestCase):
    def test_note_lights_fails_before_launch_when_optional_modules_are_missing(self):
        def find_spec(name):
            return object() if name == "sounddevice" else None

        with (
            mock.patch.object(applet_controls.importlib.util, "find_spec", side_effect=find_spec),
            mock.patch.object(applet_controls, "_start_subprocess_applet") as start,
        ):
            response = applet_controls.start_applet("note_lights")

        self.assertIn("isn't available on this device", response)
        start.assert_not_called()

    def test_note_lights_launches_when_optional_modules_are_available(self):
        with (
            mock.patch.object(applet_controls.importlib.util, "find_spec", return_value=object()),
            mock.patch.object(
                applet_controls,
                "_start_subprocess_applet",
                return_value="Started note_lights",
            ) as start,
        ):
            response = applet_controls.start_applet("note_lights")

        self.assertEqual(response, "Started note_lights")
        start.assert_called_once_with("note_lights")


if __name__ == "__main__":
    unittest.main()
