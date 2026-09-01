import os
import unittest
from unittest.mock import patch

from controller.provider_loader import build_broker_from_environment


class ProviderLoaderTests(unittest.TestCase):
    def test_healthcheck_can_be_required_from_environment(self):
        env = {
            "RVSC_PROVIDER_NAME": "daniel",
            "RVSC_PROVIDER_ENDPOINT": "http://127.0.0.1:8768/execute",
            "RVSC_PROVIDER_REQUIRE_HEALTHCHECK": "true",
        }
        with patch.dict(os.environ, env, clear=True):
            broker = build_broker_from_environment()
        adapter = broker._providers["daniel"]
        self.assertTrue(adapter.config.require_healthcheck)

    def test_healthcheck_defaults_off_for_legacy_providers(self):
        env = {
            "RVSC_PROVIDER_NAME": "legacy",
            "RVSC_PROVIDER_ENDPOINT": "https://worker.example/execute",
        }
        with patch.dict(os.environ, env, clear=True):
            broker = build_broker_from_environment()
        adapter = broker._providers["legacy"]
        self.assertFalse(adapter.config.require_healthcheck)


if __name__ == "__main__":
    unittest.main()
