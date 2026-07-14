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

    def test_bundle_excludes_private_config_and_raw_logs(self):
        class FakeDoctor:
            checks = []

            def __init__(self, *args, **kwargs):
                pass

            def run(self, **kwargs):
                return 0

            def role_summary(self):
                return [{"role": "text", "status": "OK", "required_failures": 0, "warnings": 0}]

            def required_failures(self):
                return []

            def relevant_checks(self):
                return []

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
            self.assertEqual(names, ["README.txt", "doctor.json", "summary.json"])
            self.assertEqual(summary, {"safe": True})
            self.assertNotIn("private_config.py", names)
            self.assertNotIn("homesuite.log", names)


if __name__ == "__main__":
    unittest.main()
