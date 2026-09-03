from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from . import daniel_multi_mission_host as daniel
from .generic_engineering_worker import execute_mission as execute_generic_engineering
from .generic_engineering_worker import recover_controlled_workspace, resume_persisted_engineering_result
from .generic_qa_worker import execute_mission as execute_generic_qa
from .runtime_state_store import DurableRuntimeStateStore, sanitize_for_persistence
from .work_package_controller import QA_ACCEPTED, QA_REJECTED, QAHandoffError, build_qa_mission, engineering_commit_sha, validate_qa_result

RVSC_ROOT = Path(__file__).resolve().parents[1]
AGENT_REGISTRY = RVSC_ROOT / "config" / "agents.yaml"
_STATE_LOCK = threading.Lock()
_EXECUTION_LOCK = threading.Lock()
_RECOVERY_THREAD: threading.Thread | None = None
_RUNTIME_STATE: dict[str, Any] = {
    "active_mission": None,
    "active_run_id": None,
    "last_run_id": None,
    "last_activity": None,
    "last_result": None,
    "last_checkpoint": None,
    "checkpoint_evidence": (),
    "recovery_required": False,
    "recovered_checkpoint": None,
    "lifecycle_state": "idle",
    "recovery_context": None,
    "recovery_digest": None,
    "recovery_attempted": False,
    "engineering_result": None,
    "qa_dispatch_started": False,
    "terminal_recovery": None,
}
LEGACY_DANIEL_WP_IDS = frozenset({"SEM-DANIEL-002", "SEM-DANIEL-003"})
_RECOVERY_REQUIRED_FIELDS = ("agent_id", "project", "repository", "wp_id", "run_id")
_CLEAN_REEXECUTION_CHECKPOINTS = frozenset({"mission_acknowledged", "preflight_passed", "proposal_received"})
_WORKSPACE_RESTORE_CHECKPOINTS = frozenset({"implementation_applied", "tests_passed"})
_MAX_HEALTH_RESPONSE_BYTES = 1024 * 1024


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


def configured_qa_worker_endpoint() -> str:
    endpoint = os.environ.get("RVSC_QA_WORKER_URL", "http://127.0.0.1:8771/execute").strip()
    if not endpoint:
        raise QAHandoffError("QA dispatch endpoint is not configured", category="transport_failure")
    return endpoint


def qa_health_endpoint(execution_endpoint: str) -> str:
    try:
        parsed = parse.urlsplit(execution_endpoint)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise QAHandoffError("QA dispatch endpoint is invalid", category="qa_endpoint_invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or port is None and not parsed.netloc:
        raise QAHandoffError("QA dispatch endpoint is invalid", category="qa_endpoint_invalid")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise QAHandoffError("QA dispatch endpoint is invalid", category="qa_endpoint_invalid")
    execution_path = parsed.path.rstrip("/")
    prefix, separator, final_segment = execution_path.rpartition("/")
    if not separator or final_segment != "execute":
        raise QAHandoffError("QA dispatch endpoint does not identify an execution resource", category="qa_endpoint_invalid")
    health_path = f"{prefix}/health" if prefix else "/health"
    return parse.urlunsplit((parsed.scheme, parsed.netloc, health_path, parsed.query, ""))


def _qa_health_timeout() -> float:
    try:
        timeout = float(os.environ.get("RVSC_QA_HEALTH_TIMEOUT_SECONDS", "10"))
    except ValueError as exc:
        raise QAHandoffError("QA health timeout is invalid", category="qa_endpoint_invalid") from exc
    if timeout <= 0:
        raise QAHandoffError("QA health timeout is invalid", category="qa_endpoint_invalid")
    return timeout


def _fetch_qa_health(execution_endpoint: str) -> dict[str, Any]:
    outbound = request.Request(qa_health_endpoint(execution_endpoint), method="GET")
    try:
        with request.urlopen(outbound, timeout=_qa_health_timeout()) as response:
            status = response.getcode()
            raw = response.read(_MAX_HEALTH_RESPONSE_BYTES + 1)
    except error.HTTPError as exc:
        raise QAHandoffError("QA health endpoint returned an HTTP error", category="transport_failure", http_status=exc.code, retryable=True) from exc
    except QAHandoffError:
        raise
    except (error.URLError, TimeoutError, OSError) as exc:
        raise QAHandoffError("QA health endpoint is unreachable", category="transport_failure", retryable=True) from exc
    if not isinstance(status, int) or not 200 <= status < 300:
        raise QAHandoffError("QA health endpoint did not return success", category="transport_failure", http_status=status if isinstance(status, int) else None, retryable=True)
    if len(raw) > _MAX_HEALTH_RESPONSE_BYTES:
        raise QAHandoffError("QA health response is malformed", category="qa_endpoint_invalid")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise QAHandoffError("QA health response is malformed", category="qa_endpoint_invalid") from exc
    if not isinstance(decoded, dict):
        raise QAHandoffError("QA health response is malformed", category="qa_endpoint_invalid")
    return decoded


def select_registered_qa_agent(implementer_id: str, project: str, execution_endpoint: str | None = None) -> RegisteredAgent:
    health = _fetch_qa_health(execution_endpoint or configured_qa_worker_endpoint())
    if health.get("protocol") != "rvsc.worker.health.v1" or health.get("service") != "rvsc-generic-worker":
        raise QAHandoffError("QA health response uses an unsupported protocol", category="qa_endpoint_invalid")
    live_id = health.get("worker")
    if not isinstance(live_id, str) or not live_id.strip():
        raise QAHandoffError("QA health response has no worker identity", category="qa_endpoint_invalid")
    if health.get("ready") is not True:
        raise QAHandoffError("QA endpoint worker is unavailable", category="transport_failure", retryable=True)
    if health.get("worker_enabled") is not True or health.get("qa_eligible") is not True:
        raise QAHandoffError("QA endpoint worker is not worker-enabled and QA-eligible", category="qa_endpoint_invalid")

    normalized_live_id = live_id.strip().upper()
    matches = [agent for agent in load_agents() if agent.agent_id.strip().upper() == normalized_live_id]
    if len(matches) != 1:
        raise QAHandoffError("QA endpoint worker identity is not uniquely registered", category="qa_endpoint_invalid")
    agent = matches[0]
    if agent.agent_id.strip().upper() == implementer_id.strip().upper():
        raise QAHandoffError("QA endpoint worker is not independent from the implementer", category="qa_endpoint_invalid")
    if not agent.worker_enabled:
        raise QAHandoffError("QA endpoint worker is not worker-enabled", category="qa_endpoint_invalid")
    if not agent.qa_eligible:
        raise QAHandoffError("QA endpoint worker is not QA-eligible", category="qa_endpoint_invalid")
    normalized_project = project.strip().lower()
    if not normalized_project or normalized_project not in {item.strip().lower() for item in agent.projects}:
        raise QAHandoffError("QA endpoint worker is not authorized for the engineering project", category="qa_endpoint_invalid")
    return agent


def is_legacy_daniel_mission(mission: dict[str, Any]) -> bool:
    return str(mission.get("wp_id", "")).strip().upper() in LEGACY_DANIEL_WP_IDS


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


def _mission_context(mission: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_for_persistence(mission)
    if not isinstance(sanitized, dict):
        raise ValueError("mission recovery context is invalid")
    return sanitized


def _context_digest(context: dict[str, Any]) -> str:
    encoded = json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_recovery_context(context: Any, digest: Any = None) -> dict[str, Any]:
    if not isinstance(context, dict):
        raise ValueError("persisted recovery context is missing")
    for key in _RECOVERY_REQUIRED_FIELDS:
        value = context.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"persisted recovery context is incomplete: {key}")
    calculated = _context_digest(context)
    if not isinstance(digest, str) or digest != calculated:
        raise ValueError("persisted recovery context digest mismatch")
    return context


def _restore_runtime_state() -> bool:
    try:
        restored = _state_store().load(configured_agent().agent_id)
    except ValueError as exc:
        with _STATE_LOCK:
            _RUNTIME_STATE.update({
                "active_mission": None,
                "active_run_id": None,
                "recovery_required": True,
                "lifecycle_state": "recovery_failed",
                "last_result": "failed",
                "last_checkpoint": "recovery_failed",
                "checkpoint_evidence": (f"recovery_refused:{exc}",),
                "recovery_attempted": True,
                "terminal_recovery": {"wp_id": None, "run_id": None, "result": "recovery_failed", "completed_at": _utc_now()},
                "last_activity": _utc_now(),
            })
        return True
    if restored is None:
        return False
    filtered = {key: value for key, value in restored.items() if key in _RUNTIME_STATE}
    filtered["checkpoint_evidence"] = tuple(filtered.get("checkpoint_evidence", ()))
    previous_checkpoint = filtered.get("last_checkpoint")
    interrupted = bool(filtered.get("active_mission"))
    recovery_error: str | None = None
    if interrupted:
        try:
            context = _validate_recovery_context(filtered.get("recovery_context"), filtered.get("recovery_digest"))
            if context["wp_id"] != filtered.get("active_mission") or context["run_id"] != filtered.get("active_run_id"):
                raise ValueError("persisted recovery identity mismatch")
            if context["agent_id"].strip().upper() != configured_agent().agent_id.upper():
                raise ValueError("persisted recovery agent mismatch")
            if filtered.get("qa_dispatch_started"):
                raise ValueError("QA dispatch result is unknown")
            if filtered.get("recovery_attempted") or filtered.get("lifecycle_state") in {"recovering", "recovery_failed"}:
                raise ValueError("previous recovery was interrupted or failed")
        except ValueError as exc:
            recovery_error = str(exc)
    with _STATE_LOCK:
        _RUNTIME_STATE.update(filtered)
        _RUNTIME_STATE["recovery_required"] = interrupted
        _RUNTIME_STATE["recovered_checkpoint"] = previous_checkpoint
        evidence = list(_RUNTIME_STATE.get("checkpoint_evidence", ()))
        if interrupted:
            if previous_checkpoint:
                evidence.append(f"recovered_checkpoint:{previous_checkpoint}")
            evidence.append("durable_state:restored")
            if recovery_error:
                _RUNTIME_STATE["lifecycle_state"] = "recovery_failed"
                _RUNTIME_STATE["last_checkpoint"] = "recovery_failed"
                _RUNTIME_STATE["recovery_attempted"] = True
                evidence.append(f"recovery_refused:{recovery_error}")
            else:
                _RUNTIME_STATE["lifecycle_state"] = "recovery_required"
                _RUNTIME_STATE["last_checkpoint"] = "runtime_recovered"
        elif filtered.get("lifecycle_state") not in {"recovered", "recovery_failed"}:
            _RUNTIME_STATE["lifecycle_state"] = "idle"
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
        "active_run_id": state["active_run_id"],
        "last_run_id": state["last_run_id"],
        "last_activity": state["last_activity"],
        "last_result": state["last_result"],
        "last_checkpoint": state["last_checkpoint"],
        "checkpoint_evidence": list(state["checkpoint_evidence"]),
        "recovery_required": state["recovery_required"],
        "recovered_checkpoint": state["recovered_checkpoint"],
        "lifecycle_state": state["lifecycle_state"],
        "terminal_recovery": state["terminal_recovery"],
        "durable_state": True,
        "generic_engineering": not agent.qa_eligible,
        "generic_qa": agent.qa_eligible,
        "execution_path": execution_path,
    }


def _decode_qa_response(raw: bytes, *, http_status: int | None = None) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace")
    try:
        decoded = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise QAHandoffError("malformed QA dispatch response", category="malformed_qa_response", response=text, http_status=http_status) from exc
    if not isinstance(decoded, dict):
        raise QAHandoffError("malformed QA dispatch response", category="malformed_qa_response", response=decoded, http_status=http_status)
    return decoded


def dispatch_qa_payload(payload: dict[str, Any], *, endpoint: str | None = None) -> dict[str, Any]:
    selected_endpoint = endpoint or configured_qa_worker_endpoint()
    outbound = request.Request(selected_endpoint, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    timeout = float(os.environ.get("RVSC_QA_DISPATCH_TIMEOUT_SECONDS", "300"))
    try:
        with request.urlopen(outbound, timeout=timeout) as response:
            return _decode_qa_response(response.read(), http_status=response.getcode())
    except error.HTTPError as exc:
        decoded = _decode_qa_response(exc.read(), http_status=exc.code)
        summary = decoded.get("summary")
        detail = summary.strip() if isinstance(summary, str) and summary.strip() else str(exc.reason)
        raise QAHandoffError(f"QA worker HTTP {exc.code}: {detail}", category="qa_http_error", response=decoded, http_status=exc.code, retryable=False) from exc
    except QAHandoffError:
        raise
    except (error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise QAHandoffError(f"QA transport failure: {reason}", category="transport_failure", retryable=True) from exc


def _qa_failure_result(engineering_result: dict[str, Any], mission: dict[str, Any], exc: QAHandoffError) -> dict[str, Any]:
    branch = str(engineering_result.get("work_branch") or engineering_result.get("branch") or mission.get("work_branch") or mission.get("branch") or "").strip()
    commit_sha = engineering_commit_sha(engineering_result)
    project = str(mission.get("project") or engineering_result.get("project") or "").strip()
    repository = str(mission.get("repository") or engineering_result.get("repository") or "").strip()
    handoff: dict[str, Any] = {"success": False, "classification": exc.category, "summary": str(exc), "retryable": exc.retryable, "engineering_project": project, "engineering_repository": repository, "engineering_branch": branch, "engineering_commit_sha": commit_sha}
    if exc.http_status is not None:
        handoff["http_status"] = exc.http_status
    if exc.response is not None:
        handoff["response"] = exc.response
    return {**engineering_result, "success": False, "qa_handoff": handoff, "summary": f"engineering completed but automatic QA handoff blocked progression: {exc}"}


def automatic_qa_handoff(implementer: RegisteredAgent, mission: dict[str, Any], engineering_result: dict[str, Any]) -> dict[str, Any]:
    try:
        endpoint = configured_qa_worker_endpoint()
        qa_agent = select_registered_qa_agent(implementer.agent_id, str(mission.get("project", "")), endpoint)
        qa_mission = build_qa_mission(engineering_mission=mission, engineering_result=engineering_result, qa_agent_id=qa_agent.agent_id)
        _checkpoint("qa_handoff_dispatching", (f"qa_agent:{qa_agent.agent_id}", f"engineering_commit:{qa_mission['engineering_commit_sha']}", f"engineering_branch:{qa_mission['engineering_branch']}"))
        qa_result = dispatch_qa_payload({"protocol": "rvsc.worker.v1", "mission": qa_mission}, endpoint=endpoint)
        verdict, evidence = validate_qa_result(qa_result)
    except QAHandoffError as exc:
        return _qa_failure_result(engineering_result, mission, exc)
    except Exception as exc:
        return _qa_failure_result(engineering_result, mission, QAHandoffError(str(exc)))
    combined = {**engineering_result, "success": verdict == QA_ACCEPTED, "verdict": verdict, "qa_evidence": list(evidence), "qa_handoff": {"success": verdict == QA_ACCEPTED, "classification": "qa_accepted" if verdict == QA_ACCEPTED else "qa_rejected", "qa_agent_id": qa_agent.agent_id, "verdict": verdict, "evidence": list(evidence), "engineering_project": qa_mission["engineering_project"], "engineering_repository": qa_mission["engineering_repository"], "engineering_branch": qa_mission["engineering_branch"], "engineering_commit_sha": qa_mission["engineering_commit_sha"]}}
    if verdict == QA_REJECTED:
        combined["summary"] = "automatic QA rejected the engineering result; progression blocked"
        _checkpoint("qa_rejected", evidence)
    else:
        _checkpoint("qa_accepted", evidence)
    return combined


def _persist_engineering_result(result: dict[str, Any]) -> None:
    if not isinstance(result, dict):
        raise ValueError("engineering result must be an object")
    _set_runtime_state(engineering_result=result, last_run_id=result.get("run_id"), last_checkpoint="engineering_result_persisted")


def _run_worker(configured: RegisteredAgent, mission: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if configured.qa_eligible:
        return execute_generic_qa(agent_id=configured.agent_id, agent_name=configured.name, role=configured.role, qa_eligible=True, mission=mission, checkpoint=_checkpoint)
    if configured.agent_id == "DEV-001" and is_legacy_daniel_mission(mission):
        return daniel.execute_payload(payload)
    return execute_generic_engineering(agent_id=configured.agent_id, agent_name=configured.name, role=configured.role, mission=mission, checkpoint=_checkpoint, persist_result=_persist_engineering_result)


def _validate_persisted_result_identity(result: dict[str, Any], mission: dict[str, Any]) -> None:
    if str(result.get("run_id", "")).strip() != str(mission.get("run_id", "")).strip():
        raise RuntimeError("persisted engineering result run identity mismatch")
    if str(result.get("project", "")).strip() != str(mission.get("project", "")).strip():
        raise RuntimeError("persisted engineering result project mismatch")
    if str(result.get("repository", "")).strip() != str(mission.get("repository", "")).strip():
        raise RuntimeError("persisted engineering result repository mismatch")
    if str(result.get("work_branch", "")).strip() != str(mission.get("work_branch", "")).strip():
        raise RuntimeError("persisted engineering result branch mismatch")


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
    recovery = payload.get("recovery") is True
    if not _EXECUTION_LOCK.acquire(blocking=False):
        raise RuntimeError("worker execution already in progress")
    try:
        with _STATE_LOCK:
            state = dict(_RUNTIME_STATE)
        context = _mission_context(mission)
        digest = _context_digest(context)
        wp_id = str(mission.get("wp_id", "")).strip() or "unknown"
        run_id = str(mission.get("run_id", "")).strip()
        if recovery:
            if not state["recovery_required"]:
                raise RuntimeError("no interrupted mission requires recovery")
            if state["recovery_attempted"] or state["lifecycle_state"] in {"recovering", "recovery_failed"}:
                raise RuntimeError("recovery already attempted; refusing duplicate recovery")
            persisted = _validate_recovery_context(state["recovery_context"], state["recovery_digest"])
            if digest != state["recovery_digest"] or context != persisted:
                raise RuntimeError("recovery mission does not exactly match persisted context")
            if wp_id != state["active_mission"] or run_id != state["active_run_id"]:
                raise RuntimeError("recovery mission identity mismatch")
            _set_runtime_state(lifecycle_state="recovering", recovery_attempted=True, last_checkpoint="recovery_started", checkpoint_evidence=(f"wp_id:{wp_id}", f"run_id:{run_id}"))
        else:
            if state["recovery_required"]:
                raise RuntimeError(f"durable recovery required for interrupted mission {state['active_mission']}; refusing duplicate dispatch")
            if state["active_mission"] is not None:
                raise RuntimeError(f"mission {state['active_mission']} is already executing")
            _set_runtime_state(active_mission=wp_id, active_run_id=run_id or None, last_result="acknowledged", last_checkpoint="mission_acknowledged", checkpoint_evidence=(), recovery_required=False, recovered_checkpoint=None, lifecycle_state="executing", recovery_context=context, recovery_digest=digest, recovery_attempted=False, engineering_result=None, qa_dispatch_started=False, terminal_recovery=None)

        engineering_result = state.get("engineering_result") if recovery else None
        if recovery and engineering_result is not None:
            if not isinstance(engineering_result, dict):
                raise RuntimeError("persisted engineering result is malformed")
            _validate_persisted_result_identity(engineering_result, mission)
            if not engineering_result.get("pushed"):
                engineering_result = resume_persisted_engineering_result(mission, engineering_result, checkpoint=_checkpoint)
                _persist_engineering_result(engineering_result)
        if engineering_result is None:
            if recovery:
                boundary = state.get("recovered_checkpoint") or state.get("last_checkpoint")
                if boundary in _WORKSPACE_RESTORE_CHECKPOINTS:
                    recovery_evidence = recover_controlled_workspace(mission)
                    _checkpoint("recovery_workspace_restored", recovery_evidence + (f"run_id:{run_id}",))
                elif boundary not in _CLEAN_REEXECUTION_CHECKPOINTS:
                    raise RuntimeError("interrupted operation has no proven idempotent recovery boundary")
            engineering_result = _run_worker(configured, mission, payload)
            _persist_engineering_result(engineering_result)
        if not configured.qa_eligible and engineering_result.get("success"):
            if recovery and state.get("qa_dispatch_started"):
                raise RuntimeError("QA handoff was already dispatched; refusing duplicate QA")
            _set_runtime_state(qa_dispatch_started=True, last_checkpoint="qa_handoff_reserved")
            result = automatic_qa_handoff(configured, mission, engineering_result)
        else:
            result = engineering_result

        if recovery and not result.get("success"):
            terminal = {"wp_id": wp_id, "run_id": run_id, "result": "failed", "completed_at": _utc_now()}
            _set_runtime_state(last_result="failed", lifecycle_state="recovery_failed", recovery_required=True, last_checkpoint="recovery_failed", terminal_recovery=terminal)
            return result
        if recovery:
            terminal = {"wp_id": wp_id, "run_id": run_id, "result": "recovered", "completed_at": _utc_now()}
            _set_runtime_state(active_mission=None, active_run_id=None, last_result="success", lifecycle_state="recovered", recovery_required=False, last_checkpoint="recovery_completed", terminal_recovery=terminal, engineering_result=None, qa_dispatch_started=False)
        else:
            _set_runtime_state(active_mission=None, active_run_id=None, last_result="success" if result.get("success") else "failed", lifecycle_state="idle", recovery_required=False, engineering_result=None, qa_dispatch_started=False)
        return result
    except Exception as exc:
        if recovery:
            with _STATE_LOCK:
                active_wp = _RUNTIME_STATE.get("active_mission")
                active_run = _RUNTIME_STATE.get("active_run_id")
            terminal = {"wp_id": active_wp, "run_id": active_run, "result": "recovery_failed", "completed_at": _utc_now()}
            _set_runtime_state(last_result="failed", lifecycle_state="recovery_failed", recovery_required=True, last_checkpoint="recovery_failed", checkpoint_evidence=(f"failure:{exc}",), terminal_recovery=terminal)
        else:
            _set_runtime_state(active_mission=None, active_run_id=None, last_result="failed", lifecycle_state="idle", recovery_required=False, last_checkpoint="execution_failed", checkpoint_evidence=(f"failure:{exc}",), engineering_result=None, qa_dispatch_started=False)
        raise
    finally:
        _EXECUTION_LOCK.release()


def _automatic_recovery_payload() -> dict[str, Any] | None:
    with _STATE_LOCK:
        state = dict(_RUNTIME_STATE)
    if not state.get("recovery_required") or state.get("lifecycle_state") != "recovery_required" or state.get("recovery_attempted"):
        return None
    context = _validate_recovery_context(state.get("recovery_context"), state.get("recovery_digest"))
    if context["wp_id"] != state.get("active_mission") or context["run_id"] != state.get("active_run_id"):
        raise ValueError("persisted recovery identity mismatch")
    if context["agent_id"].strip().upper() != configured_agent().agent_id.upper():
        raise ValueError("persisted recovery agent mismatch")
    return {"protocol": "rvsc.worker.v1", "recovery": True, "mission": context}


def _automatic_recovery_runner() -> None:
    try:
        payload = _automatic_recovery_payload()
        if payload is not None:
            execute_payload(payload)
    except Exception:
        return


def _start_automatic_recovery() -> threading.Thread | None:
    global _RECOVERY_THREAD
    try:
        payload = _automatic_recovery_payload()
    except Exception as exc:
        terminal = {"wp_id": _RUNTIME_STATE.get("active_mission"), "run_id": _RUNTIME_STATE.get("active_run_id"), "result": "recovery_failed", "completed_at": _utc_now()}
        _set_runtime_state(last_result="failed", lifecycle_state="recovery_failed", recovery_required=True, recovery_attempted=True, last_checkpoint="recovery_failed", checkpoint_evidence=(f"failure:{exc}",), terminal_recovery=terminal)
        return None
    if payload is None:
        return None
    thread = threading.Thread(target=_automatic_recovery_runner, name=f"rvsc-recovery-{configured_agent().agent_id}", daemon=True)
    _RECOVERY_THREAD = thread
    thread.start()
    return thread


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
    server = ThreadingHTTPServer((host, port), GenericWorkerHandler)
    print(f"{agent.agent_id} {agent.name} generic worker host listening on http://{host}:{port}/execute")
    print(f"{agent.agent_id} {agent.name} health available on http://{host}:{port}/health")
    if recovery_required:
        print(f"{agent.agent_id} automatically recovering interrupted mission {active_mission}")
        _start_automatic_recovery()
    server.serve_forever()


if __name__ == "__main__":
    main()
