import os
import subprocess
import sys
import unittest
from unittest import mock

from controller.runtime_supervisor import (
    DEFAULT_QA_ENDPOINT,
    QA_ROUTING_ENV_KEYS,
    RuntimeConflictError,
    RuntimeSupervisor,
    WorkerConfig,
    golden_team_configs,
)


class FakeProcess:
    next_pid = 1000

    def __init__(self, command, env):
        self.command = command
        self.env = env
        self.return_code = None
        self.terminated = False
        self.killed = False
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1

    def poll(self):
        return self.return_code

    def terminate(self):
        self.terminated = True
        self.return_code = 0

    def kill(self):
        self.killed = True
        self.return_code = -9

    def wait(self, timeout=None):
        if self.return_code is None:
            raise subprocess.TimeoutExpired(self.command, timeout)
        return self.return_code


class Harness:
    def __init__(self):
        self.processes = []
        self.health = {}
        self.open_ports = set()

    def process_factory(self, command, env):
        process = FakeProcess(command, env)
        self.processes.append(process)
        return process

    def health_checker(self, config):
        return self.health.get(config.agent_id, {"healthy": False})

    def port_checker(self, port):
        return port in self.open_ports

    def supervisor(self, **kwargs):
        return RuntimeSupervisor(
            process_factory=self.process_factory,
            health_checker=self.health_checker,
            port_checker=self.port_checker,
            **kwargs
        )


class FakeStore:
    def __init__(self, missions):
        self.records = {item["mission_id"]: dict(item) for item in missions}
        self.transitions = []

    def list_missions(self):
        return [dict(item) for item in self.records.values()]

    def transition(self, mission_id, new_state, worker_id=None, evidence=None):
        record = self.records[mission_id]
        record["status"] = getattr(new_state, "value", new_state)
        if worker_id:
            record["assigned_worker_id"] = worker_id
        record.setdefault("evidence", []).append(dict(evidence or {}))
        self.transitions.append((mission_id, str(record["status"]).lower(), worker_id))
        return dict(record)


class RuntimeSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()

    def test_golden_team_identity_and_ports_are_fixed(self):
        configs = golden_team_configs()
        self.assertEqual(
            [(item.agent_id, item.name, item.port) for item in configs],
            [("OPS-001", "Noah", 8770), ("DEV-001", "Daniel", 8765), ("QA-001", "Quinn", 8771)],
        )

    def test_launch_propagates_identity_repositories_and_qa_endpoint(self):
        endpoint = "http://qa.internal:8771/execute"
        supervisor = self.harness.supervisor(
            repository_mappings={
                "RVSC_RVSC_REPO": "/repos/rvsc",
                "RVSC_SEMANTIQ_REPO": "/repos/semantiq",
                "RVSC_MOXIE_REPO": "/repos/moxie",
            },
            qa_endpoint=endpoint,
        )
        supervisor.start("OPS-001")
        process = self.harness.processes[0]
        self.assertEqual(process.command, [sys.executable, "-m", "controller.generic_worker_host"])
        self.assertEqual(process.env["RVSC_WORKER_AGENT_ID"], "OPS-001")
        self.assertEqual(process.env["RVSC_WORKER_PORT"], "8770")
        self.assertEqual(process.env["RVSC_RVSC_REPO"], "/repos/rvsc")
        for key in QA_ROUTING_ENV_KEYS:
            self.assertEqual(process.env.get(key), endpoint)

    def test_default_qa_endpoint_is_execute_endpoint(self):
        supervisor = self.harness.supervisor()
        _, environment = supervisor.build_launch("DEV-001")
        self.assertEqual(DEFAULT_QA_ENDPOINT, "http://127.0.0.1:8771/execute")
        for key in QA_ROUTING_ENV_KEYS:
            self.assertEqual(environment.get(key), DEFAULT_QA_ENDPOINT)

    def test_qa_launch_removes_inherited_routing(self):
        inherited = {key: "http://inherited.invalid/execute" for key in QA_ROUTING_ENV_KEYS}
        inherited["RVSC_TEST_CREDENTIAL"] = "sensitive-test-value"
        with mock.patch.dict(os.environ, inherited, clear=False):
            supervisor = self.harness.supervisor()
            _, environment = supervisor.build_launch("QA-001")
        for key in QA_ROUTING_ENV_KEYS:
            self.assertNotIn(key, environment)
        self.assertIn("RVSC_TEST_CREDENTIAL", environment)

    def test_matching_healthy_worker_is_not_duplicated_or_owned(self):
        self.harness.health["DEV-001"] = {"status": "healthy", "agent_id": "DEV-001"}
        self.harness.open_ports.add(8765)
        supervisor = self.harness.supervisor()
        status = supervisor.start("DEV-001")
        self.assertEqual(self.harness.processes, [])
        self.assertTrue(status.running)
        self.assertTrue(status.ready)
        self.assertFalse(status.supervisor_owned)
        self.assertFalse(supervisor.stop("DEV-001"))

    def test_identity_conflict_fails_closed(self):
        self.harness.health["OPS-001"] = {"status": "healthy", "agent_id": "UNKNOWN-001"}
        self.harness.open_ports.add(8770)
        with self.assertRaises(RuntimeConflictError):
            self.harness.supervisor().start("OPS-001")
        self.assertEqual(self.harness.processes, [])

    def test_unknown_listener_fails_closed(self):
        self.harness.open_ports.add(8771)
        with self.assertRaises(RuntimeConflictError):
            self.harness.supervisor().start("QA-001")
        self.assertEqual(self.harness.processes, [])

    def test_controlled_stop_only_terminates_owned_process(self):
        supervisor = self.harness.supervisor()
        supervisor.start("OPS-001")
        process = self.harness.processes[0]
        self.assertTrue(supervisor.stop("OPS-001"))
        self.assertTrue(process.terminated)
        self.assertFalse(supervisor.stop("OPS-001"))

    def test_unexpected_exit_restarts_only_up_to_bound(self):
        supervisor = self.harness.supervisor(max_restarts=2)
        supervisor.start("DEV-001")
        for expected in (2, 3):
            self.harness.processes[-1].return_code = 1
            supervisor.poll_once()
            self.assertEqual(len(self.harness.processes), expected)
        self.harness.processes[-1].return_code = 1
        statuses = supervisor.poll_once()
        self.assertEqual(len(self.harness.processes), 3)
        daniel = next(item for item in statuses if item.agent_id == "DEV-001")
        self.assertEqual(daniel.restart_count, 2)
        self.assertEqual(daniel.health_result.get("supervisor_error"), "restart limit reached")

    def test_intentional_stop_does_not_restart(self):
        supervisor = self.harness.supervisor(max_restarts=3)
        supervisor.start("QA-001")
        supervisor.stop("QA-001")
        supervisor.poll_once()
        self.assertEqual(len(self.harness.processes), 1)

    def test_consolidated_status_contains_all_workers(self):
        self.harness.health["QA-001"] = {"ready": True, "agent_id": "QA-001"}
        self.harness.open_ports.add(8771)
        statuses = self.harness.supervisor().status_dicts()
        self.assertEqual(len(statuses), 3)
        quinn = next(item for item in statuses if item["agent_id"] == "QA-001")
        self.assertTrue(quinn["running"])
        self.assertTrue(quinn["ready"])
        self.assertIn("work_state", quinn)

    def test_start_all_rolls_back_only_processes_started_by_supervisor(self):
        self.harness.open_ports.add(8765)
        supervisor = self.harness.supervisor()
        with self.assertRaises(RuntimeConflictError):
            supervisor.start_all()
        self.assertEqual(len(self.harness.processes), 1)
        self.assertTrue(self.harness.processes[0].terminated)

    def _work_supervisor(self, store, result=None, failure=None):
        config = WorkerConfig("DEV-001", "Daniel", 8765, "engineering", authorized_projects=("rvsc",))
        self.harness.health["DEV-001"] = {
            "healthy": True,
            "agent_id": "DEV-001",
            "authorized_projects": ["rvsc"],
        }

        def execute(_config, _mission):
            if failure:
                raise failure
            return result

        return self.harness.supervisor(
            configs=(config,), mission_store=store, execute_requester=execute
        )

    @mock.patch("controller.runtime_supervisor.select_dispatch")
    def test_automatic_selection_persists_before_dispatch_and_accepts(self, select):
        mission = {"mission_id": "M-1", "project": "rvsc", "status": "queued", "dependencies": []}
        store = FakeStore([mission])
        select.side_effect = lambda missions, workers: (missions[0], workers[0])
        observed = []

        def execute(_config, _mission):
            observed.extend(state for _, state, _ in store.transitions)
            return {"result": "complete", "qa_status": "QA_ACCEPTED", "qa_agent_id": "QA-001"}

        supervisor = self._work_supervisor(store)
        supervisor._execute_requester = execute
        status = supervisor.work_control_once()
        self.assertEqual(observed[:2], ["assigned", "running"])
        self.assertEqual(status["state"], "ACCEPTED")
        self.assertEqual(store.records["M-1"]["status"], "accepted")

    @mock.patch("controller.runtime_supervisor.select_dispatch")
    def test_qa_rejection_is_durable(self, select):
        mission = {"mission_id": "M-2", "project": "rvsc", "status": "queued"}
        store = FakeStore([mission])
        select.side_effect = lambda missions, workers: (missions[0], workers[0])
        status = self._work_supervisor(store, {"qa_status": "QA_REJECTED"}).work_control_once()
        self.assertEqual(status["state"], "REJECTED")
        self.assertEqual(store.records["M-2"]["status"], "rejected")

    @mock.patch("controller.runtime_supervisor.select_dispatch")
    def test_transport_failure_is_durably_blocked(self, select):
        mission = {"mission_id": "M-3", "project": "rvsc", "status": "queued"}
        store = FakeStore([mission])
        select.side_effect = lambda missions, workers: (missions[0], workers[0])
        status = self._work_supervisor(store, failure=OSError("offline")).work_control_once()
        self.assertEqual(status["state"], "BLOCKED")
        self.assertEqual(store.records["M-3"]["status"], "blocked")
        self.assertTrue(store.records["M-3"]["evidence"][-1]["retryable"])

    @mock.patch("controller.runtime_supervisor.select_dispatch", return_value=None)
    def test_starvation_is_exposed_when_no_worker_can_execute(self, _select):
        mission = {"mission_id": "M-4", "project": "rvsc", "status": "queued"}
        store = FakeStore([mission])
        supervisor = self.harness.supervisor(mission_store=store)
        status = supervisor.work_control_once()
        self.assertEqual(status["state"], "STARVED")
        self.assertIn("M-4", status["queued_ready"])

    @mock.patch("controller.runtime_supervisor.select_dispatch", return_value=None)
    def test_active_assignment_prevents_duplicate_dispatch(self, _select):
        mission = {
            "mission_id": "M-5",
            "project": "rvsc",
            "status": "running",
            "assigned_worker_id": "DEV-001",
        }
        store = FakeStore([mission])
        supervisor = self._work_supervisor(store, {"qa_status": "QA_ACCEPTED"})
        status = supervisor.work_control_once()
        self.assertEqual(status["state"], "IDLE")
        self.assertEqual(store.transitions, [])
        worker = supervisor.status_dicts()[0]
        self.assertEqual(worker["work_state"], "RUNNING")
        self.assertEqual(worker["mission_id"], "M-5")

    @mock.patch("controller.runtime_supervisor.select_dispatch")
    def test_worker_cannot_accept_its_own_qa(self, select):
        mission = {"mission_id": "M-6", "project": "rvsc", "status": "queued"}
        store = FakeStore([mission])
        select.side_effect = lambda missions, workers: (missions[0], workers[0])
        result = {"qa_status": "QA_ACCEPTED", "qa_agent_id": "DEV-001"}
        status = self._work_supervisor(store, result).work_control_once()
        self.assertEqual(status["state"], "REJECTED")
        self.assertEqual(store.records["M-6"]["status"], "rejected")

    def test_legacy_operation_does_not_require_store(self):
        supervisor = self.harness.supervisor()
        self.assertEqual(supervisor.work_control_status["state"], "DISABLED")
        supervisor.poll_once()
        self.assertEqual(len(supervisor.status()), 3)


if __name__ == "__main__":
    unittest.main()
