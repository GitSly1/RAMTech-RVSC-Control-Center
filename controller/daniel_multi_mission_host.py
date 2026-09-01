from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import daniel_worker_host as legacy

SEM_DANIEL_003_WP = "SEM-DANIEL-003"
SEM_DANIEL_003_BASE_BRANCH = "rvsc/SEM-DANIEL-003-final-baseline"
SEM_DANIEL_003_BRANCH = "rvsc/SEM-DANIEL-003-runtime-proof"
SEM_DANIEL_003_ALLOWED = (
    "interpretation_layer.py",
    "tests/test_interpretation_layer.py",
)

_EXECUTION_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_RUNTIME_STATE: dict[str, Any] = {
    "active_mission": None,
    "last_run_id": None,
    "last_activity": None,
    "last_result": None,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_runtime_state(**updates: Any) -> None:
    with _STATE_LOCK:
        _RUNTIME_STATE.update(updates)
        _RUNTIME_STATE["last_activity"] = _utc_now()


def health_payload() -> dict[str, Any]:
    with _STATE_LOCK:
        state = dict(_RUNTIME_STATE)
    credential_ready = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    return {
        "protocol": "rvsc.worker.health.v1",
        "worker": "DEV-001",
        "name": "Daniel",
        "service": "daniel-multi-mission",
        "ready": credential_ready and state["active_mission"] is None,
        "credential_ready": credential_ready,
        "busy": state["active_mission"] is not None,
        "active_mission": state["active_mission"],
        "last_run_id": state["last_run_id"],
        "last_activity": state["last_activity"],
        "last_result": state["last_result"],
        "supported_missions": [legacy.SEM_DANIEL_WP, SEM_DANIEL_003_WP],
    }


def _execute_003(api_key: str, mission: dict[str, Any], run_id: str, started: str) -> dict[str, Any]:
    if mission.get("base_branch") != SEM_DANIEL_003_BASE_BRANCH:
        raise ValueError(f"{SEM_DANIEL_003_WP} requires base branch {SEM_DANIEL_003_BASE_BRANCH}")
    if mission.get("work_branch") != SEM_DANIEL_003_BRANCH:
        raise ValueError(f"{SEM_DANIEL_003_WP} requires branch {SEM_DANIEL_003_BRANCH}")
    if tuple(mission.get("allowed_paths", ())) != SEM_DANIEL_003_ALLOWED:
        raise ValueError(f"{SEM_DANIEL_003_WP} allowed path contract mismatch")

    with _EXECUTION_LOCK:
        original = (
            legacy.SEM_DANIEL_WP,
            legacy.SEM_DANIEL_BASE_BRANCH,
            legacy.SEM_DANIEL_BRANCH,
            legacy.SEM_DANIEL_ALLOWED,
        )
        try:
            legacy.SEM_DANIEL_WP = SEM_DANIEL_003_WP
            legacy.SEM_DANIEL_BASE_BRANCH = SEM_DANIEL_003_BASE_BRANCH
            legacy.SEM_DANIEL_BRANCH = SEM_DANIEL_003_BRANCH
            legacy.SEM_DANIEL_ALLOWED = SEM_DANIEL_003_ALLOWED
            return legacy._execute_sem_daniel(api_key, mission, run_id, started)
        finally:
            (
                legacy.SEM_DANIEL_WP,
                legacy.SEM_DANIEL_BASE_BRANCH,
                legacy.SEM_DANIEL_BRANCH,
                legacy.SEM_DANIEL_ALLOWED,
            ) = original


def execute_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("protocol") != "rvsc.worker.v1":
        raise ValueError("unsupported protocol")
    mission = payload.get("mission")
    if not isinstance(mission, dict):
        raise ValueError("mission must be an object")
    if mission.get("agent_id") != "DEV-001":
        raise ValueError("Daniel host only accepts DEV-001")

    wp_id = str(mission.get("wp_id", "")).strip() or "unknown"
    _set_runtime_state(active_mission=wp_id, last_result="acknowledged")
    try:
        if mission.get("wp_id") != SEM_DANIEL_003_WP:
            with _EXECUTION_LOCK:
                result = legacy.execute_payload(payload)
            _set_runtime_state(last_result="success" if result.get("success") else "failed")
            return result

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        run_id = f"RVSC-DAN-{legacy.uuid.uuid4().hex[:12].upper()}"
        started = legacy._utc_now()
        _set_runtime_state(last_run_id=run_id, last_result="executing")
        result = _execute_003(api_key, mission, run_id, started)
        _set_runtime_state(last_result="success" if result.get("success") else "failed")
        return result
    except Exception:
        _set_runtime_state(last_result="failed")
        raise
    finally:
        _set_runtime_state(active_mission=None)


class DanielMultiMissionHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        self._send_json(200, health_payload())

    def do_POST(self) -> None:
        if self.path != "/execute":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = execute_payload(payload)
            self._send_json(200, result)
        except Exception as exc:
            self._send_json(500, {
                "success": False,
                "summary": str(exc),
                "evidence": ["worker_host:daniel-multi-mission"],
                "retryable": False,
            })

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[DanielMultiHost] {fmt % args}")


def main() -> None:
    host = os.environ.get("RVSC_DANIEL_HOST", "127.0.0.1")
    port = int(os.environ.get("RVSC_DANIEL_MULTI_PORT", "8768"))
    print(f"DEV-001 Daniel multi-mission host listening on http://{host}:{port}/execute")
    print(f"DEV-001 Daniel health available on http://{host}:{port}/health")
    ThreadingHTTPServer((host, port), DanielMultiMissionHandler).serve_forever()


if __name__ == "__main__":
    main()
