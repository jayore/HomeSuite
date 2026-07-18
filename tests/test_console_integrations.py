from __future__ import annotations

import io
from types import SimpleNamespace
import unittest

from console_integrations import ConsoleIntegrationManager, IntegrationConnectionTester


class _Editor:
    def __init__(self, fields):
        self.fields = fields

    def public_state(self, *, include_secrets=False):
        rows = []
        for field in self.fields:
            row = dict(field)
            if row.get("secret") and not include_secrets:
                row["value"] = None
            rows.append(row)
        return {"fields": rows, "revisions": {"private_config.py": "rev"}}


class _Response:
    def __init__(self, status, body=b""):
        self.status_code = status
        self.raw = io.BytesIO(body)

    def close(self):
        return None


class _Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


class ConsoleIntegrationManagerTests(unittest.TestCase):
    def test_public_state_uses_current_editor_values_instead_of_stale_module_values(self):
        fields = [
            {"key": "ALPACA_API_KEY_ID", "label": "API key ID", "configured": True, "value": "new", "secret": False},
            {"key": "ALPACA_API_SECRET_KEY", "label": "API secret", "configured": True, "value": "secret", "secret": True},
        ]
        manager = ConsoleIntegrationManager(
            editor=_Editor(fields),
            private_config=SimpleNamespace(ALPACA_API_KEY_ID="", ALPACA_API_SECRET_KEY=""),
            app_config=SimpleNamespace(),
            environ={},
            tester=SimpleNamespace(supports=lambda _value: True),
        )

        alpaca = next(row for row in manager.public_state()["integrations"] if row["id"] == "alpaca")

        self.assertEqual(alpaca["status"], "configured")
        self.assertNotIn("new", repr(alpaca))
        self.assertNotIn("secret", repr(alpaca))

    def test_edit_state_returns_only_provider_fields_with_authenticated_values(self):
        fields = [
            {"key": "HA_URL", "label": "URL", "configured": True, "value": "http://ha", "secret": False},
            {"key": "HA_TOKEN", "label": "Token", "configured": True, "value": "token", "secret": True},
            {"key": "OPENAI_API_KEY", "label": "OpenAI", "configured": True, "value": "other", "secret": True},
        ]
        manager = ConsoleIntegrationManager(
            editor=_Editor(fields),
            private_config=SimpleNamespace(),
            app_config=SimpleNamespace(),
            environ={},
            tester=SimpleNamespace(supports=lambda _value: True),
        )

        state = manager.edit_state("home_assistant")

        self.assertEqual([field["key"] for field in state["fields"]], ["HA_URL", "HA_TOKEN"])
        self.assertEqual(state["fields"][1]["value"], "token")
        self.assertNotIn("other", repr(state))

    def test_youtube_editor_includes_device_owned_refresh_behavior(self):
        fields = [
            {"key": "YOUTUBE_OAUTH_CLIENT_ID", "label": "Client", "configured": True, "value": "client", "secret": False},
            {"key": "YOUTUBE_OAUTH_CLIENT_SECRET", "label": "Secret", "configured": True, "value": "secret", "secret": True},
            {"key": "YOUTUBE_OAUTH_REFRESH_TOKEN", "label": "Token", "configured": True, "value": "token", "secret": True},
            {
                "key": "YOUTUBE_REEL_REFRESH_ENABLED",
                "label": "Refresh generated playlists",
                "configured": True,
                "value": False,
                "secret": False,
                "target_file": "local_prefs.py",
                "can_reset": True,
            },
        ]
        manager = ConsoleIntegrationManager(
            editor=_Editor(fields),
            private_config=SimpleNamespace(),
            app_config=SimpleNamespace(),
            environ={},
            tester=SimpleNamespace(supports=lambda _value: True),
        )

        state = manager.edit_state("youtube")

        self.assertEqual(
            [field["key"] for field in state["fields"]],
            [
                "YOUTUBE_OAUTH_CLIENT_ID",
                "YOUTUBE_OAUTH_CLIENT_SECRET",
                "YOUTUBE_OAUTH_REFRESH_TOKEN",
                "YOUTUBE_REEL_REFRESH_ENABLED",
            ],
        )


class IntegrationConnectionTesterTests(unittest.TestCase):
    def test_home_assistant_test_uses_bearer_token_without_returning_it(self):
        session = _Session(_Response(200, b'{}'))
        tester = IntegrationConnectionTester(
            session=session,
            app_config=SimpleNamespace(),
        )

        result = tester.test("home_assistant", {"HA_URL": "http://ha.local:8123", "HA_TOKEN": "very-secret"})

        self.assertEqual(result["status"], "success")
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("GET", "http://ha.local:8123/api/"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer very-secret")
        self.assertNotIn("very-secret", repr(result))

    def test_provider_error_is_specific_but_does_not_echo_credentials_or_response(self):
        session = _Session(_Response(401, b'{"error":"secret-value"}'))
        tester = IntegrationConnectionTester(
            session=session,
            app_config=SimpleNamespace(),
        )

        result = tester.test("openai", {"OPENAI_API_KEY": "secret-value"})

        self.assertEqual(result["status"], "failed")
        self.assertIn("rejected", result["detail"])
        self.assertNotIn("secret-value", repr(result))


if __name__ == "__main__":
    unittest.main()
