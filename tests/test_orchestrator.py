import tempfile
import unittest
from pathlib import Path

from controller.orchestrator import Mission, MissionState, MissionStore, OrchestrationError, WorkerState, dispatch_next, select_dispatch


class MissionStoreTests(unittest.TestCase):
    def test_progress_is_durable_and_identical_evidence_is_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missions.json"; store = MissionStore(path); store.add(Mission("M-1", "rvsc")); store.save()
            store.record_progress("M-1", timestamp=10, checkpoint="build", evidence=["commit:a"])
            store.record_progress("M-1", timestamp=20, checkpoint="build", evidence=["commit:a"])
            restored = MissionStore.load(path); mission = restored.get("M-1")
            self.assertEqual(mission.material_progress_at, 10.0); self.assertEqual(mission.material_checkpoint, "build")
            restored.record_progress("M-1", timestamp=20, checkpoint="tests", evidence=["pass"])
            self.assertEqual(restored.get("M-1").material_progress_at, 20.0)

    def test_progress_timestamp_cannot_move_backwards(self):
        store = MissionStore(); store.add(Mission("M", "rvsc")); store.record_progress("M", timestamp=10, checkpoint="a", evidence=[])
        with self.assertRaises(OrchestrationError): store.record_progress("M", timestamp=9, checkpoint="b", evidence=[])

    def test_lifecycle_and_independent_qa(self):
        store = MissionStore(); store.add(Mission("M", "rvsc")); store.transition("M", "assigned", worker_id="DEV-001", timestamp=1); store.transition("M", "running", worker_id="DEV-001", timestamp=2); store.transition("M", "completed", worker_id="DEV-001", timestamp=3)
        with self.assertRaises(OrchestrationError): store.transition("M", "qa_pending", worker_id="DEV-001")
        store.transition("M", "qa_pending", worker_id="QA-001", timestamp=4); store.transition("M", "accepted", worker_id="QA-001", timestamp=5)
        self.assertEqual(store.get("M").state, MissionState.ACCEPTED); self.assertEqual(store.get("M").material_progress_at, 5.0)

    def test_dispatch_remains_deterministic_and_distinguishes_idle_starved(self):
        store = MissionStore(); store.add(Mission("later", "rvsc", priority=2)); store.add(Mission("first", "rvsc", priority=1))
        workers = [WorkerState("DEV-002", ("rvsc",)), WorkerState("DEV-001", ("rvsc",))]
        decision = select_dispatch(store, workers); self.assertEqual((decision.mission_id, decision.worker_id), ("first", "DEV-001"))
        assigned = dispatch_next(store, workers); self.assertEqual(assigned.outcome, "dispatch")
        blocked = MissionStore(); blocked.add(Mission("blocked", "rvsc", dependencies=("missing",)))
        self.assertEqual(select_dispatch(blocked, []).outcome, "idle")
        queued = MissionStore(); queued.add(Mission("queued", "rvsc"))
        self.assertEqual(select_dispatch(queued, []).outcome, "starved")

    def test_recovery_count_is_durable_and_monotonic(self):
        store = MissionStore(); store.add(Mission("M", "rvsc")); store.record_recovery("M", 1, "ATTEMPTED", "restart")
        self.assertEqual(store.get("M").recovery_outcome, "restart")
        with self.assertRaises(OrchestrationError): store.record_recovery("M", 0, "NONE", None)


if __name__ == "__main__": unittest.main()
