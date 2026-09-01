from __future__ import annotations

import json
import os
import threading
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

# The multi-mission process temporarily overrides the legacy host's bounded
# mission constants while SEM-DANIEL-003 executes. Every path that can enter
# legacy.execute_payload or legacy._execute_sem_daniel must share this lock so
# another request in this same process cannot observe those temporary values.
_EXECUTION_LOCK = threading.Lock()


def _execute_003(api_key: str, mission: dict[str, Any], run_id: str, started: str) -> dict[str, Any]:
    if mission.get("base_branch") != SEM_DANIEL_003_BASE_BRANCH:
        raise ValueError(f"{SEM_DANIEL_003_WP} requires base branch {SEM_DANIEL_003_BASE_BRANCH}")
    if mission.get("work_branch") != SEM_DANIEL_003_BRANCH:
        raise ValueError(f"{SEM_DANIEL_003_WP} requires branch {SEM_DANIEL_003_BRANCH}")
    if tuple(mission.get("allowed_paths", ())) != SEM_DANIEL_003_ALLOWED:
        raise ValueError(f"{SEM_DANIEL_003_WP} allowed path contract mismatch")

    # Reuse the already-proven controlled engineering implementation without
    # changing its normal SEM-DANIEL-002 contract. The lock protects the
    # temporary mission-contract override from all requests handled by this
    # multi-mission process.
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

    if mission.get("wp_id") != SEM_DANIEL_003_WP:
        # Serialize delegation with SEM-DANIEL-003's temporary overrides.
        # Without this, a concurrent SEM-DANIEL-002 request could observe the
        # 003 constants and be misrouted to acknowledgement-only behavior.
        with _EXECUTION_LOCK:
            return legacy.execute_payload(payload)

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    run_id = f"RVSC-DAN-{legacy.uuid.uuid4().hex[:12].upper()}"
    started = legacy._utc_now()
    return _execute_003(api_key, mission, run_id, started)


class DanielMultiMissionHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/execute":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = execute_payload(payload)
            encoded = json.dumps(result).encode("utf-8")
            self.send_response(200)
        except Exception as exc:
            encoded = json.dumps({"success": False, "summary": str(exc), "evidence": ["worker_host:daniel-multi-mission"], "retryable": False}).encode("utf-8")
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[DanielMultiHost] {fmt % args}")


def main() -> None:
    host = os.environ.get("RVSC_DANIEL_HOST", "127.0.0.1")
    port = int(os.environ.get("RVSC_DANIEL_MULTI_PORT", "8768"))
    print(f"DEV-001 Daniel multi-mission host listening on http://{host}:{port}/execute")
    ThreadingHTTPServer((host, port), DanielMultiMissionHandler).serve_forever()


if __name__ == "__main__":
    main()
