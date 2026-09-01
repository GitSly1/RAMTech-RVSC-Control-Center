from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from controller import daniel_multi_mission_host as host


class DanielMultiMissionHostTests(unittest.TestCase):
    def mission(self):
        return {
            "agent_id": "DEV-001",
            "wp_id": host.SEM_DANIEL_003_WP,
            "base_branch": host.SEM_DANIEL_003_BASE_BRANCH,
            "work_branch": host.SEM_DANIEL_003_BRANCH,
            "allowed_paths": list(host.SEM_DANIEL_003_ALLOWED),
        }

    def test_003_contract_is_distinct_from_002(self):
        self.assertEqual(host.SEM_DANIEL_003_WP, "SEM-DANIEL-003")
        self.assertNotEqual(host.SEM_DANIEL_003_BRANCH, host.legacy.SEM_DANIEL_BRANCH)
        self.assertEqual(host.SEM_DANIEL_003_ALLOWED, ("interpretation_layer.py", "tests/test_interpretation_layer.py"))

    def test_003_fails_closed_on_branch_mismatch(self):
        mission = self.mission()
        mission["work_branch"] = "wrong"
        with self.assertRaisesRegex(ValueError, "requires branch"):
            host._execute_003("key", mission, "run", "start")

    def test_003_fails_closed_on_allowed_path_mismatch(self):
        mission = self.mission()
        mission["allowed_paths"] = ["interpretation_layer.py"]
        with self.assertRaisesRegex(ValueError, "allowed path contract mismatch"):
            host._execute_003("key", mission, "run", "start")

    def test_003_reuses_controlled_execution_and_restores_002_contract(self):
        mission = self.mission()
        original = (host.legacy.SEM_DANIEL_WP, host.legacy.SEM_DANIEL_BASE_BRANCH, host.legacy.SEM_DANIEL_BRANCH, host.legacy.SEM_DANIEL_ALLOWED)
        with patch.object(host.legacy, "_execute_sem_daniel", return_value={"success": True}) as execute:
            result = host._execute_003("key", mission, "run", "start")
            active = execute.call_args.args[1]
        self.assertTrue(result["success"])
        self.assertEqual(active["wp_id"], "SEM-DANIEL-003")
        self.assertEqual((host.legacy.SEM_DANIEL_WP, host.legacy.SEM_DANIEL_BASE_BRANCH, host.legacy.SEM_DANIEL_BRANCH, host.legacy.SEM_DANIEL_ALLOWED), original)

    def test_non_003_delegates_to_proven_host(self):
        payload = {"protocol": "rvsc.worker.v1", "mission": {"agent_id": "DEV-001", "wp_id": "SEM-DANIEL-002"}}
        with patch.object(host.legacy, "execute_payload", return_value={"success": True, "delegated": True}) as delegated:
            result = host.execute_payload(payload)
        self.assertTrue(result["delegated"])
        delegated.assert_called_once_with(payload)


if __name__ == "__main__":
    unittest.main()
