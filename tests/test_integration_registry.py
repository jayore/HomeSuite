from __future__ import annotations

from types import SimpleNamespace
import unittest

from integration_registry import SPECS_BY_ID, credentials_for, integration_readiness


class IntegrationRegistryTests(unittest.TestCase):
    def test_alpaca_canonical_credentials_are_configured(self):
        private_config = SimpleNamespace(
            ALPACA_API_KEY_ID="key-id",
            ALPACA_API_SECRET_KEY="secret-key",
        )

        readiness = integration_readiness(
            SPECS_BY_ID["alpaca"],
            private_config,
            environ={},
        )

        self.assertEqual(readiness["status"], "configured")
        self.assertEqual(
            readiness["configured_fields"],
            ["ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"],
        )
        self.assertNotIn("key-id", repr(readiness))
        self.assertNotIn("secret-key", repr(readiness))

    def test_alpaca_standard_aliases_and_environment_are_supported(self):
        private_config = SimpleNamespace()
        environment = {
            "APCA_API_KEY_ID": "environment-key",
            "APCA_API_SECRET_KEY": "environment-secret",
        }

        readiness = integration_readiness(
            SPECS_BY_ID["alpaca"],
            private_config,
            environ=environment,
        )
        credentials = credentials_for(
            "alpaca",
            private_config,
            environ=environment,
        )

        self.assertEqual(readiness["status"], "configured")
        self.assertEqual(
            readiness["resolved_names"],
            {
                "ALPACA_API_KEY_ID": "APCA_API_KEY_ID",
                "ALPACA_API_SECRET_KEY": "APCA_API_SECRET_KEY",
            },
        )
        self.assertEqual(credentials["ALPACA_API_KEY_ID"], "environment-key")
        self.assertEqual(credentials["ALPACA_API_SECRET_KEY"], "environment-secret")

    def test_partial_status_uses_canonical_missing_field(self):
        readiness = integration_readiness(
            SPECS_BY_ID["alpaca"],
            SimpleNamespace(APCA_API_KEY_ID="key-only"),
            environ={},
        )

        self.assertEqual(readiness["status"], "partial")
        self.assertEqual(readiness["missing_fields"], ["ALPACA_API_SECRET_KEY"])


if __name__ == "__main__":
    unittest.main()
