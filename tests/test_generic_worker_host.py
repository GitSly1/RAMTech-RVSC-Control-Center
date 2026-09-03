from __future__ import annotations

import unittest
from unittest.mock import patch

from controller.generic_worker_host import RegisteredAgent, execute_payload, is_legacy_daniel_mission


class GenericWorkerHostTests(unittest.TestCase):
    def setUp(self):
        self.daniel = RegisteredAgent("DEV-001", "Daniel", "Development", ("semantiq",), True, False)

    def test_identifies_only_historical_daniel_contracts_as_legacy(self):
        self.assertTrue(is_legacy_daniel_mission({"wp_id": "SEM-DANIEL-002"}))
        self.assertTrue(is_legacy_daniel_mission({"wp_id": " sem-daniel-003 "}))
        self.assertFalse(is_legacy_daniel_mission({"wp_id": "SEM-DANIEL-QUALIFICATION-001"}))
        self.assertFalse(is_legacy_daniel_mission({"wp_id": "SEM-DANIEL-027B-LIVE-QUALIFICATION"}))
        self.assertFalse(is_legacy_daniel_mission({"wp_id": "SEM-1234-ORDINARY"}))
        self.assertFalse(is_legacy_daniel_mission({"wp_id": ""}))

    def test_new_daniel_mission_uses_generic_engineering(self):
        mission = {"agent_id": "DEV-001", "project": "semantiq", "wp_id": "SEM-DANIEL-027B-LIVE-QUALIFICATION", "repository": "GitSly1/RAMTech-SEMANTIQ"}
        engineering = {"success": False, "run_id": "DEV-RUN"}
        with patch("controller.generic_worker_host.configured_agent", return_value=self.daniel), patch("controller.generic_worker_host._set_runtime_state"), patch("controller.generic_worker_host.execute_generic_engineering", return_value=engineering) as generic, patch("controller.generic_worker_host.daniel.execute_payload") as legacy:
            result = execute_payload({"protocol": "rvsc.worker.v1", "mission": mission})
        self.assertEqual(result, engineering)
        generic.assert_called_once()
        legacy.assert_not_called()

    def test_historical_daniel_missions_preserve_specific_handler(self):
        for wp_id in ("SEM-DANIEL-002", "SEM-DANIEL-003"):
            with self.subTest(wp_id=wp_id):
                mission = {"agent_id": "DEV-001", "project": "semantiq", "wp_id": wp_id, "repository": "GitSly1/RAMTech-SEMANTIQ"}
                engineering = {"success": False, "run_id": "LEGACY-RUN"}
                with patch("controller.generic_worker_host.configured_agent", return_value=self.daniel), patch("controller.generic_worker_host._set_runtime_state"), patch("controller.generic_worker_host.daniel.execute_payload", return_value=engineering) as legacy, patch("controller.generic_worker_host.execute_generic_engineering") as generic:
                    result = execute_payload({"protocol": "rvsc.worker.v1", "mission": mission})
                self.assertEqual(result, engineering)
                legacy.assert_called_once()
                generic.assert_not_called()

    def test_authorization_is_checked_before_routing(self):
        mission = {"agent_id": "DEV-001", "project": "rvsc", "wp_id": "RVSC-UNAUTHORIZED"}
        with patch("controller.generic_worker_host.configured_agent", return_value=self.daniel), patch("controller.generic_worker_host.execute_generic_engineering") as generic:
            with self.assertRaisesRegex(ValueError, "not authorized"):
                execute_payload({"protocol": "rvsc.worker.v1", "mission": mission})
        generic.assert_not_called()


if __name__ == "__main__":
    unittest.main()
