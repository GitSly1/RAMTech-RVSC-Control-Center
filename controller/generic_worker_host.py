from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import daniel_multi_mission_host as daniel


RVSC_ROOT = Path(__file__).resolve().parents[1]
AGENT_REGISTRY = RVSC_ROOT / "config" / "agents.yaml"

_STATE_LOCK = threading.Lock()
_RUNTIME_STATE: dict[str, Any] = {
    "active_mission": None,
    "last_run_id": None,
    "last_activity": None,
    "last_result": None,
    "last_checkpoint": None,
    "checkpoint_evidence": (),
}


@dataclass(frozen=True)
class RegisteredAgent:
    agent_id: str
    name: str
    role: str
    projects: tuple[str, ...]
    worker_enabled: bool
    qa_eligible: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scalar(value: str) -> str:
    return value.strip().strip('"').strip("'")


def _bool(value: str) -> bool:
    return _scalar(value).lower() == "true"


def _list(value: str) -> tuple[str, ...]:
    text = value.strip()
    if not (text.startswith("[") and text.endswith("]")):
        return ()
    body = text[1:-1].strip()
    if not body:
        return ()
    return tuple(_scalar(item) for item in body.split(","))


def load_agents(path: Path = AGENT_REGISTRY) -> tuple[RegisteredAgent, ...]:
    """
    Parse the constrained RVSC agent registry without introducing a PyYAML
    runtime dependency.

    This intentionally parses only the fields required by the worker host.
    """
    text = path.read_text(encoding="utf-8")
    agents: list[RegisteredAgent] = []
    current: dict[str, Any] | None = None
    inside_agents = False

    for raw in text.splitlines():
        stripped = raw.strip()

        if stripped == "agents:":
            inside_agents = True
            continue

        if stripped == "policies:":
            break

        if not inside_agents or not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- id:"):
            if current:
                agents.append(
                    RegisteredAgent(
                        agent_id=current["id"],
                        name=current.get("name", ""),
                        role=current.get("role", ""),
                        projects=tuple(current.get("projects", ())),
                        worker_enabled=bool(current.get("worker_enabled", False)),
                        qa_eligible=bool(current.get("qa_eligible", False)),
                    )
                )
            current = {"id": _scalar(stripped.split(":", 1)[1])}
            continue

        if current is None or ":" not in stripped:
            continue

        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()

        if key in {"name", "role"}:
            current[key] = _scalar(value)
        elif key == "projects":
            current[key] = _list(value)
        elif key in {"worker_enabled", "qa_eligible"}:
            current[key] = _bool(value)

    if current:
        agents.append(
            RegisteredAgent(
                agent_id=current["id"],
                name=current.get("name", ""),
                role=current.get("role", ""),
                projects=tuple(current.get("projects", ())),
                worker_enabled=bool(current.get("worker_enabled", False)),
                qa_eligible=bool(current.get("qa_eligible", False)),
            )
        )

    return tuple(agents)


def get_agent(agent_id: str) -> RegisteredAgent:
    normalized = agent_id.strip().upper()
    for agent in load_agents():
        if agent.agent_id.upper() == normalized:
            return agent
    raise ValueError(f"unregistered RVSC agent: {agent_id}")


def validate_worker(agent: RegisteredAgent, project: str | None = None) -> None:
    if not agent.worker_enabled:
        raise ValueError(f"{agent.agent_id} is not worker-enabled")

    if project:
        normalized_project = project.strip().lower()
        allowed = {item.lower() for item in agent.projects}
        if normalized_project not in allowed:
            raise ValueError(
                f"{agent.agent_id} is not authorized for project {project}"
            )


def _set_runtime_state(**updates: Any) -> None:
    with _STATE_LOCK:
        _RUNTIME_STATE.update(updates)
        _RUNTIME_STATE["last_activity"] = _utc_now()


def _checkpoint(name: str, evidence: tuple[str, ...] = ()) -> None:
    _set_runtime_state(
        last_checkpoint=name,
        checkpoint_evidence=tuple(evidence),
    )


def configured_agent() -> RegisteredAgent:
    agent_id = os.environ.get("RVSC_WORKER_AGENT_ID", "DEV-001")
    agent = get_agent(agent_id)
    validate_worker(agent)
    return agent


def health_payload() -> dict[str, Any]:
    agent = configured_agent()

    with _STATE_LOCK:
        state = dict(_RUNTIME_STATE)

    credential_ready = bool(os.environ.get("OPENAI_API_KEY", "").strip())

    return {
        "protocol": "rvsc.worker.health.v1",
        "worker": agent.agent_id,
        "name": agent.name,
        "role": agent.role,
        "service": "rvsc-generic-worker",
        "ready": (
            agent.worker_enabled
            and credential_ready
            and state["active_mission"] is None
        ),
        "credential_ready": credential_ready,
        "worker_enabled": agent.worker_enabled,
        "qa_eligible": agent.qa_eligible,
        "projects": list(agent.projects),
        "busy": state["active_mission"] is not None,
        "active_mission": state["active_mission"],
        "last_run_id": state["last_run_id"],
        "last_activity": state["last_activity"],
        "last_result": state["last_result"],
        "last_checkpoint": state["last_checkpoint"],
        "checkpoint_evidence": list(state["checkpoint_evidence"]),
    }


def execute_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("protocol") != "rvsc.worker.v1":
        raise ValueError("unsupported protocol")

    mission = payload.get("mission")
    if not isinstance(mission, dict):
        raise ValueError("mission must be an object")

    requested_id = str(mission.get("agent_id", "")).strip()
    if not requested_id:
        raise ValueError("mission agent_id is required")

    configured = configured_agent()

    if requested_id != configured.agent_id:
        raise ValueError(
            f"worker configured for {configured.agent_id}, "
            f"mission requested {requested_id}"
        )

    project = str(mission.get("project", "")).strip()
    validate_worker(configured, project or None)

    wp_id = str(mission.get("wp_id", "")).strip() or "unknown"

    _set_runtime_state(
        active_mission=wp_id,
        last_result="acknowledged",
        last_checkpoint="mission_acknowledged",
        checkpoint_evidence=(),
    )

    try:
        # Preserve the already-qualified Daniel mission runtime unchanged.
        if configured.agent_id == "DEV-001":
            result = daniel.execute_payload(payload)
            _set_runtime_state(
                last_result="success" if result.get("success") else "failed"
            )
            return result

        # Other agents are now valid executable worker identities, but their
        # mission-specific runners are intentionally introduced separately.
        # This prevents a generic host from silently pretending that a worker
        # performed engineering or QA that has no controlled runner yet.
        raise RuntimeError(
            f"{configured.agent_id} worker identity is active but no "
            f"controlled mission runner is registered for work package {wp_id}"
        )

    except Exception as exc:
        _set_runtime_state(
            last_result="failed",
            last_checkpoint="execution_failed",
            checkpoint_evidence=(f"failure:{exc}",),
        )
        raise

    finally:
        _set_runtime_state(active_mission=None)


class GenericWorkerHandler(BaseHTTPRequestHandler):
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
            payload = json.loads(
                self.rfile.read(length).decode("utf-8")
            )
            result = execute_payload(payload)
            self._send_json(200, result)

        except Exception as exc:
            self._send_json(
                500,
                {
                    "success": False,
                    "summary": str(exc),
                    "evidence": ["worker_host:rvsc-generic"],
                    "retryable": False,
                },
            )

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[RVSCGenericWorker] {fmt % args}")


def main() -> None:
    agent = configured_agent()
    host = os.environ.get("RVSC_WORKER_HOST", "127.0.0.1")
    port = int(os.environ.get("RVSC_WORKER_PORT", "8770"))

    _checkpoint("worker_started")

    print(
        f"{agent.agent_id} {agent.name} generic worker host listening "
        f"on http://{host}:{port}/execute"
    )
    print(
        f"{agent.agent_id} {agent.name} health available "
        f"on http://{host}:{port}/health"
    )

    ThreadingHTTPServer(
        (host, port),
        GenericWorkerHandler,
    ).serve_forever()


if __name__ == "__main__":
    main()
