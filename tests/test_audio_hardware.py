from __future__ import annotations

import unittest

from audio_hardware import _parse_devices


class AudioHardwareTests(unittest.TestCase):
    def test_parse_devices_keeps_stable_card_id_and_busy_state(self):
        devices = _parse_devices(
            "card 3: MINI [MOVO X1 MINI], device 0: USB Audio [USB Audio]\n"
            "  Subdevices: 0/1\n"
            "  Subdevice #0: subdevice #0\n",
            direction="input",
        )

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["card_id"], "MINI")
        self.assertEqual(devices[0]["plughw"], "plughw:CARD=MINI,DEV=0")
        self.assertTrue(devices[0]["busy"])

    def test_parse_devices_reports_available_subdevice(self):
        devices = _parse_devices(
            "card 1: Device [USB PnP Sound Device], device 0: USB Audio [USB Audio]\n"
            "  Subdevices: 1/1\n",
            direction="output",
        )

        self.assertFalse(devices[0]["busy"])
        self.assertEqual(devices[0]["available_subdevices"], 1)


if __name__ == "__main__":
    unittest.main()
