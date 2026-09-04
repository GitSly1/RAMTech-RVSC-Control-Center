import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from controller.orchestrator import Mission, MissionState, MissionStore, OrchestrationError
from controller.runtime_supervisor import RuntimeSupervisor, WorkerConfig, main, production_mission_store_path


class Clock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


class RuntimeProductivityTests(unittest.TestCase):
    def make_supervisor(self, store, clock, payload, result=None, **kwargs):
        config = WorkerConfig("DEV-001", "Daniel", 8765, "engineering", authorized_projects=("rvsc",))
        response = result or {"qa_status": "QA_ACCEPTED", "qa_agent_id": "QA-001"}
        return RuntimeSupervisor(configs=(config,), mission_store=store, clock=clock, health_checker=lambda _c: payload, port_checker=lambda _p: False, execute_requester=lambda _c, _m: response, **kwargs)

    def active_store(self):
        store = MissionStore()
        store.add(Mission("M-1", "rvsc"))
        store.transition("M-1", MissionState.ASSIGNED, worker_id="DEV-001")
        store.transition("M-1", MissionState.RUNNING, worker_id="DEV-001")
        return store

    def test_identical_health_does_not_advance_material_clock(self):
        clock = Clock()
        store = self.active_store()
        payload = {"healthy": True, "agent_id": "DEV-001", "checkpoint": "build", "evidence": ["commit:a"]}
        supervisor = self.make_supervisor(store, clock, payload, stall_threshold=10)
        supervisor.work_control_once()
        first = store.get("M-1").material_progress_at
        clock.value += 5
        supervisor.work_control_once()
        self.assertEqual(store.get("M-1").material_progress_at, first)
        self.assertEqual(supervisor.status_dicts()[0]["work_state"], "WORKING")

    def test_changed_checkpoint_advances_progress_and_prevents_stall(self):
        clock = Clock()
        store = self.active_store()
        payload = {"healthy": True, "agent_id": "DEV-001", "checkpoint": "build"}
        supervisor = self.make_supervisor(store, clock, payload, stall_threshold=5)
        supervisor.work_control_once()
        clock.value += 6
        self.assertEqual(supervisor.work_control_once()["state"], "IDLE")
        self.assertEqual(supervisor.status_dicts()[0]["work_state"], "STALLED")
        payload["checkpoint"] = "tests"
        supervisor.work_control_once()
        self.assertEqual(supervisor.status_dicts()[0]["work_state"], "PROGRESSING")

    def test_recovery_is_bounded(self):
        clock = Clock()
        store = self.active_store()
        store.record_progress("M-1", timestamp=90, checkpoint="start", evidence=[])
        calls = []
        supervisor = self.make_supervisor(store, clock, {"healthy": True, "agent_id": "DEV-001"}, stall_threshold=5, max_recovery_attempts=1, recovery_handler=lambda _c, _m, attempt: calls.append(attempt) or "restart-requested")
        supervisor.work_control_once()
        supervisor.work_control_once()
        self.assertEqual(calls, [1])
        self.assertEqual(supervisor.status_dicts()[0]["recovery_state"], "EXHAUSTED")

    def test_no_work_is_idle_and_eligible_unassigned_work_is_starved(self):
        empty = MissionStore()
        supervisor = self.make_supervisor(empty, Clock(), {"healthy": False}, starvation_threshold=0)
        self.assertEqual(supervisor.work_control_once()["state"], "IDLE")
        queued = MissionStore()
        queued.add(Mission("M-2", "rvsc"))
        supervisor = self.make_supervisor(queued, Clock(), {"healthy": False}, starvation_threshold=0)
        result = supervisor.work_control_once()
        self.assertEqual(result["state"], "STARVED")
        self.assertEqual(result["next_eligible_work"], "M-2")

    def test_restart_does_not_duplicate_active_durable_mission(self):
        store = self.active_store()
        calls = []
        supervisor = self.make_supervisor(store, Clock(), {"healthy": True, "agent_id": "DEV-001"}, execute_requester=lambda _c, _m: calls.append(1))
        self.assertEqual(supervisor.work_control_once()["state"], "IDLE")
        self.assertEqual(calls, [])
        self.assertEqual(store.get("M-1").state, MissionState.RUNNING)

    def test_queued_work_dispatches_after_supervisor_recreation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missions.json"
            store = MissionStore.load_or_create(path)
            store.add(Mission("M-3", "rvsc"))
            restored = MissionStore.load(path)
            supervisor = self.make_supervisor(restored, Clock(), {"healthy": True, "agent_id": "DEV-001", "authorized_projects": ["rvsc"]})
            self.assertEqual(supervisor.work_control_once()["state"], "ACCEPTED")
            self.assertEqual(MissionStore.load(path).get("M-3").state, MissionState.ACCEPTED)

    def test_independent_qa_and_rework_are_preserved(self):
        store = MissionStore()
        store.add(Mission("M-4", "rvsc"))
        rejected = {"qa_status": "QA_REJECTED", "qa_agent_id": "QA-001", "reason": "bad"}
        supervisor = self.make_supervisor(store, Clock(), {"healthy": True, "agent_id": "DEV-001", "authorized_projects": ["rvsc"]}, result=rejected)
        outcome = supervisor.work_control_once()
        self.assertEqual(outcome["state"], "REWORK_QUEUED")
        self.assertEqual(outcome["next_eligible_work"], "M-4::rework:1")

    def test_self_qa_is_blocked(self):
        store = MissionStore()
        store.add(Mission("M-5", "rvsc"))
        supervisor = self.make_supervisor(store, Clock(), {"healthy": True, "agent_id": "DEV-001", "authorized_projects": ["rvsc"]}, result={"qa_status": "QA_ACCEPTED", "qa_agent_id": "DEV-001"})
        self.assertEqual(supervisor.work_control_once()["state"], "BLOCKED")

    def test_production_path_is_deterministic_and_override_is_preserved(self):
        env = {"RVSC_STATE_DIR": "/state"}
        self.assertEqual(production_mission_store_path(env), Path("/state/RAMTech/RVSC/mission-store.json"))
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "override.json"
            with mock.patch("controller.runtime_supervisor.RuntimeSupervisor.status_dicts", return_value=[]):
                self.assertEqual(main(["status", "--mission-store", str(store_path)]), 0)
            self.assertTrue(store_path.exists())

    def test_cli_add_and_status_expose_queue(self):
        contract = {
            "wp_id": "WP-1", "project": "rvsc", "objective": "Ship bounded work",
            "repository": "GitSly1/repo", "work_branch": "feature", "base_branch": "main",
            "agent_id": "DEV-001", "acceptance_criteria": ["passes"],
            "allowed_paths": ["controller/a.py"],
            "validation_commands": [{"name": "tests", "argv": ["python", "-m", "unittest"]}],
        }
        with tempfile.TemporaryDirectory() as directory:
            mission_file = Path(directory) / "mission.json"
            store_path = Path(directory) / "store.json"
            mission_file.write_text(json.dumps(contract), encoding="utf-8")
            self.assertEqual(main(["add", "--mission-store", str(store_path), "--mission-file", str(mission_file)]), 0)
            restored = MissionStore.load(store_path)
            self.assertEqual(restored.get("WP-1").state, MissionState.QUEUED)
            with self.assertRaises(OrchestrationError):
                restored.add_contract(contract, supported_projects=("rvsc",))


if __name__ == "__main__":
    unittest.main()
