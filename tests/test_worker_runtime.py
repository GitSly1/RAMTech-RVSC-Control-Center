import unittest
from datetime import datetime, timedelta, timezone

from controller.worker_runtime import (
    Agent, WorkPackage, WorkerExecution, WorkerHealth, WorkerRuntimeError, WorkerSignalType, WorkerState, WatchdogAction,
    block_execution, block_signal, checkpoint_execution, completion_signal, deliver_evidence, dispatch_queue, heartbeat_signal,
    retry_allowed, select_agent, select_qa_agent, start_execution, watchdog_decision,
)

AGENTS = (
    Agent("DEV-001", "Daniel", frozenset({"python", "application_engineering"}), frozenset({"semantiq"})),
    Agent("WEB-001", "Kai", frozenset({"web_extraction", "dom"}), frozenset({"semantiq"})),
    Agent("QA-001", "Quinn", frozenset({"qa", "regression"}), frozenset({"rvsc", "semantiq", "moxie"}), qa_eligible=True),
)


class WorkerRuntimeTests(unittest.TestCase):
    def test_selects_capable_project_worker(self):
        wp = WorkPackage("SEM-101", "semantiq", frozenset({"python"}), "rvsc/SEM-101", ("src/**",), 0)
        self.assertEqual(select_agent(AGENTS, wp).agent_id, "DEV-001")

    def test_rejects_unmatched_capability(self):
        wp = WorkPackage("SEM-102", "semantiq", frozenset({"database"}), "rvsc/SEM-102", ("db/**",), 0)
        with self.assertRaises(WorkerRuntimeError): select_agent(AGENTS, wp)

    def test_execution_moves_to_evidence(self):
        execution = start_execution(WorkerExecution("SEM-101", "DEV-001", WorkerState.ASSIGNED))
        self.assertEqual(execution.attempt, 1)
        execution = deliver_evidence(execution, ["commit:abc", "test:pass"])
        self.assertEqual(execution.state, WorkerState.EVIDENCE_DELIVERED)

    def test_checkpoint_records_accomplishment_and_emits_signal(self):
        execution = start_execution(WorkerExecution("SEM-DANIEL-003", "DEV-001", WorkerState.ASSIGNED))
        execution, signal = checkpoint_execution(execution, "tests_passed", ["validation:unittest:returncode:0"])
        self.assertEqual(signal.signal_type, WorkerSignalType.CHECKPOINT)
        self.assertEqual(heartbeat_signal(execution).checkpoint, "tests_passed")

    def test_completion_requires_delivered_evidence(self):
        execution = start_execution(WorkerExecution("SEM-DANIEL-003", "DEV-001", WorkerState.ASSIGNED))
        execution = deliver_evidence(execution, ["commit:abc", "push:success"])
        self.assertEqual(completion_signal(execution).signal_type, WorkerSignalType.COMPLETION)

    def test_blocked_execution_can_retry_within_policy(self):
        execution = start_execution(WorkerExecution("SEM-101", "DEV-001", WorkerState.ASSIGNED))
        execution = block_execution(execution, "transient runner failure")
        self.assertTrue(retry_allowed(execution, 3))

    def test_stall_signal_preserves_failure_reason(self):
        execution = start_execution(WorkerExecution("SEM-DANIEL-003", "DEV-001", WorkerState.ASSIGNED))
        execution = block_execution(execution, "checkpoint timeout")
        signal = block_signal(execution, stalled=True)
        self.assertEqual(signal.signal_type, WorkerSignalType.STALL)
        self.assertEqual(signal.failure_reason, "checkpoint timeout")

    def test_watchdog_heartbeat_is_not_progress_when_checkpoint_is_stale(self):
        now = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)
        execution = start_execution(WorkerExecution("SEM-DANIEL-003", "DEV-001", WorkerState.ASSIGNED))
        health = WorkerHealth(True, False, True, "SEM-DANIEL-003", "proposal_received", (now - timedelta(minutes=11)).isoformat())
        decision = watchdog_decision(execution, health, now=now, checkpoint_timeout_seconds=600, max_attempts=3)
        self.assertEqual(decision.action, WatchdogAction.STALL)
        self.assertIn("stale", decision.reason)

    def test_watchdog_missing_acknowledgement_retries(self):
        now = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)
        execution = start_execution(WorkerExecution("SEM-DANIEL-003", "DEV-001", WorkerState.ASSIGNED))
        health = WorkerHealth(True, True, False, None, None, now.isoformat())
        decision = watchdog_decision(execution, health, now=now, checkpoint_timeout_seconds=600, max_attempts=3)
        self.assertEqual(decision.action, WatchdogAction.RETRY)
        self.assertIn("acknowledge", decision.reason)

    def test_watchdog_unreachable_worker_escalates_after_attempt_limit(self):
        now = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)
        execution = WorkerExecution("SEM-DANIEL-003", "DEV-001", WorkerState.EXECUTING, attempt=3)
        health = WorkerHealth(False, False, False)
        decision = watchdog_decision(execution, health, now=now, checkpoint_timeout_seconds=600, max_attempts=3)
        self.assertEqual(decision.action, WatchdogAction.ESCALATE)

    def test_watchdog_recent_checkpoint_is_healthy(self):
        now = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)
        execution = start_execution(WorkerExecution("SEM-DANIEL-003", "DEV-001", WorkerState.ASSIGNED))
        health = WorkerHealth(True, False, True, "SEM-DANIEL-003", "tests_passed", (now - timedelta(seconds=30)).isoformat())
        decision = watchdog_decision(execution, health, now=now, checkpoint_timeout_seconds=600, max_attempts=3)
        self.assertEqual(decision.action, WatchdogAction.HEALTHY)

    def test_qa_is_independent_from_implementer(self):
        self.assertEqual(select_qa_agent(AGENTS, "DEV-001", "semantiq").agent_id, "QA-001")

    def test_dispatch_queue_honors_project_priority(self):
        queue = dispatch_queue({"mox": WorkPackage("MOX-002", "moxie", frozenset(), "rvsc/MOX-002", ("docs/**",), 1), "sem": WorkPackage("SEM-003", "semantiq", frozenset(), "rvsc/SEM-003", ("**",), 0)})
        self.assertEqual(tuple(item.wp_id for item in queue), ("SEM-003", "MOX-002"))


if __name__ == "__main__": unittest.main()
