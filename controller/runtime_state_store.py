from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROTOCOL = "rvsc.runtime.state.v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DurableRuntimeStateStore:
    """Persist worker runtime state atomically outside tracked source files."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self._lock = threading.Lock()

    def _path(self, agent_id: str) -> Path:
        normalized = agent_id.strip().upper()
        if not normalized or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in normalized):
            raise ValueError(f"unsafe agent id: {agent_id!r}")
        return self.root / f"{normalized}.json"

    def save(self, agent_id: str, state: dict[str, Any]) -> Path:
        path = self._path(agent_id)
        payload = {
            "protocol": PROTOCOL,
            "agent_id": agent_id.strip().upper(),
            "saved_at": _utc_now(),
            "state": dict(state),
        }
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8", newline="")
            os.replace(temp, path)
        return path

    def load(self, agent_id: str) -> dict[str, Any] | None:
        path = self._path(agent_id)
        with self._lock:
            if not path.exists():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("protocol") != PROTOCOL:
            raise ValueError("unsupported runtime-state protocol")
        expected = agent_id.strip().upper()
        if payload.get("agent_id") != expected:
            raise ValueError("runtime-state agent mismatch")
        state = payload.get("state")
        if not isinstance(state, dict):
            raise ValueError("runtime-state payload is invalid")
        restored = dict(state)
        restored["checkpoint_evidence"] = tuple(restored.get("checkpoint_evidence", ()))
        return restored

    def clear(self, agent_id: str) -> None:
        path = self._path(agent_id)
        with self._lock:
            if path.exists():
                path.unlink()
