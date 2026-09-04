import tempfile
import unittest
from pathlib import Path

from controller.orchestrator import Mission, MissionState, MissionStore, OrchestrationError, WorkerState, dispatch_next, select_dispatch


class MissionStoreTests(unittest.TestCase):
    def completed(self, store, mission_id, implementer="DEV-001"):
        store.transition(mission_id, "assigned", worker_id=implementer)
        store.transition(mission_id, "running", worker_id=implementer)
        store.transition(mission_id, "completed", worker_id=implementer)

    def test_progress_is_durable_and_identical_evidence_is_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missions.json"
            store = MissionStore(path)
            store.add(Mission("M-1", "rvsc"))
            store.save()
            store.record_progress("M-1", timestamp=10, checkpoint="build", evidence=["commit:a"])
            store.record_progress("M-1", timestamp=20, checkpoint="build", evidence=["commit:a"])
            restored = MissionStore.load(path)
            mission = restored.get("M-1")
            self.assertEqual(mission.material_progress_at, 10.0)
            self.assertEqual(mission.material_checkpoint, "build")
            restored.record_progress("M-1", timestamp=20, checkpoint="tests", evidence=["pass"])
            self.assertEqual(restored.get("M-1").material_progress_at, 20.0)

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
        self.assertEqual(store.get("M").material_progress_at, 5.0)

    def test_qa_acceptance_unlocks_dependency(self):
        store = MissionStore()
        store.add(Mission("origin", "rvsc"))
        store.add(Mission("dependent", "rvsc", dependencies=("origin",), priority=1))
        self.completed(store, "origin")
        store.process_qa_outcome("origin", "QA_ACCEPTED", qa_worker="QA-001", evidence={"commit": "abc"}, timestamp=5)
        self.assertEqual(store.get("origin").state, MissionState.ACCEPTED)
        self.assertTrue(store.readiness("dependent")[0])
        decision = select_dispatch(store, [WorkerState("DEV-001", ("rvsc",))])
        self.assertEqual(decision.mission_id, "dependent")

    def test_rejection_creates_traceable_idempotent_corrective_work(self):
        store = MissionStore()
        store.add(Mission("origin", "rvsc"))
        self.completed(store, "origin")
        evidence = {"rejection_reason": "tests failed", "commit": "abc", "branch": "feature"}
        first = store.process_qa_outcome("origin", "QA_REJECTED", qa_worker="QA-001", evidence=evidence, max_rework_attempts=2)
        duplicate = store.process_qa_outcome("origin", "QA_REJECTED", qa_worker="QA-001", evidence=evidence, max_rework_attempts=2)
        self.assertEqual(first.corrective_mission_id, "origin::rework:1")
        self.assertTrue(duplicate.idempotent)
        self.assertEqual(len(store.all()), 2)
        corrective = store.get(first.corrective_mission_id)
        self.assertEqual(corrective.parent_mission_id, "origin")
        self.assertEqual(corrective.metadata["qa_rejection_evidence"], evidence)
        self.assertEqual(store.get("origin").state, MissionState.REJECTED)
        decision = select_dispatch(store, [WorkerState("QA-001", ("rvsc",)), WorkerState("DEV-001", ("rvsc",))])
        self.assertEqual((decision.mission_id, decision.worker_id), (corrective.mission_id, "DEV-001"))

    def test_corrective_acceptance_accepts_root_and_unlocks_dependency(self):
        store = MissionStore()
        store.add(Mission("origin", "rvsc"))
        store.add(Mission("dependent", "rvsc", dependencies=("origin",)))
        self.completed(store, "origin")
        rejected = store.process_qa_outcome("origin", "QA_REJECTED", qa_worker="QA-001", evidence={"reason": "bad"})
        self.completed(store, rejected.corrective_mission_id, "DEV-002")
        store.process_qa_outcome(rejected.corrective_mission_id, "QA_ACCEPTED", qa_worker="QA-001", evidence={"commit": "fixed"})
        self.assertEqual(store.get("origin").state, MissionState.ACCEPTED)
        self.assertEqual(store.get(rejected.corrective_mission_id).state, MissionState.ACCEPTED)
        self.assertTrue(store.readiness("dependent")[0])

    def test_rework_count_persists_and_exhaustion_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missions.json"
            store = MissionStore(path)
            store.add(Mission("origin", "rvsc"))
            self.completed(store, "origin")
            first = store.process_qa_outcome("origin", "QA_REJECTED", qa_worker="QA-001", evidence={"reason": "one"}, max_rework_attempts=1)
            restored = MissionStore.load(path)
            self.assertEqual(restored.get("origin").rework_attempts, 1)
            self.completed(restored, first.corrective_mission_id, "DEV-002")
            exhausted = restored.process_qa_outcome(first.corrective_mission_id, "QA_REJECTED", qa_worker="QA-001", evidence={"reason": "two"}, max_rework_attempts=1)
            self.assertEqual(exhausted.outcome, "REWORK_EXHAUSTED")
            self.assertEqual(restored.get("origin").state, MissionState.BLOCKED)
            self.assertEqual(len(restored.all()), 2)

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

    def test_recovery_count_is_durable_and_monotonic(self):
        store = MissionStore()
        store.add(Mission("M", "rvsc"))
        store.record_recovery("M", 1, "ATTEMPTED", "restart")
        self.assertEqual(store.get("M").recovery_outcome, "restart")
        with self.assertRaises(OrchestrationError):
            store.record_recovery("M", 0, "NONE", None)


if __name__ == "__main__":
    unittest.main()
