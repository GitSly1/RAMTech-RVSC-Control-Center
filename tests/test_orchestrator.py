import tempfile
import unittest
from pathlib import Path

from controller.orchestrator import Mission, MissionState, MissionStore, OrchestrationError, WorkerState, dispatch_next, select_dispatch, validate_mission_contract


class MissionStoreTests(unittest.TestCase):
    def contract(self, **changes):
        value = {
            "wp_id": "WP-1", "project": "rvsc", "objective": "Deliver work",
            "repository": "GitSly1/repo", "work_branch": "feature", "base_branch": "main",
            "agent_id": "DEV-001", "acceptance_criteria": ["tests pass"],
            "allowed_paths": ["controller/a.py"],
            "validation_commands": [{"name": "tests", "argv": ["python", "-m", "unittest"]}],
        }
        value.update(changes)
        return value

    def completed(self, store, mission_id, implementer="DEV-001"):
        store.transition(mission_id, "assigned", worker_id=implementer)
        store.transition(mission_id, "running", worker_id=implementer)
        store.transition(mission_id, "completed", worker_id=implementer)

    def test_load_or_create_and_progress_are_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missions.json"
            store = MissionStore.load_or_create(path)
            store.add(Mission("M-1", "rvsc"))
            store.record_progress("M-1", timestamp=10, checkpoint="build", evidence=["commit:a"])
            store.record_progress("M-1", timestamp=20, checkpoint="build", evidence=["commit:a"])
            self.assertEqual(MissionStore.load(path).get("M-1").material_progress_at, 10.0)

    def test_independent_qa_and_dependency_dispatch_remain_intact(self):
        store = MissionStore()
        store.add(Mission("origin", "rvsc"))
        store.add(Mission("dependent", "rvsc", dependencies=("origin",), priority=1))
        self.completed(store, "origin")
        with self.assertRaises(OrchestrationError):
            store.transition("origin", "qa_pending", worker_id="DEV-001")
        store.process_qa_outcome("origin", "QA_ACCEPTED", qa_worker="QA-001", evidence={"commit": "abc"})
        self.assertTrue(store.readiness("dependent")[0])

    def test_dispatch_is_deterministic_and_honors_contract_agent(self):
        store = MissionStore()
        store.add_contract(self.contract(wp_id="later", priority=2), supported_projects=("rvsc",))
        store.add_contract(self.contract(wp_id="first", priority=1), supported_projects=("rvsc",))
        workers = [WorkerState("DEV-002", ("rvsc",)), WorkerState("DEV-001", ("rvsc",))]
        decision = select_dispatch(store, workers)
        self.assertEqual((decision.mission_id, decision.worker_id), ("first", "DEV-001"))
        self.assertEqual(dispatch_next(store, workers).outcome, "dispatch")

    def test_contract_ingestion_and_dispatch_contract_validate_identity(self):
        store = MissionStore()
        expected = validate_mission_contract(self.contract(), ("rvsc",))
        store.add_contract(self.contract(), supported_projects=("rvsc",))
        self.assertEqual(store.dispatch_contract("WP-1", "DEV-001", supported_projects=("rvsc",)), expected)
        for field, value in (("wp_id", "OTHER"), ("project", "moxie"), ("agent_id", "DEV-002")):
            with self.subTest(field=field):
                store.get("WP-1").metadata["contract"][field] = value
                with self.assertRaises(OrchestrationError):
                    store.dispatch_contract("WP-1", "DEV-001", supported_projects=("rvsc", "moxie"))
                store.get("WP-1").metadata["contract"] = expected.copy()

    def test_missing_and_malformed_stored_contracts_fail_closed(self):
        store = MissionStore()
        store.add(Mission("raw", "rvsc"))
        with self.assertRaises(OrchestrationError):
            store.dispatch_contract("raw", "DEV-001", supported_projects=("rvsc",))
        mission = store.get("raw")
        mission.metadata["contract"] = {"wp_id": "raw"}
        with self.assertRaises(OrchestrationError):
            store.dispatch_contract("raw", "DEV-001", supported_projects=("rvsc",))

    def test_rejection_creates_valid_dispatchable_corrective_contract(self):
        store = MissionStore()
        original = store.add_contract(self.contract(), supported_projects=("rvsc",))
        self.completed(store, original.mission_id)
        result = store.process_qa_outcome(original.mission_id, "QA_REJECTED", qa_worker="QA-001", evidence={"reason": "tests failed", "commit": "abc"})
        corrective = store.get(result.corrective_mission_id)
        contract = store.dispatch_contract(corrective.mission_id, "DEV-001", supported_projects=("rvsc",))
        self.assertEqual(contract["wp_id"], corrective.mission_id)
        self.assertEqual(contract["repository"], self.contract()["repository"])
        self.assertEqual(contract["work_branch"], self.contract()["work_branch"])
        self.assertEqual(contract["allowed_paths"], self.contract()["allowed_paths"])
        self.assertEqual(contract["acceptance_criteria"], self.contract()["acceptance_criteria"])
        self.assertEqual(contract["dependencies"], [])
        self.assertIn("Original objective", contract["objective"])
        self.assertIn("QA-001", corrective.metadata["excluded_worker_ids"])
        self.assertTrue(corrective.metadata["requires_independent_qa"])

    def test_malformed_unsupported_and_unsafe_contracts_fail_closed(self):
        with self.assertRaises(OrchestrationError):
            validate_mission_contract(self.contract(project="unknown"), ("rvsc",))
        with self.assertRaises(OrchestrationError):
            validate_mission_contract(self.contract(allowed_paths=["../secret"]), ("rvsc",))
        with self.assertRaises(OrchestrationError):
            validate_mission_contract(self.contract(validation_commands=[]), ("rvsc",))
        with self.assertRaises(OrchestrationError):
            validate_mission_contract(self.contract(mission_id="OTHER"), ("rvsc",))


if __name__ == "__main__":
    unittest.main()
