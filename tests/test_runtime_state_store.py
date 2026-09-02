from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from controller.runtime_state_store import DurableRuntimeStateStore


class DurableRuntimeStateStoreTests(unittest.TestCase):
    def test_round_trip_preserves_runtime_state(self):
        with tempfile.TemporaryDirectory() as temp:
            store = DurableRuntimeStateStore(temp)
            original = {
                "active_mission": "RVSC-025B",
                "last_run_id": "RUN-001",
                "last_checkpoint": "tests_passed",
                "checkpoint_evidence": ("validation:unit:0",),
            }
            path = store.save("OPS-001", original)
            restored = store.load("OPS-001")

            self.assertTrue(path.exists())
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored["active_mission"], "RVSC-025B")
            self.assertEqual(restored["last_run_id"], "RUN-001")
            self.assertEqual(restored["last_checkpoint"], "tests_passed")
            self.assertEqual(restored["checkpoint_evidence"], ("validation:unit:0",))

    def test_state_is_isolated_by_agent(self):
        with tempfile.TemporaryDirectory() as temp:
            store = DurableRuntimeStateStore(temp)
            store.save("OPS-001", {"last_checkpoint": "noah"})
            store.save("QA-001", {"last_checkpoint": "quinn"})

            self.assertEqual(store.load("OPS-001")["last_checkpoint"], "noah")
            self.assertEqual(store.load("QA-001")["last_checkpoint"], "quinn")

    def test_clear_removes_saved_state(self):
        with tempfile.TemporaryDirectory() as temp:
            store = DurableRuntimeStateStore(temp)
            store.save("OPS-001", {"last_checkpoint": "saved"})
            store.clear("OPS-001")
            self.assertIsNone(store.load("OPS-001"))

    def test_unsafe_agent_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            store = DurableRuntimeStateStore(temp)
            with self.assertRaises(ValueError):
                store.save("../OPS-001", {})


if __name__ == "__main__":
    unittest.main()
