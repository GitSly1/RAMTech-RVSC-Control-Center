from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Mapping

from controller.worker_runtime import WorkerExecution, WorkerState


class ExecutionStoreError(ValueError):
    pass


class JsonExecutionStore:
    """Durable, single-file store for current worker execution state by WP ID."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load_all(self) -> Mapping[str, WorkerExecution]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExecutionStoreError(f"cannot read execution state: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ExecutionStoreError("invalid execution state schema")
        records = payload.get("executions")
        if not isinstance(records, dict):
            raise ExecutionStoreError("executions must be an object")
        result: dict[str, WorkerExecution] = {}
        for wp_id, raw in records.items():
            if not isinstance(wp_id, str) or not isinstance(raw, dict):
                raise ExecutionStoreError("invalid execution record")
            try:
                result[wp_id] = WorkerExecution(
                    wp_id=wp_id,
                    agent_id=str(raw["agent_id"]),
                    state=WorkerState(str(raw["state"])),
                    attempt=int(raw.get("attempt", 0)),
                    evidence=tuple(str(item) for item in raw.get("evidence", [])),
                    failure_reason=(
                        None if raw.get("failure_reason") is None else str(raw["failure_reason"])
                    ),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ExecutionStoreError(f"invalid execution record for {wp_id}") from exc
        return result

    def get(self, wp_id: str) -> WorkerExecution | None:
        return self.load_all().get(wp_id)

    def save(self, execution: WorkerExecution) -> None:
        records = dict(self.load_all())
        records[execution.wp_id] = execution
        payload = {
            "schema_version": 1,
            "executions": {
                wp_id: {
                    "agent_id": item.agent_id,
                    "state": item.state.value,
                    "attempt": item.attempt,
                    "evidence": list(item.evidence),
                    "failure_reason": item.failure_reason,
                }
                for wp_id, item in sorted(records.items())
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
