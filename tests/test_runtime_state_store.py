from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from controller.runtime_state_store import DurableRuntimeStateStore


class DurableRuntimeStateStoreTests(unittest.TestCase):
    def test_round_trip_preserves_runtime_state(self):
        with tempfile.TemporaryDirectory() as temp:
            store = DurableRuntimeStateStore(temp)
            original = {"active_mission": "RVSC-025B", "last_run_id": "RUN-001", "last_checkpoint": "tests_passed", "checkpoint_evidence": ("validation:unit:0",)}
            path = store.save("OPS-001", original)
            restored = store.load("OPS-001")
            self.assertTrue(path.exists())
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored["active_mission"], "RVSC-025B")
            self.assertEqual(restored["last_run_id"], "RUN-001")
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
            with self.assertRaises(ValueError):
                DurableRuntimeStateStore(temp).save("../OPS-001", {})

    def test_secrets_are_removed_or_redacted_before_persistence(self):
        with tempfile.TemporaryDirectory() as temp:
            store = DurableRuntimeStateStore(temp)
            path = store.save("OPS-001", {
                "recovery_context": {
                    "wp_id": "RVSC-029B",
                    "api_key": "sk-super-secret-value",
                    "headers": {"Authorization": "Bearer hidden-token"},
                    "nested": {"password": "hidden", "note": "Authorization: Bearer another-token"},
                },
                "checkpoint_evidence": ("failure: api_key=hidden-value",),
            })
            raw = path.read_text(encoding="utf-8")
            restored = store.load("OPS-001")
            self.assertNotIn("sk-super-secret-value", raw)
            self.assertNotIn("hidden-token", raw)
            self.assertNotIn("hidden-value", raw)
            self.assertNotIn("api_key", restored["recovery_context"])
            self.assertNotIn("headers", restored["recovery_context"])
            self.assertNotIn("password", restored["recovery_context"]["nested"])

    def test_malformed_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "OPS-001.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "malformed"):
                DurableRuntimeStateStore(temp).load("OPS-001")

    def test_agent_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = DurableRuntimeStateStore(temp).save("OPS-001", {"last_checkpoint": "saved"})
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["agent_id"] = "QA-001"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "agent mismatch"):
                DurableRuntimeStateStore(temp).load("OPS-001")


if __name__ == "__main__":
    unittest.main()
