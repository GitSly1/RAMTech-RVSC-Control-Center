from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROTOCOL = "rvsc.runtime.state.v1"
_SENSITIVE_KEYS = frozenset({"api_key", "apikey", "access_token", "refresh_token", "token", "password", "passwd", "secret", "client_secret", "private_key", "credential", "credentials", "authorization", "authorization_header", "cookie", "set_cookie", "headers"})
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|password|client[_-]?secret)\s*[:=]\s*[^\s,;]+"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_key(key: str) -> str:
    return key.strip().lower().replace("-", "_").replace(" ", "_")


def _sanitize_string(value: str) -> str:
    sanitized = value
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized


def sanitize_for_persistence(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if _normalized_key(key) in _SENSITIVE_KEYS:
                continue
            sanitized[key] = sanitize_for_persistence(item)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [sanitize_for_persistence(item) for item in value]
    if isinstance(value, str):
        return _sanitize_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_string(str(value))


class DurableRuntimeStateStore:
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
        payload = {"protocol": PROTOCOL, "agent_id": agent_id.strip().upper(), "saved_at": _utc_now(), "state": sanitize_for_persistence(state)}
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + ".tmp")
            try:
                temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8", newline="")
                os.replace(temp, path)
            finally:
                if temp.exists():
                    temp.unlink()
        return path

    def load(self, agent_id: str) -> dict[str, Any] | None:
        path = self._path(agent_id)
        with self._lock:
            if not path.exists():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                raise ValueError("runtime-state payload is malformed") from exc
        if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL:
            raise ValueError("unsupported runtime-state protocol")
        expected = agent_id.strip().upper()
        if payload.get("agent_id") != expected:
            raise ValueError("runtime-state agent mismatch")
        state = payload.get("state")
        if not isinstance(state, dict):
            raise ValueError("runtime-state payload is invalid")
        restored = dict(state)
        evidence = restored.get("checkpoint_evidence", ())
        if not isinstance(evidence, (list, tuple)):
            raise ValueError("runtime-state checkpoint evidence is invalid")
        restored["checkpoint_evidence"] = tuple(evidence)
        return restored

    def clear(self, agent_id: str) -> None:
        path = self._path(agent_id)
        with self._lock:
            if path.exists():
                path.unlink()
