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


class RuntimeSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()

    def test_golden_team_identity_and_ports_are_fixed(self):
        configs = golden_team_configs()
        self.assertEqual(
            [(item.agent_id, item.name, item.port) for item in configs],
            [
                ("OPS-001", "Noah", 8770),
                ("DEV-001", "Daniel", 8765),
                ("QA-001", "Quinn", 8771),
            ],
        )

    def test_launch_propagates_identity_repositories_and_qa_endpoint(self):
        qa_endpoint = "http://qa.internal:8771/execute"
        supervisor = self.harness.supervisor(
            repository_mappings={
                "RVSC_RVSC_REPO": "/repos/rvsc",
                "RVSC_SEMANTIQ_REPO": "/repos/semantiq",
                "RVSC_MOXIE_REPO": "/repos/moxie",
            },
            qa_endpoint=qa_endpoint,
        )
        supervisor.start("OPS-001")
        process = self.harness.processes[0]
        self.assertEqual(
            process.command,
            [sys.executable, "-m", "controller.generic_worker_host"],
        )
        self.assertNotIn("--agent-id", process.command)
        self.assertNotIn("--port", process.command)
        self.assertEqual(process.env["RVSC_WORKER_AGENT_ID"], "OPS-001")
        self.assertEqual(process.env["RVSC_WORKER_PORT"], "8770")
        self.assertEqual(process.env["RVSC_AGENT_ID"], "OPS-001")
        self.assertEqual(process.env["RVSC_RVSC_REPO"], "/repos/rvsc")
        self.assertEqual(process.env["RVSC_SEMANTIQ_REPO"], "/repos/semantiq")
        self.assertEqual(process.env["RVSC_MOXIE_REPO"], "/repos/moxie")
        for key in QA_ROUTING_ENV_KEYS:
            self.assertEqual(process.env.get(key), qa_endpoint, key)

    def test_default_qa_endpoint_is_execute_endpoint(self):
        supervisor = self.harness.supervisor()
        _, environment = supervisor.build_launch("DEV-001")
        self.assertEqual(DEFAULT_QA_ENDPOINT, "http://127.0.0.1:8771/execute")
        for key in QA_ROUTING_ENV_KEYS:
            self.assertEqual(environment.get(key), DEFAULT_QA_ENDPOINT, key)

    def test_qa_launch_removes_inherited_routing_and_preserves_other_environment(self):
        inherited = {key: "http://inherited.invalid/execute" for key in QA_ROUTING_ENV_KEYS}
        inherited["RVSC_TEST_CREDENTIAL"] = "sensitive-test-value"
        with mock.patch.dict(os.environ, inherited, clear=False):
            supervisor = self.harness.supervisor(
                repository_mappings={"RVSC_RVSC_REPO": "/repos/rvsc"}
            )
            _, environment = supervisor.build_launch("QA-001")

        for key in QA_ROUTING_ENV_KEYS:
            self.assertNotIn(key, environment, key)
        self.assertIn("RVSC_TEST_CREDENTIAL", environment)
        self.assertEqual(environment.get("RVSC_RVSC_REPO"), "/repos/rvsc")
        self.assertEqual(environment.get("RVSC_AGENT_ROLE"), "qa")
        self.assertEqual(environment.get("RVSC_WORKER_AGENT_ID"), "QA-001")
        self.assertEqual(environment.get("RVSC_WORKER_PORT"), "8771")

    def test_matching_healthy_worker_is_not_duplicated_or_owned(self):
        self.harness.health["DEV-001"] = {
            "status": "healthy",
            "agent_id": "DEV-001",
        }
        self.harness.open_ports.add(8765)
        supervisor = self.harness.supervisor()
        status = supervisor.start("DEV-001")
        self.assertEqual(self.harness.processes, [])
        self.assertTrue(status.running)
        self.assertTrue(status.ready)
        self.assertFalse(status.supervisor_owned)
        self.assertFalse(supervisor.stop("DEV-001"))

    def test_identity_conflict_fails_closed(self):
        self.harness.health["OPS-001"] = {
            "status": "healthy",
            "agent_id": "UNKNOWN-001",
        }
        self.harness.open_ports.add(8770)
        supervisor = self.harness.supervisor()
        with self.assertRaises(RuntimeConflictError):
            supervisor.start("OPS-001")
        self.assertEqual(self.harness.processes, [])

    def test_unknown_listener_fails_closed(self):
        self.harness.open_ports.add(8771)
        supervisor = self.harness.supervisor()
        with self.assertRaises(RuntimeConflictError):
            supervisor.start("QA-001")
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
        self.harness.processes[-1].return_code = 1
        supervisor.poll_once()
        self.assertEqual(len(self.harness.processes), 2)
        self.harness.processes[-1].return_code = 1
        supervisor.poll_once()
        self.assertEqual(len(self.harness.processes), 3)
        self.harness.processes[-1].return_code = 1
        statuses = supervisor.poll_once()
        self.assertEqual(len(self.harness.processes), 3)
        daniel = next(item for item in statuses if item.agent_id == "DEV-001")
        self.assertEqual(daniel.restart_count, 2)
        self.assertEqual(
            daniel.health_result.get("supervisor_error"),
            "restart limit reached",
        )

    def test_intentional_stop_does_not_restart(self):
        supervisor = self.harness.supervisor(max_restarts=3)
        supervisor.start("QA-001")
        supervisor.stop("QA-001")
        supervisor.poll_once()
        self.assertEqual(len(self.harness.processes), 1)

    def test_consolidated_status_contains_all_workers(self):
        self.harness.health["QA-001"] = {
            "ready": True,
            "agent_id": "QA-001",
        }
        self.harness.open_ports.add(8771)
        supervisor = self.harness.supervisor()
        statuses = supervisor.status_dicts()
        self.assertEqual(len(statuses), 3)
        self.assertEqual(
            {(item["agent_id"], item["name"], item["port"]) for item in statuses},
            {
                ("OPS-001", "Noah", 8770),
                ("DEV-001", "Daniel", 8765),
                ("QA-001", "Quinn", 8771),
            },
        )
        quinn = next(item for item in statuses if item["agent_id"] == "QA-001")
        self.assertTrue(quinn["running"])
        self.assertTrue(quinn["ready"])
        self.assertIn("health_result", quinn)

    def test_start_all_rolls_back_only_processes_started_by_supervisor(self):
        self.harness.open_ports.add(8765)
        supervisor = self.harness.supervisor()
        with self.assertRaises(RuntimeConflictError):
            supervisor.start_all()
        self.assertEqual(len(self.harness.processes), 1)
        self.assertTrue(self.harness.processes[0].terminated)


if __name__ == "__main__":
    unittest.main()
