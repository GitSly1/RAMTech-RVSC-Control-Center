import unittest

from controller.orchestrator import Event, OrchestrationError, build_execution_plan, dispatch_order, worker_signal_event
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


class OrchestratorTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
