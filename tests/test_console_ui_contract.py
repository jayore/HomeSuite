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
        self.assertIn('id="exit-setup-journey"', html)
        self.assertIn('aria-label="Exit setup mode"', html)
        self.assertLess(html.index('id="sidebar"'), html.index('id="setup-journey-bar"'))
        self.assertLess(html.index('id="setup-journey-bar"'), html.index('<header class="topbar">'))
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
            'setupNav.hidden = !statusKnown || (complete && view !== "setup" && !onJourneyView);',
            javascript,
        )
        self.assertIn('$("#review-setup").hidden = !statusKnown || !complete;', javascript)
        self.assertIn(
            '$("#setup-journey-bar").hidden = !statusKnown || !onJourneyView',
            javascript,
        )
        self.assertIn('SETUP_JOURNEY_VIEWS.includes(saved.view)', javascript)
        self.assertIn('view: state.setupJourneyView', javascript)
        self.assertIn('view !== state.setupJourneyView', javascript)
        self.assertIn('$("#exit-setup-journey").addEventListener', javascript)
        self.assertIn('window.sessionStorage.setItem(SETUP_JOURNEY_STORAGE_KEY', javascript)
        self.assertEqual(javascript.count('(!state.setup || !state.setup.complete)'), 2)

    def test_setup_journey_owns_a_responsive_content_row(self):
        stylesheet = (ROOT / "console_static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn(".setup-journey-bar:not([hidden]) ~ .topbar", stylesheet)
        self.assertNotIn(".setup-journey-bar:not([hidden]) ~ .sidebar", stylesheet)
        self.assertIn("--setup-journey-height: 56px", stylesheet)

    def test_wakeword_page_supports_multiple_models_and_upload(self):
        html = (ROOT / "console_static" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "console_static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-view="wakeword"', html)
        self.assertIn('id="view-wakeword"', html)
        self.assertIn("Select one or more", html)
        self.assertIn('id="wakeword-dropzone"', html)
        self.assertIn('id="wakeword-model-file" type="file"', html)
        self.assertIn('accept=".onnx,application/octet-stream"', html)
        self.assertIn('id="wakeword-review-dialog"', html)
        self.assertIn('setAttribute("role", "switch")', javascript)
        self.assertIn('form.append("model", file, file.name)', javascript)
        self.assertIn('opts.body instanceof FormData', javascript)
        self.assertIn('"Active wake words"', javascript)
        self.assertIn('names.join(", ")', javascript)

    def test_wakeword_layout_has_responsive_model_rows(self):
        stylesheet = (ROOT / "console_static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn(".wakeword-model-row", stylesheet)
        self.assertIn(".wakeword-upload-panel.drag-active", stylesheet)
        self.assertIn(".wakeword-summary-grid { grid-template-columns: 1fr; }", stylesheet)
        self.assertIn(".review-dialog .config-review-values { grid-template-columns: 1fr;", stylesheet)

    def test_navigation_separates_system_device_and_tools(self):
        html = (ROOT / "console_static" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "console_static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="nav-system-label" class="nav-group-label">System</span>', html)
        self.assertIn('id="nav-device-label" class="nav-group-label">This device</span>', html)
        self.assertIn('id="nav-tools-label" class="nav-group-label">Tools</span>', html)
        self.assertIn('data-view="settings"', html)
        self.assertIn('id="view-settings"', html)
        self.assertIn('data-view="controls"', html)
        self.assertIn('id="view-controls"', html)
        self.assertIn('<span>Wake word</span>', html)
        self.assertIn('<span>Chat</span>', html)
        self.assertNotIn('data-view="configuration"', html)
        self.assertIn('settings: {', javascript)
        self.assertIn('controls: {', javascript)
        self.assertIn('wakeword: {', javascript)

    def test_chat_is_a_single_live_browser_surface(self):
        html = (ROOT / "console_static" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "console_static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="view-console" class="view" data-title="Chat"', html)
        self.assertIn('class="status-badge configured chat-live-status"', html)
        self.assertNotIn('aria-label="Execution mode"', html)
        self.assertNotIn('class="segmented-control"', html)
        self.assertNotIn('mode: state.mode', javascript)
        self.assertIn('action === "chat"', javascript)
        self.assertIn('activationLabel = "Open Chat";', javascript)
        self.assertIn('element("option", "", "Follow conversation")', javascript)
        self.assertIn('<label for="chat-room">Location</label>', html)

    def test_login_supports_password_managers_and_keyboard_submission(self):
        html = (ROOT / "console_static" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "console_static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('action="/api/login" method="post" autocomplete="on"', html)
        self.assertIn('name="username" type="text" value="homesuite-console" autocomplete="username"', html)
        self.assertIn('name="password" type="password" autocomplete="current-password"', html)
        self.assertIn('id="login-submit"', html)
        self.assertIn('$("#login-form").requestSubmit($("#login-submit"));', javascript)
        self.assertIn('if (state.loginBusy) return;', javascript)

    def test_physical_controls_and_detector_use_shared_config_editor(self):
        html = (ROOT / "console_static" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "console_static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="controls-editor"', html)
        self.assertIn('id="ptt-summary"', html)
        self.assertIn('id="button-summary"', html)
        self.assertIn('id="wakeword-detector-dialog"', html)
        self.assertIn('beginConfigurationEdit("controls")', javascript)
        self.assertIn('beginConfigurationEdit("wakeword")', javascript)
        self.assertIn('sectionData.surface || "settings"', javascript)
        self.assertIn('action: "roles_ptt"', javascript)
        self.assertIn('action: "roles_wakeword"', javascript)
        self.assertIn('window.renderLucideIcons(holder);', javascript)


if __name__ == "__main__":
    unittest.main()
