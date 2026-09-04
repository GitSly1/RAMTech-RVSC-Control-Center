import tempfile
import unittest
from pathlib import Path

from controller.orchestrator import (
    Event,
    Mission,
    MissionState,
    MissionStore,
    OrchestrationError,
    WorkerState,
    build_execution_plan,
    dispatch_next,
    dispatch_order,
    select_dispatch,
    worker_signal_event,
)
from controller.worker_runtime import WorkerSignal, WorkerSignalType


CONFIG = {
    "triggers": [
        {"id": "wp_ready", "event": "work_package.status_changed", "when": {"to": "ready"}, "route": "dispatch_work_package"},
        {"id": "worker_checkpoint", "event": "worker.checkpoint", "when": {}, "route": "record_checkpoint"},
        {"id": "worker_completion", "event": "worker.completion", "when": {}, "route": "start_qa"},
        {"id": "worker_stall", "event": "worker.stall", "when": {}, "route": "recover_worker"},
        {"id": "worker_blocked", "event": "worker.blocked", "when": {}, "route": "escalate_blocker"},
        {"id": "qa_pass", "event": "qa.completed", "when": {"outcome": "pass"}, "route": "prepare_merge"},
    ],
    "routes": {
        "dispatch_work_package": {"actions": ["validate_scope", "resolve_agent", "invoke_worker"]},
        "record_checkpoint": {"actions": ["persist_evidence", "update_work_package_activity"]},
        "start_qa": {"actions": ["persist_evidence", "select_qa_agent", "dispatch_qa"]},
        "recover_worker": {"actions": ["diagnose_worker", "retry_or_block"]},
        "escalate_blocker": {"actions": ["persist_failure", "notify_controller_owner"]},
        "prepare_merge": {"actions": ["evaluate_merge_eligibility", "open_or_update_pull_request"]},
    },
}


class OrchestratorRegressionTests(unittest.TestCase):
    def test_ready_event_resolves_dispatch_path(self):
        plan = build_execution_plan(CONFIG, Event("work_package.status_changed", {"to": "ready"}))
        self.assertEqual(plan.trigger_id, "wp_ready")
        self.assertEqual(plan.route, "dispatch_work_package")
        self.assertEqual(plan.actions, ("validate_scope", "resolve_agent", "invoke_worker"))

    def test_worker_checkpoint_becomes_orchestration_event(self):
        signal = WorkerSignal(WorkerSignalType.CHECKPOINT, "SEM-DANIEL-003", "DEV-001", checkpoint="tests_passed", evidence=("tests:pass",))
        event = worker_signal_event(signal)
        self.assertEqual(event.event_type, "worker.checkpoint")
        self.assertEqual(event.payload["wp_id"], "SEM-DANIEL-003")
        self.assertEqual(event.payload["checkpoint"], "tests_passed")
        plan = build_execution_plan(CONFIG, event)
        self.assertEqual(plan.route, "record_checkpoint")
        self.assertIn("persist_evidence", plan.actions)

    def test_worker_completion_routes_to_qa(self):
        signal = WorkerSignal(WorkerSignalType.COMPLETION, "SEM-DANIEL-003", "DEV-001", evidence=("commit:abc",))
        plan = build_execution_plan(CONFIG, worker_signal_event(signal))
        self.assertEqual(plan.route, "start_qa")
        self.assertEqual(plan.actions[-1], "dispatch_qa")

    def test_worker_stall_routes_to_recovery(self):
        signal = WorkerSignal(WorkerSignalType.STALL, "SEM-DANIEL-003", "DEV-001", failure_reason="checkpoint timeout")
        plan = build_execution_plan(CONFIG, worker_signal_event(signal))
        self.assertEqual(plan.route, "recover_worker")
        self.assertEqual(plan.actions, ("diagnose_worker", "retry_or_block"))

    def test_worker_blocked_routes_to_escalation(self):
        signal = WorkerSignal(WorkerSignalType.BLOCKED, "SEM-DANIEL-003", "DEV-001", failure_reason="credential unavailable")
        plan = build_execution_plan(CONFIG, worker_signal_event(signal))
        self.assertEqual(plan.route, "escalate_blocker")

    def test_qa_pass_resolves_merge_path(self):
        plan = build_execution_plan(CONFIG, Event("qa.completed", {"outcome": "pass"}))
        self.assertEqual(plan.route, "prepare_merge")

    def test_unknown_event_is_rejected(self):
        with self.assertRaises(OrchestrationError):
            build_execution_plan(CONFIG, Event("unknown.event", {}))

    def test_project_priority_is_deterministic(self):
        order = dispatch_order({"moxie": {"priority": "P1"}, "semantiq": {"priority": "P0"}, "other": {"priority": "P2"}})
        self.assertEqual(order, ("semantiq", "moxie", "other"))


class MissionStoreTests(unittest.TestCase):
    def test_store_serializes_and_reloads_all_mission_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            store = MissionStore(path)
            mission = Mission(
                "RVSC-2",
                "rvsc",
                dependencies=("RVSC-1",),
                dependency_policy="completed",
                priority="P1",
                metadata={"run_id": "run-2", "evidence": ["scope:checked"]},
            )
            store.add(Mission("RVSC-1", "rvsc", state=MissionState.ACCEPTED, priority=0))
            store.add(mission)
            store.transition("RVSC-2", MissionState.ASSIGNED, worker_id="DEV-001")
            store.save()

            restored = MissionStore.load(path)
            self.assertEqual([item.to_dict() for item in restored.all()], [item.to_dict() for item in store.all()])
            self.assertEqual(restored.get("RVSC-2").implementer, "DEV-001")
            self.assertEqual(restored.get("RVSC-2").metadata["evidence"], ["scope:checked"])

    def test_duplicate_and_self_dependencies_are_rejected(self):
        with self.assertRaises(OrchestrationError):
            Mission("bad", "rvsc", dependencies=("bad",))
        with self.assertRaises(OrchestrationError):
            Mission("bad", "rvsc", dependencies=("one", "one"))

    def test_invalid_and_ambiguous_transitions_are_rejected(self):
        store = MissionStore()
        store.add(Mission("work", "rvsc"))
        with self.assertRaises(OrchestrationError):
            store.transition("work", MissionState.RUNNING, worker_id="DEV-001")
        with self.assertRaises(OrchestrationError):
            store.transition("work", MissionState.QUEUED)
        with self.assertRaises(OrchestrationError):
            store.transition("work", MissionState.BLOCKED)

    def test_valid_lifecycle_distinguishes_assignment_running_and_qa(self):
        store = MissionStore()
        store.add(Mission("work", "rvsc"))
        store.transition("work", MissionState.ASSIGNED, worker_id="DEV-001")
        self.assertEqual(store.get("work").assigned_worker, "DEV-001")
        store.transition("work", MissionState.RUNNING, worker_id="DEV-001")
        self.assertEqual(store.get("work").state, MissionState.RUNNING)
        store.transition("work", MissionState.COMPLETED, worker_id="DEV-001")
        self.assertIsNone(store.get("work").assigned_worker)
        store.transition("work", MissionState.QA_PENDING, worker_id="QA-001")
        store.transition("work", MissionState.ACCEPTED, worker_id="QA-001")
        self.assertEqual(store.get("work").state, MissionState.ACCEPTED)

    def test_implementer_cannot_satisfy_own_qa(self):
        store = MissionStore()
        store.add(Mission("work", "rvsc"))
        store.transition("work", MissionState.ASSIGNED, worker_id="DEV-001")
        store.transition("work", MissionState.RUNNING)
        store.transition("work", MissionState.COMPLETED)
        with self.assertRaises(OrchestrationError):
            store.transition("work", MissionState.QA_PENDING, worker_id="DEV-001")

    def test_blocked_state_preserves_explicit_reason_and_can_requeue(self):
        store = MissionStore()
        store.add(Mission("work", "rvsc"))
        store.transition("work", MissionState.BLOCKED, reason="authorization unavailable")
        self.assertEqual(store.get("work").block_reason, "authorization unavailable")
        store.transition("work", MissionState.QUEUED)
        self.assertIsNone(store.get("work").block_reason)


class DependencyTests(unittest.TestCase):
    def test_dependency_blocks_and_acceptance_unlocks_mission(self):
        store = MissionStore()
        store.add(Mission("first", "rvsc"))
        store.add(Mission("second", "rvsc", dependencies=("first",)))
        self.assertEqual(store.readiness("second"), (False, "dependency_not_accepted:first:queued"))
        first = store.get("first")
        first.state = MissionState.ACCEPTED
        self.assertEqual(store.readiness("second"), (True, None))

    def test_completed_policy_unlocks_before_acceptance(self):
        store = MissionStore()
        store.add(Mission("first", "rvsc", state=MissionState.COMPLETED))
        store.add(Mission("second", "rvsc", dependencies=("first",), dependency_policy="completed"))
        self.assertEqual(store.readiness("second"), (True, None))

    def test_missing_dependency_has_evidence_friendly_reason(self):
        store = MissionStore()
        store.add(Mission("second", "rvsc", dependencies=("missing",)))
        self.assertEqual(store.readiness("second"), (False, "dependency_missing:missing"))


class DispatchTests(unittest.TestCase):
    def test_dispatch_selects_only_authorized_available_worker(self):
        store = MissionStore()
        store.add(Mission("work", "rvsc", priority=0))
        workers = [
            WorkerState("DEV-BUSY", ("rvsc",), active_mission_id="other"),
            WorkerState("DEV-WRONG", ("semantiq",)),
            WorkerState("DEV-RIGHT", ("rvsc",)),
        ]
        decision = select_dispatch(store, workers)
        self.assertEqual((decision.outcome, decision.mission_id, decision.worker_id), ("dispatch", "work", "DEV-RIGHT"))

    def test_dispatch_is_deterministic_by_priority_sequence_and_worker_id(self):
        store = MissionStore()
        store.add(Mission("later", "rvsc", priority=2))
        store.add(Mission("first", "rvsc", priority=1))
        store.add(Mission("second", "rvsc", priority=1))
        workers = [WorkerState("DEV-002", ("rvsc",)), WorkerState("DEV-001", ("rvsc",))]
        decision = select_dispatch(store, workers)
        self.assertEqual((decision.mission_id, decision.worker_id), ("first", "DEV-001"))
        self.assertEqual(select_dispatch(store, reversed(workers)), decision)

    def test_starvation_is_explicit_when_eligible_work_has_no_worker(self):
        store = MissionStore()
        store.add(Mission("work", "rvsc"))
        decision = select_dispatch(store, [WorkerState("DEV-001", ("rvsc",), available=False)])
        self.assertEqual(decision.outcome, "starved")
        self.assertEqual(decision.reason, "eligible_work_has_no_authorized_available_worker")

    def test_dependency_blocked_queue_reports_idle_not_worker_starvation(self):
        store = MissionStore()
        store.add(Mission("work", "rvsc", dependencies=("missing",)))
        decision = select_dispatch(store, [])
        self.assertEqual(decision.outcome, "idle")
        self.assertEqual(decision.reason, "no_eligible_work")

    def test_dispatch_next_records_assignment_and_active_work_is_not_redispatched(self):
        store = MissionStore()
        store.add(Mission("work", "rvsc"))
        worker = WorkerState("DEV-001", ("rvsc",))
        decision = dispatch_next(store, [worker])
        self.assertEqual(decision.outcome, "dispatch")
        self.assertEqual(store.get("work").state, MissionState.ASSIGNED)
        self.assertEqual(store.get("work").assigned_worker, "DEV-001")
        self.assertEqual(select_dispatch(store, [worker]).outcome, "idle")

    def test_healthy_worker_with_active_mission_is_not_available(self):
        store = MissionStore()
        store.add(Mission("work", "rvsc"))
        worker = WorkerState("DEV-001", ("rvsc",), healthy=True, available=True, active_mission_id="other")
        self.assertEqual(select_dispatch(store, [worker]).outcome, "starved")


if __name__ == "__main__":
    unittest.main()
