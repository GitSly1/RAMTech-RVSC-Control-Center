from __future__ import annotations

import unittest
from unittest.mock import patch

from controller import daniel_multi_mission_host as host


class _RecordingLock:
    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exited += 1
        return False


class DanielMultiMissionHostTests(unittest.TestCase):
    def setUp(self):
        with host._STATE_LOCK:
            host._RUNTIME_STATE.update({
                "active_mission": None,
                "last_run_id": None,
                "last_activity": None,
                "last_result": None,
            })

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

    def test_health_fails_ready_when_credential_missing(self):
        with patch.dict(host.os.environ, {}, clear=True):
            health = host.health_payload()
        self.assertFalse(health["ready"])
        self.assertFalse(health["credential_ready"])
        self.assertFalse(health["busy"])
        self.assertEqual(health["worker"], "DEV-001")
        self.assertIn("SEM-DANIEL-003", health["supported_missions"])

    def test_health_reports_ready_when_credential_present_and_idle(self):
        with patch.dict(host.os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            health = host.health_payload()
        self.assertTrue(health["ready"])
        self.assertTrue(health["credential_ready"])
        self.assertFalse(health["busy"])

    def test_health_reports_busy_active_mission(self):
        host._set_runtime_state(active_mission="SEM-DANIEL-003", last_result="acknowledged")
        with patch.dict(host.os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            health = host.health_payload()
        self.assertFalse(health["ready"])
        self.assertTrue(health["busy"])
        self.assertEqual(health["active_mission"], "SEM-DANIEL-003")
        self.assertEqual(health["last_result"], "acknowledged")
        self.assertIsNotNone(health["last_activity"])

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
        original = (
            host.legacy.SEM_DANIEL_WP,
            host.legacy.SEM_DANIEL_BASE_BRANCH,
            host.legacy.SEM_DANIEL_BRANCH,
            host.legacy.SEM_DANIEL_ALLOWED,
        )
        with patch.object(host.legacy, "_execute_sem_daniel", return_value={"success": True}) as execute:
            result = host._execute_003("key", mission, "run", "start")
            active = execute.call_args.args[1]
        self.assertTrue(result["success"])
        self.assertEqual(active["wp_id"], "SEM-DANIEL-003")
        self.assertEqual(
            (
                host.legacy.SEM_DANIEL_WP,
                host.legacy.SEM_DANIEL_BASE_BRANCH,
                host.legacy.SEM_DANIEL_BRANCH,
                host.legacy.SEM_DANIEL_ALLOWED,
            ),
            original,
        )

    def test_non_003_delegation_uses_shared_execution_lock(self):
        payload = {"protocol": "rvsc.worker.v1", "mission": {"agent_id": "DEV-001", "wp_id": "SEM-DANIEL-002"}}
        lock = _RecordingLock()
        with patch.object(host, "_EXECUTION_LOCK", lock), patch.object(
            host.legacy, "execute_payload", return_value={"success": True, "delegated": True}
        ) as delegated:
            result = host.execute_payload(payload)
        self.assertTrue(result["delegated"])
        delegated.assert_called_once_with(payload)
        self.assertEqual(lock.entered, 1)
        self.assertEqual(lock.exited, 1)

    def test_execution_state_is_cleared_after_003_failure(self):
        payload = {"protocol": "rvsc.worker.v1", "mission": self.mission()}
        with patch.dict(host.os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY is not set"):
                host.execute_payload(payload)
        health = host.health_payload()
        self.assertFalse(health["busy"])
        self.assertIsNone(health["active_mission"])
        self.assertEqual(health["last_result"], "failed")


if __name__ == "__main__":
    unittest.main()
