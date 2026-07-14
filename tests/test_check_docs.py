from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.check_docs import broken_links


class CheckDocsTests(unittest.TestCase):
    def test_ignores_external_and_anchor_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            document = root / "README.md"
            document.write_text(
                "[web](https://example.com) [anchor](#section) [mail](mailto:test@example.com)\n",
                encoding="utf-8",
            )

            self.assertEqual(broken_links([document], root=root), [])

    def test_reports_missing_and_accepts_existing_relative_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            docs.mkdir()
            target = docs / "target.md"
            target.write_text("# Target\n", encoding="utf-8")
            document = root / "README.md"
            document.write_text("[ok](docs/target.md) [missing](docs/missing.md)\n", encoding="utf-8")

            self.assertEqual(broken_links([document], root=root), [(document, "docs/missing.md")])


if __name__ == "__main__":
    unittest.main()
