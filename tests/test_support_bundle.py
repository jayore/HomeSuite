from __future__ import annotations

import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import support_bundle


class SupportBundleTests(unittest.TestCase):
    def test_git_metadata_tolerates_a_non_git_source_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            metadata = support_bundle._git_metadata(Path(tmp))

        self.assertEqual(
            metadata,
            {"commit": None, "branch": None, "worktree_dirty": None},
        )

    def test_bundle_excludes_private_config_raw_logs_and_check_details(self):
        class FakeDoctor:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, **kwargs):
                return 0

            def redacted_report(self):
                return {
                    "ok": True,
                    "roles": [
                        {
                            "role": "text",
                            "status": "OK",
                            "required_failures": 0,
                            "warnings": 0,
                        }
                    ],
                    "checks": [
                        {
                            "group": "Rooms",
                            "status": "OK",
                            "label": "room brightness",
                            "required": False,
                            "roles": ("text",),
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "support.tar.gz"
            with (
                mock.patch("tools.doctor.Doctor", FakeDoctor),
                mock.patch.object(support_bundle, "build_summary", return_value={"safe": True}),
            ):
                support_bundle.write_bundle(output, live=False)

            with tarfile.open(output, "r:gz") as archive:
                names = archive.getnames()
                summary = json.load(archive.extractfile("summary.json"))
                doctor_report = archive.extractfile("doctor.json").read().decode()
            self.assertEqual(names, ["README.txt", "doctor.json", "summary.json"])
            self.assertEqual(summary, {"safe": True})
            self.assertNotIn("private_config.py", names)
            self.assertNotIn("homesuite.log", names)
            self.assertNotIn("living_room", doctor_report)
            self.assertNotIn("light.secret_lamp", doctor_report)

    def test_doctor_redacted_report_omits_local_identifiers_and_details(self):
        from tools.doctor import Check, Doctor

        doctor = Doctor.__new__(Doctor)
        doctor.requested_roles = ("text",)
        doctor._active_roles = ("text",)
        doctor.checks = [
            Check(
                group="Rooms",
                status="OK",
                label="living_room brightness",
                detail="entity light.secret_lamp",
                roles=("text",),
            ),
            Check(
                group="Runtime readiness",
                status="OK",
                label="configured audio input",
                detail="Secret Microphone (index 7, 48000 Hz)",
                roles=("text",),
            ),
        ]

        report = doctor.redacted_report()
        encoded = json.dumps(report)

        self.assertNotIn("living_room", encoded)
        self.assertNotIn("light.secret_lamp", encoded)
        self.assertNotIn("Secret Microphone", encoded)
        self.assertEqual(report["checks"][0]["label"], "room brightness")


if __name__ == "__main__":
    unittest.main()
