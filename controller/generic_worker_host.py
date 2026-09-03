from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import request

from . import daniel_multi_mission_host as daniel
from .generic_engineering_worker import execute_mission as execute_generic_engineering
from .generic_qa_worker import execute_mission as execute_generic_qa
from .runtime_state_store import DurableRuntimeStateStore
from .work_package_controller import QA_ACCEPTED, QA_REJECTED, QAHandoffError, build_qa_mission, validate_qa_result

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
    "recovery_required": False,
    "recovered_checkpoint": None,
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
    return tuple(_scalar(item) for item in body.split(",")) if body else ()


def load_agents(path: Path = AGENT_REGISTRY) -> tuple[RegisteredAgent, ...]:
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
                agents.append(RegisteredAgent(current["id"], current.get("name", ""), current.get("role", ""), tuple(current.get("projects", ())), bool(current.get("worker_enabled", False)), bool(current.get("qa_eligible", False))))
            current = {"id": _scalar(stripped.split(":", 1)[1])}
            continue
        if current is None or ":" not in stripped:
            continue
        key, value = (item.strip() for item in stripped.split(":", 1))
        if key in {"name", "role"}:
            current[key] = _scalar(value)
        elif key == "projects":
            current[key] = _list(value)
        elif key in {"worker_enabled", "qa_eligible"}:
            current[key] = _bool(value)
    if current:
        agents.append(RegisteredAgent(current["id"], current.get("name", ""), current.get("role", ""), tuple(current.get("projects", ())), bool(current.get("worker_enabled", False)), bool(current.get("qa_eligible", False))))
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
    if project and project.strip().lower() not in {item.lower() for item in agent.projects}:
        raise ValueError(f"{agent.agent_id} is not authorized for project {project}")


def configured_agent() -> RegisteredAgent:
    agent = get_agent(os.environ.get("RVSC_WORKER_AGENT_ID", "DEV-001"))
    validate_worker(agent)
    return agent


def select_registered_qa_agent(implementer_id: str, project: str) -> RegisteredAgent:
    implementer = implementer_id.strip().upper()
    normalized_project = project.strip().lower()
    candidates = [
        agent for agent in load_agents()
        if agent.worker_enabled
        and agent.qa_eligible
        and agent.agent_id.strip().upper() != implementer
        and normalized_project in {item.strip().lower() for item in agent.projects}
    ]
    if not candidates:
        raise QAHandoffError("missing QA candidate")
    return sorted(candidates, key=lambda item: item.agent_id)[0]


def is_legacy_daniel_mission(mission: dict[str, Any]) -> bool:
    """Keep historical qualification contracts on Daniel's legacy handlers."""
    wp_id = str(mission.get("wp_id", "")).strip().upper()
    return wp_id.startswith("SEM-DANIEL-")


def _state_store() -> DurableRuntimeStateStore:
    default = RVSC_ROOT / ".rvsc" / "runtime"
    return DurableRuntimeStateStore(Path(os.environ.get("RVSC_RUNTIME_STATE_DIR", str(default))))


def _snapshot_state() -> dict[str, Any]:
    with _STATE_LOCK:
        state = dict(_RUNTIME_STATE)
    state["checkpoint_evidence"] = list(state.get("checkpoint_evidence", ()))
    return state


def _persist_runtime_state() -> None:
    _state_store().save(configured_agent().agent_id, _snapshot_state())


def _set_runtime_state(*, persist: bool = True, **updates: Any) -> None:
    with _STATE_LOCK:
        _RUNTIME_STATE.update(updates)
        _RUNTIME_STATE["last_activity"] = _utc_now()
    if persist:
        _persist_runtime_state()


def _checkpoint(name: str, evidence: tuple[str, ...] = ()) -> None:
    run_id = next((item.split(":", 1)[1] for item in evidence if item.startswith("run_id:")), None)
    updates: dict[str, Any] = {"last_checkpoint": name, "checkpoint_evidence": tuple(evidence)}
    if run_id:
        updates["last_run_id"] = run_id
    _set_runtime_state(**updates)


def _restore_runtime_state() -> bool:
    restored = _state_store().load(configured_agent().agent_id)
    if restored is None:
        return False
    filtered = {key: value for key, value in restored.items() if key in _RUNTIME_STATE}
    filtered["checkpoint_evidence"] = tuple(filtered.get("checkpoint_evidence", ()))
    previous_checkpoint = filtered.get("last_checkpoint")
    interrupted = bool(filtered.get("active_mission"))
    with _STATE_LOCK:
        _RUNTIME_STATE.update(filtered)
        _RUNTIME_STATE["recovery_required"] = interrupted
        _RUNTIME_STATE["recovered_checkpoint"] = previous_checkpoint
        if interrupted:
            _RUNTIME_STATE["last_checkpoint"] = "runtime_recovered"
            evidence = list(_RUNTIME_STATE.get("checkpoint_evidence", ()))
            if previous_checkpoint:
                evidence.append(f"recovered_checkpoint:{previous_checkpoint}")
            evidence.append("durable_state:restored")
            _RUNTIME_STATE["checkpoint_evidence"] = tuple(evidence)
        _RUNTIME_STATE["last_activity"] = _utc_now()
    _persist_runtime_state()
    return True


def health_payload() -> dict[str, Any]:
    agent = configured_agent()
    with _STATE_LOCK:
        state = dict(_RUNTIME_STATE)
    credential_ready = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    execution_path = "independent_qa" if agent.qa_eligible else "generic_engineering"
    return {
        "protocol": "rvsc.worker.health.v1",
        "worker": agent.agent_id,
        "name": agent.name,
        "role": agent.role,
        "service": "rvsc-generic-worker",
        "ready": agent.worker_enabled and credential_ready and state["active_mission"] is None and not state["recovery_required"],
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
        "recovery_required": state["recovery_required"],
        "recovered_checkpoint": state["recovered_checkpoint"],
        "durable_state": True,
        "generic_engineering": not agent.qa_eligible,
        "generic_qa": agent.qa_eligible,
        "execution_path": execution_path,
    }


def dispatch_qa_payload(payload: dict[str, Any]) -> dict[str, Any]:
    endpoint = os.environ.get("RVSC_QA_WORKER_URL", "http://127.0.0.1:8771/execute").strip()
    if not endpoint:
        raise QAHandoffError("QA dispatch endpoint is not configured")
    outbound = request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    timeout = float(os.environ.get("RVSC_QA_DISPATCH_TIMEOUT_SECONDS", "300"))
    with request.urlopen(outbound, timeout=timeout) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise QAHandoffError("malformed QA dispatch response")
    return decoded


def automatic_qa_handoff(implementer: RegisteredAgent, mission: dict[str, Any], engineering_result: dict[str, Any]) -> dict[str, Any]:
    try:
        qa_agent = select_registered_qa_agent(implementer.agent_id, str(mission.get("project", "")))
        qa_mission = build_qa_mission(engineering_mission=mission, engineering_result=engineering_result, qa_agent_id=qa_agent.agent_id)
        _checkpoint("qa_handoff_dispatching", (f"qa_agent:{qa_agent.agent_id}", f"engineering_commit:{qa_mission['engineering_commit_sha']}", f"engineering_branch:{qa_mission['engineering_branch']}"))
        qa_result = dispatch_qa_payload({"protocol": "rvsc.worker.v1", "mission": qa_mission})
        verdict, evidence = validate_qa_result(qa_result)
    except Exception as exc:
        return {
            **engineering_result,
            "success": False,
            "qa_handoff": {"success": False, "summary": str(exc)},
            "summary": f"engineering completed but automatic QA handoff blocked progression: {exc}",
        }

    combined = {
        **engineering_result,
        "success": verdict == QA_ACCEPTED,
        "verdict": verdict,
        "qa_evidence": list(evidence),
        "qa_handoff": {
            "success": verdict == QA_ACCEPTED,
            "qa_agent_id": qa_agent.agent_id,
            "verdict": verdict,
            "evidence": list(evidence),
            "engineering_branch": qa_mission["engineering_branch"],
            "engineering_commit_sha": qa_mission["engineering_commit_sha"],
        },
    }
    if verdict == QA_REJECTED:
        combined["summary"] = "automatic QA rejected the engineering result; progression blocked"
        _checkpoint("qa_rejected", evidence)
    else:
        _checkpoint("qa_accepted", evidence)
    return combined


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
        raise ValueError(f"worker configured for {configured.agent_id}, mission requested {requested_id}")
    project = str(mission.get("project", "")).strip()
    validate_worker(configured, project or None)
    with _STATE_LOCK:
        recovery_required = bool(_RUNTIME_STATE["recovery_required"])
        active_mission = _RUNTIME_STATE["active_mission"]
    if recovery_required:
        raise RuntimeError(f"durable recovery required for interrupted mission {active_mission}; refusing duplicate dispatch")

    wp_id = str(mission.get("wp_id", "")).strip() or "unknown"
    _set_runtime_state(active_mission=wp_id, last_result="acknowledged", last_checkpoint="mission_acknowledged", checkpoint_evidence=(), recovery_required=False, recovered_checkpoint=None)
    try:
        if configured.qa_eligible:
            result = execute_generic_qa(agent_id=configured.agent_id, agent_name=configured.name, role=configured.role, qa_eligible=True, mission=mission, checkpoint=_checkpoint)
        elif configured.agent_id == "DEV-001" and is_legacy_daniel_mission(mission):
            result = daniel.execute_payload(payload)
        else:
            result = execute_generic_engineering(agent_id=configured.agent_id, agent_name=configured.name, role=configured.role, mission=mission, checkpoint=_checkpoint)
        if not configured.qa_eligible and result.get("success"):
            result = automatic_qa_handoff(configured, mission, result)
        _set_runtime_state(last_result="success" if result.get("success") else "failed", last_run_id=result.get("run_id") or _RUNTIME_STATE.get("last_run_id"))
        return result
    except Exception as exc:
        _set_runtime_state(last_result="failed", last_checkpoint="execution_failed", checkpoint_evidence=(f"failure:{exc}",))
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
            result = execute_payload(json.loads(self.rfile.read(length).decode("utf-8")))
            self._send_json(200, result)
        except Exception as exc:
            self._send_json(500, {"success": False, "summary": str(exc), "evidence": ["worker_host:rvsc-generic"], "retryable": False})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[RVSCGenericWorker] {fmt % args}")


def main() -> None:
    agent = configured_agent()
    host = os.environ.get("RVSC_WORKER_HOST", "127.0.0.1")
    port = int(os.environ.get("RVSC_WORKER_PORT", "8770"))
    restored = _restore_runtime_state()
    with _STATE_LOCK:
        recovery_required = bool(_RUNTIME_STATE["recovery_required"])
        active_mission = _RUNTIME_STATE["active_mission"]
    if not restored:
        _checkpoint("worker_started")
    elif not recovery_required:
        _checkpoint("worker_restarted", ("durable_state:restored",))
    print(f"{agent.agent_id} {agent.name} generic worker host listening on http://{host}:{port}/execute")
    print(f"{agent.agent_id} {agent.name} health available on http://{host}:{port}/health")
    if recovery_required:
        print(f"{agent.agent_id} durable recovery required for interrupted mission {active_mission}")
    ThreadingHTTPServer((host, port), GenericWorkerHandler).serve_forever()


if __name__ == "__main__":
    main()
