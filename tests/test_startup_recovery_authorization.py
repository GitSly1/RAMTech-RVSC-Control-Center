from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from controller import generic_worker_host as host
from controller.orchestrator import MissionState
from controller.runtime_supervisor import RuntimeSupervisor


class StartupRecoveryAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        with host._STATE_LOCK:
            self.original_state = dict(host._RUNTIME_STATE)

    def tearDown(self) -> None:
        with host._STATE_LOCK:
            host._RUNTIME_STATE.clear()
            host._RUNTIME_STATE.update(self.original_state)

    def _install_recovery_state(self) -> None:
        with host._STATE_LOCK:
            host._RUNTIME_STATE.update(
                {
                    "active_mission": "WP-RECOVERY-1",
                    "active_run_id": "RUN-RECOVERY-1",
                    "last_run_id": "RUN-RECOVERY-1",
                    "last_result": "working",
                    "last_checkpoint": "implementation_applied",
                    "checkpoint_evidence": (),
                    "recovery_required": True,
                    "recovered_checkpoint": "implementation_applied",
                    "lifecycle_state": "recovery_required",
                    "recovery_context": {
                        "wp_id": "WP-RECOVERY-1",
                        "run_id": "RUN-RECOVERY-1",
                    },
                    "recovery_digest": "test-digest",
                    "recovery_attempted": False,
                    "engineering_result": {"success": True},
                    "qa_dispatch_started": False,
                    "terminal_recovery": None,
                }
            )

    @staticmethod
    def _worker():
        return SimpleNamespace(
            agent_id="DEV-001",
            name="Daniel",
            role="engineering",
        )

    @staticmethod
    def _mission(state: MissionState, assigned_worker: str = "DEV-001"):
        return SimpleNamespace(
            state=state,
            assigned_worker=assigned_worker,
        )

    def _authorize_with_mission(self, mission):
        store = Mock()
        store.get.return_value = mission

        with patch.dict(
            os.environ,
            {"RVSC_MISSION_STORE_PATH": r"D:\RAMTech\Runtime\mission-store.json"},
            clear=False,
        ), patch.object(
            host.MissionStore,
            "load",
            return_value=store,
        ), patch(
            "controller.generic_worker_host.configured_agent",
            return_value=self._worker(),
        ), patch(
            "controller.generic_worker_host._persist_runtime_state"
        ):
            return host._automatic_recovery_authorization()

    def test_running_mission_assigned_to_same_worker_authorizes_recovery(self):
        self._install_recovery_state()

        decision = self._authorize_with_mission(
            self._mission(MissionState.RUNNING)
        )

        self.assertEqual(decision, "authorized")
        self.assertTrue(host._RUNTIME_STATE["recovery_required"])
        self.assertEqual(
            host._RUNTIME_STATE["active_mission"],
            "WP-RECOVERY-1",
        )

    def test_non_running_authoritative_states_suppress_stale_recovery(self):
        states = (
            MissionState.QUEUED,
            MissionState.BLOCKED,
            MissionState.ASSIGNED,
            MissionState.COMPLETED,
            MissionState.QA_PENDING,
            MissionState.ACCEPTED,
            MissionState.REJECTED,
        )

        for state in states:
            with self.subTest(state=state.value):
                self._install_recovery_state()

                decision = self._authorize_with_mission(
                    self._mission(state)
                )

                self.assertEqual(decision, "reconciled")
                self.assertFalse(
                    host._RUNTIME_STATE["recovery_required"]
                )
                self.assertIsNone(
                    host._RUNTIME_STATE["active_mission"]
                )
                self.assertEqual(
                    host._RUNTIME_STATE["lifecycle_state"],
                    "idle",
                )
                self.assertEqual(
                    host._RUNTIME_STATE["last_checkpoint"],
                    "recovery_not_authorized",
                )
                self.assertIn(
                    f"authoritative_mission_state:{state.value}",
                    host._RUNTIME_STATE["checkpoint_evidence"],
                )

    def test_missing_authoritative_store_path_fails_closed(self):
        self._install_recovery_state()

        with patch.dict(os.environ, {}, clear=False), patch(
            "controller.generic_worker_host._persist_runtime_state"
        ):
            os.environ.pop("RVSC_MISSION_STORE_PATH", None)
            decision = host._automatic_recovery_authorization()

        self.assertEqual(decision, "failed")
        self.assertTrue(host._RUNTIME_STATE["recovery_required"])
        self.assertEqual(
            host._RUNTIME_STATE["lifecycle_state"],
            "recovery_failed",
        )
        self.assertEqual(
            host._RUNTIME_STATE["last_checkpoint"],
            "recovery_authorization_failed",
        )

    def test_malformed_or_unavailable_authoritative_store_fails_closed(self):
        self._install_recovery_state()

        with patch.dict(
            os.environ,
            {"RVSC_MISSION_STORE_PATH": r"D:\bad\mission-store.json"},
            clear=False,
        ), patch.object(
            host.MissionStore,
            "load",
            side_effect=ValueError("malformed mission store"),
        ), patch(
            "controller.generic_worker_host._persist_runtime_state"
        ):
            decision = host._automatic_recovery_authorization()

        self.assertEqual(decision, "failed")
        self.assertEqual(
            host._RUNTIME_STATE["lifecycle_state"],
            "recovery_failed",
        )
        self.assertTrue(host._RUNTIME_STATE["recovery_required"])

    def test_running_mission_assigned_to_other_worker_fails_closed(self):
        self._install_recovery_state()

        decision = self._authorize_with_mission(
            self._mission(
                MissionState.RUNNING,
                assigned_worker="OPS-001",
            )
        )

        self.assertEqual(decision, "failed")
        self.assertTrue(host._RUNTIME_STATE["recovery_required"])
        self.assertEqual(
            host._RUNTIME_STATE["lifecycle_state"],
            "recovery_failed",
        )

    def test_supervisor_propagates_exact_authoritative_mission_store_path(self):
        mission_store = SimpleNamespace(
            path=Path(r"D:\RAMTech\Runtime\mission-store.json")
        )
        supervisor = RuntimeSupervisor(
            mission_store=mission_store,
        )

        _command, env = supervisor.build_launch(
            supervisor.configs[1]
        )

        self.assertEqual(
            env["RVSC_MISSION_STORE_PATH"],
            r"D:\RAMTech\Runtime\mission-store.json",
        )

    def test_main_does_not_start_recovery_when_authorization_fails(self):
        self._install_recovery_state()

        server = Mock()
        worker = self._worker()

        with patch(
            "controller.generic_worker_host.configured_agent",
            return_value=worker,
        ), patch(
            "controller.generic_worker_host._restore_runtime_state",
            return_value=True,
        ), patch(
            "controller.generic_worker_host._automatic_recovery_authorization",
            return_value="failed",
        ), patch(
            "controller.generic_worker_host.ThreadingHTTPServer",
            return_value=server,
        ), patch(
            "controller.generic_worker_host._start_automatic_recovery"
        ) as start_recovery:
            host.main()

        start_recovery.assert_not_called()
        server.serve_forever.assert_called_once_with()

    def test_main_starts_recovery_only_after_authorization_passes(self):
        self._install_recovery_state()

        server = Mock()
        worker = self._worker()

        with patch(
            "controller.generic_worker_host.configured_agent",
            return_value=worker,
        ), patch(
            "controller.generic_worker_host._restore_runtime_state",
            return_value=True,
        ), patch(
            "controller.generic_worker_host._automatic_recovery_authorization",
            return_value="authorized",
        ), patch(
            "controller.generic_worker_host.ThreadingHTTPServer",
            return_value=server,
        ), patch(
            "controller.generic_worker_host._start_automatic_recovery"
        ) as start_recovery:
            host.main()

        start_recovery.assert_called_once_with()
        server.serve_forever.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
