import json
import tempfile
import unittest
from pathlib import Path

from controller.execution_store import ExecutionStoreError, JsonExecutionStore
from controller.worker_runtime import WorkerExecution, WorkerState


class JsonExecutionStoreTests(unittest.TestCase):
    def test_round_trip_survives_store_recreation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker-state.json"
            execution = WorkerExecution(
                wp_id="SEM-003",
                agent_id="AUTO-001",
                state=WorkerState.EVIDENCE_DELIVERED,
                attempt=2,
                evidence=("commit:abc123", "ci:pass"),
            )
            JsonExecutionStore(path).save(execution)
            loaded = JsonExecutionStore(path).get("SEM-003")
            self.assertEqual(execution, loaded)

    def test_save_replaces_existing_wp_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker-state.json"
            store = JsonExecutionStore(path)
            store.save(WorkerExecution("WP-1", "DEV-001", WorkerState.ASSIGNED))
            store.save(
                WorkerExecution(
                    "WP-1", "DEV-001", WorkerState.EXECUTING, attempt=1
                )
            )
            records = store.load_all()
            self.assertEqual(1, len(records))
            self.assertEqual(WorkerState.EXECUTING, records["WP-1"].state)
            self.assertEqual(1, records["WP-1"].attempt)

    def test_unknown_wp_returns_none_without_creating_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker-state.json"
            store = JsonExecutionStore(path)
            self.assertIsNone(store.get("missing"))
            self.assertFalse(path.exists())

    def test_corrupt_or_invalid_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker-state.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(ExecutionStoreError):
                JsonExecutionStore(path).load_all()
            path.write_text(json.dumps({"schema_version": 99, "executions": {}}), encoding="utf-8")
            with self.assertRaises(ExecutionStoreError):
                JsonExecutionStore(path).load_all()


if __name__ == "__main__":
    unittest.main()
