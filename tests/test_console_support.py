from __future__ import annotations

import io
import tarfile
import unittest
from unittest import mock

import console_support


def _write_archive(path, names):
    with tarfile.open(path, "w:gz") as archive:
        for name in names:
            payload = b"safe\n"
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


class ConsoleSupportTests(unittest.TestCase):
    def test_console_bundle_accepts_only_the_reviewed_redacted_members(self):
        with mock.patch.object(
            console_support,
            "write_bundle",
            side_effect=lambda output, live: _write_archive(output, console_support.ALLOWED_MEMBERS),
        ):
            bundle = console_support.build_console_support_bundle(live=True)

        self.assertTrue(bundle.filename.startswith("homesuite-support-"))
        self.assertTrue(bundle.filename.endswith(".tar.gz"))
        with tarfile.open(fileobj=io.BytesIO(bundle.content), mode="r:gz") as archive:
            self.assertEqual(set(archive.getnames()), set(console_support.ALLOWED_MEMBERS))

    def test_console_bundle_rejects_an_unreviewed_archive_member(self):
        with mock.patch.object(
            console_support,
            "write_bundle",
            side_effect=lambda output, live: _write_archive(
                output,
                [*console_support.ALLOWED_MEMBERS, "private_config.py"],
            ),
        ):
            with self.assertRaisesRegex(console_support.ConsoleSupportError, "unexpected"):
                console_support.build_console_support_bundle()


if __name__ == "__main__":
    unittest.main()
