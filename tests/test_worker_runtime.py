import unittest

from controller.worker_runtime import (
    Agent,
    WorkPackage,
    WorkerExecution,
    WorkerRuntimeError,
    WorkerState,
    block_execution,
    deliver_evidence,
    dispatch_queue,
    retry_allowed,
    select_agent,
    select_qa_agent,
    start_execution,
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
        with self.assertRaises(WorkerRuntimeError):
            select_agent(AGENTS, wp)

    def test_execution_moves_to_evidence(self):
        execution = WorkerExecution("SEM-101", "DEV-001", WorkerState.ASSIGNED)
        execution = start_execution(execution)
        self.assertEqual(execution.state, WorkerState.EXECUTING)
        self.assertEqual(execution.attempt, 1)
        execution = deliver_evidence(execution, ["commit:abc", "test:pass"])
        self.assertEqual(execution.state, WorkerState.EVIDENCE_DELIVERED)
        self.assertEqual(len(execution.evidence), 2)

    def test_blocked_execution_can_retry_within_policy(self):
        execution = WorkerExecution("SEM-101", "DEV-001", WorkerState.ASSIGNED)
        execution = start_execution(execution)
        execution = block_execution(execution, "transient runner failure")
        self.assertTrue(retry_allowed(execution, 3))

    def test_qa_is_independent_from_implementer(self):
        qa = select_qa_agent(AGENTS, "DEV-001", "semantiq")
        self.assertEqual(qa.agent_id, "QA-001")

    def test_dispatch_queue_honors_project_priority(self):
        queue = dispatch_queue({
            "mox": WorkPackage("MOX-002", "moxie", frozenset(), "rvsc/MOX-002", ("docs/**",), 1),
            "sem": WorkPackage("SEM-003", "semantiq", frozenset(), "rvsc/SEM-003", ("**",), 0),
        })
        self.assertEqual(tuple(item.wp_id for item in queue), ("SEM-003", "MOX-002"))


if __name__ == "__main__":
    unittest.main()
