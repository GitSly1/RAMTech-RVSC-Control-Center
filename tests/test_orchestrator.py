import tempfile
import unittest
from pathlib import Path

from controller.orchestrator import Mission, MissionState, MissionStore, OrchestrationError, WorkerState, dispatch_next, select_dispatch, validate_mission_contract


class MissionStoreTests(unittest.TestCase):
    def completed(self, store, mission_id, implementer="DEV-001"):
        store.transition(mission_id, "assigned", worker_id=implementer)
        store.transition(mission_id, "running", worker_id=implementer)
        store.transition(mission_id, "completed", worker_id=implementer)

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

    def test_load_or_create_cold_creation_and_durable_add(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "missions.json"
            store = MissionStore.load_or_create(path)
            self.assertTrue(path.exists())
            self.assertEqual(store.all(), ())
            store.add(Mission("M-1", "rvsc"))
            self.assertEqual(MissionStore.load(path).get("M-1").state, MissionState.QUEUED)

    def test_progress_is_durable_and_identical_evidence_is_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missions.json"
            store = MissionStore.load_or_create(path)
            store.add(Mission("M-1", "rvsc"))
            store.record_progress("M-1", timestamp=10, checkpoint="build", evidence=["commit:a"])
            store.record_progress("M-1", timestamp=20, checkpoint="build", evidence=["commit:a"])
            mission = MissionStore.load(path).get("M-1")
            self.assertEqual(mission.material_progress_at, 10.0)

    def test_progress_timestamp_cannot_move_backwards(self):
        store = MissionStore()
        store.add(Mission("M", "rvsc"))
        store.record_progress("M", timestamp=10, checkpoint="a", evidence=[])
        with self.assertRaises(OrchestrationError):
            store.record_progress("M", timestamp=9, checkpoint="b", evidence=[])

    def test_lifecycle_and_independent_qa(self):
        store = MissionStore()
        store.add(Mission("M", "rvsc"))
        self.completed(store, "M")
        with self.assertRaises(OrchestrationError):
            store.transition("M", "qa_pending", worker_id="DEV-001")
        store.transition("M", "qa_pending", worker_id="QA-001", timestamp=4)
        store.transition("M", "accepted", worker_id="QA-001", timestamp=5)
        self.assertEqual(store.get("M").state, MissionState.ACCEPTED)

    def test_qa_acceptance_unlocks_dependency(self):
        store = MissionStore()
        store.add(Mission("origin", "rvsc"))
        store.add(Mission("dependent", "rvsc", dependencies=("origin",), priority=1))
        self.completed(store, "origin")
        store.process_qa_outcome("origin", "QA_ACCEPTED", qa_worker="QA-001", evidence={"commit": "abc"}, timestamp=5)
        self.assertTrue(store.readiness("dependent")[0])

    def test_rejection_creates_traceable_idempotent_corrective_work(self):
        store = MissionStore()
        store.add(Mission("origin", "rvsc"))
        self.completed(store, "origin")
        evidence = {"rejection_reason": "tests failed", "commit": "abc", "branch": "feature"}
        first = store.process_qa_outcome("origin", "QA_REJECTED", qa_worker="QA-001", evidence=evidence, max_rework_attempts=2)
        duplicate = store.process_qa_outcome("origin", "QA_REJECTED", qa_worker="QA-001", evidence=evidence, max_rework_attempts=2)
        self.assertTrue(duplicate.idempotent)
        self.assertEqual(first.corrective_mission_id, "origin::rework:1")
        self.assertEqual(len(store.all()), 2)

    def test_rework_count_persists_and_exhaustion_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missions.json"
            store = MissionStore.load_or_create(path)
            store.add(Mission("origin", "rvsc"))
            self.completed(store, "origin")
            first = store.process_qa_outcome("origin", "QA_REJECTED", qa_worker="QA-001", evidence={"reason": "one"}, max_rework_attempts=1)
            restored = MissionStore.load(path)
            self.completed(restored, first.corrective_mission_id, "DEV-002")
            exhausted = restored.process_qa_outcome(first.corrective_mission_id, "QA_REJECTED", qa_worker="QA-001", evidence={"reason": "two"}, max_rework_attempts=1)
            self.assertEqual(exhausted.outcome, "REWORK_EXHAUSTED")
            self.assertEqual(restored.get("origin").state, MissionState.BLOCKED)

    def test_dispatch_remains_deterministic_and_distinguishes_idle_starved(self):
        store = MissionStore()
        store.add(Mission("later", "rvsc", priority=2))
        store.add(Mission("first", "rvsc", priority=1))
        workers = [WorkerState("DEV-002", ("rvsc",)), WorkerState("DEV-001", ("rvsc",))]
        decision = select_dispatch(store, workers)
        self.assertEqual((decision.mission_id, decision.worker_id), ("first", "DEV-001"))
        self.assertEqual(dispatch_next(store, workers).outcome, "dispatch")
        blocked = MissionStore()
        blocked.add(Mission("blocked", "rvsc", dependencies=("missing",)))
        self.assertEqual(select_dispatch(blocked, []).outcome, "idle")
        queued = MissionStore()
        queued.add(Mission("queued", "rvsc"))
        self.assertEqual(select_dispatch(queued, []).outcome, "starved")

    def test_contract_ingestion_validates_and_persists_authorized_work(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missions.json"
            store = MissionStore.load_or_create(path)
            mission = store.add_contract(self.contract(), supported_projects=("rvsc",))
            restored = MissionStore.load(path).get(mission.mission_id)
            self.assertTrue(restored.metadata["requires_independent_qa"])
            self.assertEqual(restored.metadata["contract"]["objective"], "Deliver work")

    def test_malformed_duplicate_unsupported_and_unsafe_contracts_fail_closed(self):
        with self.assertRaises(OrchestrationError):
            validate_mission_contract(self.contract(project="unknown"), ("rvsc",))
        with self.assertRaises(OrchestrationError):
            validate_mission_contract(self.contract(allowed_paths=["../secret"]), ("rvsc",))
        with self.assertRaises(OrchestrationError):
            validate_mission_contract(self.contract(validation_commands=[]), ("rvsc",))
        with self.assertRaises(OrchestrationError):
            validate_mission_contract(self.contract(mission_id="OTHER"), ("rvsc",))
        store = MissionStore()
        store.add_contract(self.contract(), supported_projects=("rvsc",))
        with self.assertRaises(OrchestrationError):
            store.add_contract(self.contract(), supported_projects=("rvsc",))


if __name__ == "__main__":
    unittest.main()
