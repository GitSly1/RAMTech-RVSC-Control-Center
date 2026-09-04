import unittest
from unittest import mock

from controller.orchestrator import Mission, MissionState, MissionStore
from controller.runtime_supervisor import RuntimeSupervisor, WorkerConfig


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
        self.assertEqual(store.get("M-1").material_progress_at, 106.0)

    def test_recovery_is_bounded_and_telemetry_exposes_outcome(self):
        clock = Clock()
        store = self.active_store()
        store.record_progress("M-1", timestamp=90, checkpoint="start", evidence=[])
        calls = []
        supervisor = self.make_supervisor(store, clock, {"healthy": True, "agent_id": "DEV-001"}, stall_threshold=5, max_recovery_attempts=1, recovery_handler=lambda _c, _m, attempt: calls.append(attempt) or "restart-requested")
        supervisor.work_control_once()
        supervisor.work_control_once()
        status = supervisor.status_dicts()[0]
        self.assertEqual(calls, [1])
        self.assertEqual(status["recovery_state"], "EXHAUSTED")
        self.assertEqual(status["recovery_attempts"], 1)

    def test_no_work_is_idle_and_eligible_unassigned_work_is_starved(self):
        clock = Clock()
        empty = MissionStore()
        supervisor = self.make_supervisor(empty, clock, {"healthy": False}, starvation_threshold=0)
        self.assertEqual(supervisor.work_control_once()["state"], "IDLE")
        queued = MissionStore()
        queued.add(Mission("M-2", "rvsc"))
        supervisor = self.make_supervisor(queued, clock, {"healthy": False}, starvation_threshold=0)
        result = supervisor.work_control_once()
        self.assertEqual(result["state"], "STARVED")
        self.assertEqual(result["next_eligible_work"], "M-2")

    def test_restart_uses_durable_progress_instead_of_health(self):
        clock = Clock(200)
        store = self.active_store()
        store.record_progress("M-1", timestamp=100, checkpoint="commit", evidence=["abc"])
        supervisor = self.make_supervisor(store, clock, {"healthy": True, "agent_id": "DEV-001"}, stall_threshold=10)
        supervisor.work_control_once()
        status = supervisor.status_dicts()[0]
        self.assertEqual(status["work_state"], "STALLED")
        self.assertEqual(status["last_material_progress"], 100.0)

    def test_independent_qa_is_preserved(self):
        store = MissionStore()
        store.add(Mission("M-3", "rvsc"))
        clock = Clock()
        supervisor = self.make_supervisor(store, clock, {"healthy": True, "agent_id": "DEV-001", "authorized_projects": ["rvsc"]})
        with mock.patch("controller.runtime_supervisor.select_dispatch", return_value=(store.get("M-3"), mock.Mock(worker_id="DEV-001"))):
            result = supervisor.work_control_once()
        self.assertEqual(result["state"], "ACCEPTED")
        self.assertEqual(store.get("M-3").qa_worker, "QA-001")

    def test_rejection_queues_corrective_work_for_next_cycle(self):
        store = MissionStore()
        store.add(Mission("M-4", "rvsc"))
        clock = Clock()
        result = {"qa_status": "QA_REJECTED", "qa_agent_id": "QA-001", "rejection_reason": "regression", "reviewed_commit": "abc"}
        supervisor = self.make_supervisor(store, clock, {"healthy": True, "agent_id": "DEV-001", "authorized_projects": ["rvsc"]}, result=result)
        first = supervisor.work_control_once()
        self.assertEqual(first["state"], "REWORK_QUEUED")
        self.assertEqual(first["next_eligible_work"], "M-4::rework:1")
        self.assertEqual(store.get("M-4").state, MissionState.REJECTED)
        corrective = store.get("M-4::rework:1")
        self.assertEqual(corrective.metadata["originating_mission_id"], "M-4")
        self.assertTrue(store.readiness(corrective.mission_id)[0])

    def test_self_qa_is_blocked_and_does_not_accept(self):
        store = MissionStore()
        store.add(Mission("M-5", "rvsc"))
        supervisor = self.make_supervisor(store, Clock(), {"healthy": True, "agent_id": "DEV-001", "authorized_projects": ["rvsc"]}, result={"qa_status": "QA_ACCEPTED", "qa_agent_id": "DEV-001"})
        outcome = supervisor.work_control_once()
        self.assertEqual(outcome["state"], "BLOCKED")
        self.assertEqual(store.get("M-5").state, MissionState.BLOCKED)

    def test_rework_exhaustion_emits_exception(self):
        store = MissionStore()
        store.add(Mission("M-6", "rvsc"))
        payload = {"healthy": True, "agent_id": "DEV-001", "authorized_projects": ["rvsc"]}
        rejected = {"qa_status": "QA_REJECTED", "qa_agent_id": "QA-001", "reason": "bad"}
        supervisor = self.make_supervisor(store, Clock(), payload, result=rejected, max_rework_attempts=0)
        outcome = supervisor.work_control_once()
        self.assertEqual(outcome["state"], "EXCEPTION")
        self.assertEqual(store.get("M-6").state, MissionState.BLOCKED)
        self.assertEqual(len(store.all()), 1)


if __name__ == "__main__":
    unittest.main()
