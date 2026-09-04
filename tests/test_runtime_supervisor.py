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


class RuntimeSupervisorTests(unittest.TestCase):
    def contract(self, **changes):
        value = {
            "wp_id": "WP-1", "project": "rvsc", "objective": "Ship bounded work",
            "repository": "GitSly1/repo", "work_branch": "feature", "base_branch": "main",
            "agent_id": "DEV-001", "acceptance_criteria": ["passes"],
            "allowed_paths": ["controller/a.py"],
            "validation_commands": [{"name": "tests", "argv": ["python", "-m", "unittest"]}],
        }
        value.update(changes)
        return value

    def make_supervisor(self, store=None, execute_requester=None, payload=None, **kwargs):
        config = WorkerConfig("DEV-001", "Daniel", 8765, "engineering", authorized_projects=("rvsc",))
        return RuntimeSupervisor(
            configs=(config,), mission_store=store, clock=kwargs.pop("clock", Clock()),
            health_checker=lambda _config: payload or {"ready": True, "worker": "DEV-001", "authorized_projects": ["rvsc"]},
            port_checker=lambda _port: False,
            execute_requester=execute_requester or (lambda _config, _mission: {"qa_status": "QA_ACCEPTED", "qa_agent_id": "QA-001"}),
            **kwargs
        )

    def test_live_health_identity_behavior_remains_intact(self):
        payload = {"ready": True, "worker": "DEV-001", "name": "Daniel", "service": "rvsc-generic-worker"}
        result = RuntimeSupervisor._normalise_health(payload)
        self.assertTrue(result.healthy)
        self.assertEqual(result.agent_id, "DEV-001")
        self.assertTrue(self.make_supervisor(payload=payload).status_dicts()[0]["ready"])

    def test_conflicting_and_malformed_live_identity_fail_closed(self):
        for payload in (
            {"ready": True, "agent_id": "DEV-001", "worker": "OPS-001"},
            {"ready": True, "worker": 123},
            {"ready": True, "worker": {"agent_id": "DEV-001", "identity": "OPS-001"}},
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(RuntimeSupervisor._normalise_health(payload).agent_id)

    def test_validated_durable_contract_reaches_worker_at_top_level(self):
        store = MissionStore()
        expected = self.contract()
        store.add_contract(expected, supported_projects=("rvsc",))
        calls = []
        supervisor = self.make_supervisor(store, execute_requester=lambda config, payload: calls.append((config.agent_id, payload)) or {"qa_status": "QA_ACCEPTED", "qa_agent_id": "QA-001"})
        result = supervisor.work_control_once()
        self.assertEqual(result["state"], "ACCEPTED")
        self.assertEqual(calls, [("DEV-001", dict(expected, dependencies=[]))])
        payload = calls[0][1]
        self.assertEqual(payload["wp_id"], "WP-1")
        self.assertNotIn("mission_id", payload)
        self.assertNotIn("metadata", payload)
        self.assertNotIn("state", payload)
        self.assertNotIn("assigned_worker", payload)

    def test_missing_malformed_and_identity_mismatches_block_before_invocation(self):
        mutations = (
            lambda mission: mission.metadata.pop("contract"),
            lambda mission: mission.metadata.__setitem__("contract", {"wp_id": mission.mission_id}),
            lambda mission: mission.metadata["contract"].__setitem__("wp_id", "OTHER"),
            lambda mission: mission.metadata["contract"].__setitem__("project", "moxie"),
            lambda mission: mission.metadata["contract"].__setitem__("agent_id", "OPS-001"),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                store = MissionStore()
                mission = store.add_contract(self.contract(wp_id="WP-%d" % index), supported_projects=("rvsc",))
                mutate(mission)
                calls = []
                result = self.make_supervisor(store, execute_requester=lambda _config, payload: calls.append(payload)).work_control_once()
                self.assertEqual(result["state"], "BLOCKED")
                self.assertEqual(calls, [])
                self.assertEqual(mission.state, MissionState.BLOCKED)

    def test_dependency_aware_dispatch_remains_intact(self):
        store = MissionStore()
        store.add_contract(self.contract(wp_id="origin"), supported_projects=("rvsc",))
        store.add_contract(self.contract(wp_id="dependent", dependencies=["origin"]), supported_projects=("rvsc",))
        calls = []
        supervisor = self.make_supervisor(store, execute_requester=lambda _config, payload: calls.append(payload["wp_id"]) or {"qa_status": "QA_ACCEPTED", "qa_agent_id": "QA-001"})
        self.assertEqual(supervisor.work_control_once()["state"], "ACCEPTED")
        self.assertEqual(supervisor.work_control_once()["state"], "ACCEPTED")
        self.assertEqual(calls, ["origin", "dependent"])

    def test_qa_rejection_corrective_mission_is_automatically_dispatchable(self):
        store = MissionStore()
        store.add_contract(self.contract(), supported_projects=("rvsc",))
        calls = []
        results = iter((
            {"qa_status": "QA_REJECTED", "qa_agent_id": "QA-001", "reason": "bad"},
            {"qa_status": "QA_ACCEPTED", "qa_agent_id": "QA-001"},
        ))
        supervisor = self.make_supervisor(store, execute_requester=lambda _config, payload: calls.append(payload) or next(results))
        first = supervisor.work_control_once()
        self.assertEqual(first["state"], "REWORK_QUEUED")
        corrective_id = first["next_eligible_work"]
        second = supervisor.work_control_once()
        self.assertEqual(second["state"], "ACCEPTED")
        self.assertEqual(calls[1]["wp_id"], corrective_id)
        self.assertEqual(calls[1]["agent_id"], "DEV-001")
        self.assertEqual(calls[1]["repository"], self.contract()["repository"])
        self.assertNotEqual(calls[1]["agent_id"], "QA-001")
        self.assertEqual(store.get(corrective_id).implementer, "DEV-001")
        self.assertEqual(store.get(corrective_id).qa_worker, "QA-001")

    def test_non_quinn_and_self_qa_are_blocked(self):
        for qa_id in ("DEV-001", "QA-OTHER"):
            with self.subTest(qa_id=qa_id):
                store = MissionStore()
                store.add_contract(self.contract(), supported_projects=("rvsc",))
                supervisor = self.make_supervisor(store, execute_requester=lambda _config, _payload: {"qa_status": "QA_ACCEPTED", "qa_agent_id": qa_id})
                self.assertEqual(supervisor.work_control_once()["state"], "BLOCKED")

    def test_active_mission_is_not_duplicated_and_progress_monitoring_remains(self):
        store = MissionStore()
        store.add_contract(self.contract(), supported_projects=("rvsc",))
        store.transition("WP-1", "assigned", worker_id="DEV-001")
        store.transition("WP-1", "running", worker_id="DEV-001")
        calls = []
        supervisor = self.make_supervisor(store, execute_requester=lambda _config, payload: calls.append(payload), payload={"healthy": True, "agent_id": "DEV-001", "checkpoint": "build"})
        self.assertEqual(supervisor.work_control_once()["state"], "IDLE")
        self.assertEqual(calls, [])
        self.assertIsNotNone(store.get("WP-1").material_progress_at)

    def test_production_path_cli_add_and_status(self):
        self.assertEqual(production_mission_store_path({"RVSC_STATE_DIR": "/state"}), Path("/state/RAMTech/RVSC/mission-store.json"))
        with tempfile.TemporaryDirectory() as directory:
            mission_file = Path(directory) / "mission.json"
            store_path = Path(directory) / "store.json"
            mission_file.write_text(json.dumps(self.contract()), encoding="utf-8")
            self.assertEqual(main(["add", "--mission-store", str(store_path), "--mission-file", str(mission_file)]), 0)
            self.assertEqual(MissionStore.load(store_path).get("WP-1").state, MissionState.QUEUED)
            with mock.patch("controller.runtime_supervisor.RuntimeSupervisor.status_dicts", return_value=[]):
                self.assertEqual(main(["status", "--mission-store", str(store_path)]), 0)
            with self.assertRaises(OrchestrationError):
                MissionStore.load(store_path).add_contract(self.contract(), supported_projects=("rvsc",))


if __name__ == "__main__":
    unittest.main()
