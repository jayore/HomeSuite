from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ConsoleUiContractTests(unittest.TestCase):
    def test_setup_and_preview_controls_are_accessible(self):
        html = (ROOT / "console_static" / "index.html").read_text(encoding="utf-8")

        self.assertIn('data-view="setup"', html)
        self.assertIn('id="view-setup"', html)
        self.assertIn(
            'id="preview-onboarding" class="button secondary" type="button" '
            'title="Preview onboarding" aria-label="Preview onboarding"',
            html,
        )
        self.assertIn(
            'id="exit-onboarding-preview" class="button secondary" type="button" '
            'title="Exit onboarding preview" aria-label="Exit onboarding preview"',
            html,
        )

    def test_preview_actions_are_inert(self):
        javascript = (ROOT / "console_static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('if (state.setupPreview) return;', javascript)
        self.assertIn(
            'action.disabled = Boolean(preview || step.disabled',
            javascript,
        )


if __name__ == "__main__":
    unittest.main()
