from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ConsoleUiContractTests(unittest.TestCase):
    def test_setup_and_preview_controls_are_accessible(self):
        html = (ROOT / "console_static" / "index.html").read_text(encoding="utf-8")

        self.assertIn('data-view="setup"', html)
        self.assertIn('id="view-setup"', html)
        self.assertIn('id="return-to-setup"', html)
        self.assertIn('aria-label="Back to setup"', html)
        self.assertIn('id="setup-journey-bar"', html)
        self.assertIn('id="setup-nav" class="nav-item" type="button" data-view="setup" hidden', html)
        self.assertIn('id="review-setup"', html)
        self.assertIn('aria-label="Review setup"', html)
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

    def test_completed_setup_leaves_primary_navigation(self):
        javascript = (ROOT / "console_static" / "app.js").read_text(encoding="utf-8")

        self.assertIn(
            'setupNav.hidden = !statusKnown || (complete && view !== "setup" && !state.setupJourneyActive);',
            javascript,
        )
        self.assertIn('$("#review-setup").hidden = !statusKnown || !complete;', javascript)
        self.assertIn(
            '$("#setup-journey-bar").hidden = !statusKnown || !state.setupJourneyActive',
            javascript,
        )
        self.assertIn('window.sessionStorage.setItem(SETUP_JOURNEY_STORAGE_KEY', javascript)


if __name__ == "__main__":
    unittest.main()
