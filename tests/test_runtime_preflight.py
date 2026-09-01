from __future__ import annotations

import unittest
from unittest import mock

from controller.runtime_preflight import Check, collect_checks, required_ready


class RuntimePreflightTests(unittest.TestCase):
    def test_required_ready_ignores_optional_failures(self) -> None:
        checks = [
            Check("required", "PASS", "ok", required=True),
            Check("optional", "FAIL", "not fatal", required=False),
        ]
        self.assertTrue(required_ready(checks))

    def test_required_failure_blocks_readiness(self) -> None:
        self.assertFalse(required_ready([Check("credential", "FAIL", "missing")]))

    @mock.patch("controller.runtime_preflight._tcp_open", return_value=False)
    @mock.patch.dict("controller.runtime_preflight.os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False)
    def test_free_daniel_port_is_ready_not_failure(self, _tcp: mock.Mock) -> None:
        checks = collect_checks("127.0.0.1", 8768)
        endpoint = next(check for check in checks if check.name == "Daniel endpoint")
        self.assertEqual(endpoint.status, "READY")
        self.assertTrue(endpoint.ok)

    @mock.patch("controller.runtime_preflight._tcp_open", return_value=True)
    @mock.patch.dict("controller.runtime_preflight.os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False)
    def test_existing_daniel_endpoint_is_running(self, _tcp: mock.Mock) -> None:
        checks = collect_checks("127.0.0.1", 8768)
        endpoint = next(check for check in checks if check.name == "Daniel endpoint")
        self.assertEqual(endpoint.status, "RUNNING")


if __name__ == "__main__":
    unittest.main()
