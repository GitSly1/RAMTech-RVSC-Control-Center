"""Controlled runtime supervision, durable dispatch, QA rework, and productivity monitoring."""
from __future__ import annotations

import argparse
import inspect
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from controller.orchestrator import MissionStore, OrchestrationError, WorkerState, select_dispatch

REPOSITORY_ENV_KEYS = ("RVSC_RVSC_REPO", "RVSC_SEMANTIQ_REPO", "RVSC_MOXIE_REPO")
QA_ROUTING_ENV_KEYS = ("RVSC_QA_ENDPOINT", "RVSC_QA_URL", "RVSC_QA_WORKER_ENDPOINT", "RVSC_QA_WORKER_URL")
DEFAULT_QA_ENDPOINT = "http://127.0.0.1:8771/execute"
SUPPORTED_PROJECTS = ("rvsc", "semantiq", "moxie")
_ACTIVE_STATES = {"assigned", "running", "qa_pending", "qa_review"}
_QUEUED_STATES = {"queued", "retryable"}
_ACCEPTED_STATES = {"accepted", "qa_accepted"}


class RuntimeSupervisorError(RuntimeError):
    pass


class RuntimeConflictError(RuntimeSupervisorError):
    pass


@dataclass(frozen=True)
class WorkerConfig:
    agent_id: str
    name: str
    port: int
    role: str
    command: Optional[Tuple[str, ...]] = None
    authorized_projects: Tuple[str, ...] = ()


@dataclass
class HealthResult:
    healthy: bool
    agent_id: Optional[str] = None
    detail: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WorkerStatus:
    agent_id: str
    name: str
    port: int
    running: bool
    ready: bool
    health_result: Dict[str, Any]
    supervisor_owned: bool
    restart_count: int
    work_state: str = "IDLE"
    mission_id: Optional[str] = None
    work_detail: str = ""
    last_material_progress: Optional[float] = None
    no_progress_duration: Optional[float] = None
    material_checkpoint: Optional[str] = None
    material_evidence: Any = None
    reason: str = ""
    recovery_state: str = "NONE"
    recovery_attempts: int = 0
    recovery_outcome: Optional[str] = None
    next_eligible_work: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def golden_team_configs() -> Tuple[WorkerConfig, ...]:
    return (WorkerConfig("OPS-001", "Noah", 8770, "engineering"), WorkerConfig("DEV-001", "Daniel", 8765, "engineering"), WorkerConfig("QA-001", "Quinn", 8771, "qa"))


def production_mission_store_path(environ: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if environ is None else environ
    configured_root = str(env.get("RVSC_STATE_DIR", "")).strip()
    if configured_root:
        root = Path(configured_root).expanduser()
    elif os.name == "nt":
        root = Path(env.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        root = Path(env.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    return root / "RAMTech" / "RVSC" / "mission-store.json"


def _field(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _state_text(value: Any) -> str:
    value = _field(value, "status", "state", "lifecycle_state", default=value)
    if isinstance(value, Enum):
        value = value.value
    return str(value or "").strip().lower().replace("-", "_")


def _mission_id(mission: Any) -> str:
    value = _field(mission, "mission_id", "wp_id", "id")
    if value is None:
        raise RuntimeSupervisorError("mission has no durable identity")
    return str(value)


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "to_dict"):
        return _plain(value.to_dict())
    if hasattr(value, "as_dict"):
        return _plain(value.as_dict())
    return str(value)


def _identity(checkpoint: Any, evidence: Any) -> str:
    return json.dumps({"checkpoint": _plain(checkpoint), "evidence": _plain(evidence)}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class RuntimeSupervisor:
    def __init__(self, configs: Optional[Iterable[WorkerConfig]] = None, repository_mappings: Optional[Mapping[str, str]] = None, qa_endpoint: str = DEFAULT_QA_ENDPOINT, worker_module: str = "controller.generic_worker_host", max_restarts: int = 3, health_timeout: float = 1.0, process_factory: Optional[Callable[..., Any]] = None, health_checker: Optional[Callable[[WorkerConfig], Any]] = None, port_checker: Optional[Callable[[int], bool]] = None, mission_store: Optional[Any] = None, mission_store_path: Optional[str] = None, execute_timeout: float = 300.0, execute_requester: Optional[Callable[[WorkerConfig, Mapping[str, Any]], Any]] = None, clock: Optional[Callable[[], float]] = None, stall_threshold: float = 300.0, starvation_threshold: float = 0.0, max_recovery_attempts: int = 2, recovery_handler: Optional[Callable[[WorkerConfig, Any, int], Any]] = None, max_rework_attempts: int = 2) -> None:
        self._configs = self._validate_configs(tuple(configs or golden_team_configs()))
        self._config_by_id = {config.agent_id: config for config in self._configs}
        self.qa_endpoint = qa_endpoint.rstrip("/")
        self.worker_module = worker_module
        self.max_restarts = max(0, int(max_restarts))
        self.max_recovery_attempts = max(0, int(max_recovery_attempts))
        self.max_rework_attempts = max(0, int(max_rework_attempts))
        self.health_timeout = max(.01, float(health_timeout))
        self.execute_timeout = max(.01, float(execute_timeout))
        self.stall_threshold = max(0.0, float(stall_threshold))
        self.starvation_threshold = max(0.0, float(starvation_threshold))
        self._clock = clock or time.time
        self._process_factory = process_factory or subprocess.Popen
        self._health_checker = health_checker or self._http_health
        self._port_checker = port_checker or self._is_port_open
        self._execute_requester = execute_requester or self._http_execute
        self._recovery_handler = recovery_handler
        self._owned: Dict[str, Any] = {}
        self._restart_counts = {config.agent_id: 0 for config in self._configs}
        self._intentional_stops = set()
        self._last_errors: Dict[str, str] = {}
        self._work_states: Dict[str, Dict[str, Any]] = {}
        self._starvation_key: Optional[str] = None
        self._starvation_since: Optional[float] = None
        self._lock = threading.RLock()
        self._shutdown_requested = threading.Event()
        supplied = repository_mappings or {}
        unknown = set(supplied).difference(REPOSITORY_ENV_KEYS)
        if unknown:
            raise ValueError("unsupported repository mapping keys: %s" % sorted(unknown))
        self.repository_mappings = {key: str(supplied.get(key, os.environ.get(key))) for key in REPOSITORY_ENV_KEYS if supplied.get(key, os.environ.get(key))}
        if mission_store is not None and mission_store_path is not None:
            raise ValueError("supply mission_store or mission_store_path, not both")
        self.mission_store = mission_store if mission_store_path is None else MissionStore.load_or_create(mission_store_path)
        self._last_work_control = {"state": "IDLE", "reason": "durable mission store configured; no work-control cycle completed"} if self.mission_store is not None else {"state": "DISABLED", "reason": "no mission store configured"}

    @staticmethod
    def _validate_configs(configs: Sequence[WorkerConfig]) -> Tuple[WorkerConfig, ...]:
        if not configs:
            raise ValueError("at least one worker configuration is required")
        ids, ports = set(), set()
        for config in configs:
            if not config.agent_id or not config.name or config.agent_id in ids or config.port in ports or not 1 <= int(config.port) <= 65535:
                raise ValueError("invalid or duplicate worker configuration: %s" % config.agent_id)
            ids.add(config.agent_id)
            ports.add(config.port)
        return tuple(configs)

    @property
    def configs(self) -> Tuple[WorkerConfig, ...]:
        return self._configs

    @property
    def work_control_status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._last_work_control)

    def build_launch(self, worker: Any) -> Tuple[List[str], Dict[str, str]]:
        config = self._resolve(worker)
        command = list(config.command) if config.command else [sys.executable, "-m", self.worker_module]
        env = os.environ.copy()
        env.update(self.repository_mappings)
        env.update({"RVSC_AGENT_ID": config.agent_id, "RVSC_WORKER_AGENT_ID": config.agent_id, "RVSC_AGENT_NAME": config.name, "RVSC_AGENT_ROLE": config.role, "RVSC_WORKER_PORT": str(config.port), "RVSC_PORT": str(config.port)})
        if config.role == "engineering":
            env.update({key: self.qa_endpoint for key in QA_ROUTING_ENV_KEYS})
        else:
            for key in QA_ROUTING_ENV_KEYS:
                env.pop(key, None)
        return command, env

    def start(self, worker: Any) -> WorkerStatus:
        config = self._resolve(worker)
        with self._lock:
            process = self._owned.get(config.agent_id)
            if process is not None and process.poll() is None:
                return self._status_for(config)
            self._owned.pop(config.agent_id, None)
            health = self.check_health(config)
            occupied = bool(self._port_checker(config.port))
            if health.healthy:
                if health.agent_id != config.agent_id:
                    raise RuntimeConflictError("port %s reports identity %r; expected %s" % (config.port, health.agent_id, config.agent_id))
                self._restart_counts[config.agent_id] = 0
                self._last_errors.pop(config.agent_id, None)
                self._intentional_stops.discard(config.agent_id)
                return self._status_for(config, health)
            if occupied:
                raise RuntimeConflictError("port %s is occupied without a healthy matching identity" % config.port)
            self._restart_counts[config.agent_id] = 0
            self._intentional_stops.discard(config.agent_id)
            self._spawn(config)
            return self._status_for(config)

    def start_all(self) -> List[WorkerStatus]:
        started = []
        try:
            for config in self._configs:
                owned = config.agent_id in self._owned
                self.start(config)
                if not owned and config.agent_id in self._owned:
                    started.append(config.agent_id)
        except Exception:
            for worker_id in reversed(started):
                self.stop(worker_id)
            raise
        return self.status()

    def _spawn(self, config: WorkerConfig) -> Any:
        command, env = self.build_launch(config)
        process = self._process_factory(command, env=env)
        self._owned[config.agent_id] = process
        self._last_errors.pop(config.agent_id, None)
        return process

    def stop(self, worker: Any, timeout: float = 5.0) -> bool:
        config = self._resolve(worker)
        with self._lock:
            self._intentional_stops.add(config.agent_id)
            process = self._owned.get(config.agent_id)
            if process is None:
                return False
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=max(0.0, timeout))
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=max(0.0, timeout))
            finally:
                self._owned.pop(config.agent_id, None)
            return True

    def stop_all(self, timeout: float = 5.0) -> None:
        for config in reversed(self._configs):
            self.stop(config, timeout)

    def check_health(self, worker: Any) -> HealthResult:
        config = self._resolve(worker)
        try:
            return self._normalise_health(self._health_checker(config))
        except Exception as exc:
            return HealthResult(False, detail="health check failed: %s" % exc)

    @staticmethod
    def _normalise_health(value: Any) -> HealthResult:
        if isinstance(value, HealthResult):
            return value
        if isinstance(value, bool):
            return HealthResult(value)
        if not isinstance(value, Mapping):
            return HealthResult(False, detail="invalid health response")
        payload = dict(value)
        identities = []
        invalid_identity = False
        for key in ("agent_id", "identity", "id"):
            candidate = payload.get(key)
            if candidate:
                identities.append(str(candidate))
        for key in ("worker", "agent"):
            nested = payload.get(key)
            if key == "worker" and key in payload and isinstance(nested, str):
                if nested.strip():
                    identities.append(nested.strip())
                else:
                    invalid_identity = True
            elif key == "worker" and key in payload and not isinstance(nested, Mapping):
                invalid_identity = True
            if isinstance(nested, Mapping):
                for identity_key in ("agent_id", "identity", "id"):
                    candidate = nested.get(identity_key)
                    if candidate:
                        identities.append(str(candidate))
        if invalid_identity or len(set(identities)) > 1:
            identity, identity_detail = None, "invalid or conflicting health identity"
        else:
            identity, identity_detail = (identities[0] if identities else None), ""
        if "healthy" in payload:
            healthy = bool(payload["healthy"])
        elif "ok" in payload:
            healthy = bool(payload["ok"])
        elif "ready" in payload:
            healthy = bool(payload["ready"])
        elif "status" in payload:
            healthy = str(payload["status"]).lower() in {"ok", "healthy", "ready", "running"}
        else:
            healthy = True
        return HealthResult(healthy, identity, str(payload.get("detail") or payload.get("message") or identity_detail), payload)

    def _http_health(self, config: WorkerConfig) -> HealthResult:
        request = urllib.request.Request("http://127.0.0.1:%s/health" % config.port, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.health_timeout) as response:
                status = getattr(response, "status", response.getcode())
                if not 200 <= status < 300:
                    return HealthResult(False, detail="HTTP %s" % status)
                return self._normalise_health(json.loads(response.read().decode("utf-8")))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return HealthResult(False, detail="unavailable: %s" % exc)

    @staticmethod
    def _is_port_open(port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(.2)
            return sock.connect_ex(("127.0.0.1", int(port))) == 0
        finally:
            sock.close()

    def poll_once(self) -> List[WorkerStatus]:
        with self._lock:
            for config in self._configs:
                process = self._owned.get(config.agent_id)
                if process is None or process.poll() is None:
                    continue
                self._owned.pop(config.agent_id, None)
                if config.agent_id in self._intentional_stops:
                    continue
                count = self._restart_counts[config.agent_id]
                if count >= self.max_restarts:
                    self._last_errors[config.agent_id] = "restart limit reached"
                    continue
                self._restart_counts[config.agent_id] = count + 1
                health = self.check_health(config)
                if health.healthy:
                    if health.agent_id != config.agent_id:
                        self._last_errors[config.agent_id] = "identity conflict after exit"
                    continue
                if self._port_checker(config.port):
                    self._last_errors[config.agent_id] = "port conflict after exit"
                    continue
                try:
                    self._spawn(config)
                except Exception as exc:
                    self._last_errors[config.agent_id] = "restart failed: %s" % exc
        if self.mission_store is not None:
            self.work_control_once()
        return self.status()

    def _store_missions(self) -> List[Any]:
        for name in ("list_missions", "missions", "all", "list"):
            member = getattr(self.mission_store, name, None)
            if member is None:
                continue
            value = member() if callable(member) else member
            if isinstance(value, Mapping):
                value = value.get("missions", list(value.values()))
            return list(value or [])
        raise RuntimeSupervisorError("MissionStore does not expose durable missions")

    def _authorized_projects(self, config: WorkerConfig, health: HealthResult) -> Tuple[str, ...]:
        projects = {str(value).lower() for value in config.authorized_projects}
        advertised = health.payload.get("authorized_projects") or health.payload.get("projects") or ()
        if isinstance(advertised, str):
            advertised = [advertised]
        projects.update(str(value).lower() for value in advertised)
        projects.update(key[5:-5].lower() for key in self.repository_mappings)
        return tuple(sorted(projects))

    def _make_worker_state(self, config: WorkerConfig, health: HealthResult, available: bool, mission_id: Optional[str]) -> Any:
        projects = self._authorized_projects(config, health)
        values = {"worker_id": config.agent_id, "agent_id": config.agent_id, "identity": config.agent_id, "authorized_projects": projects, "projects": projects, "available": available, "healthy": True, "active_mission_id": mission_id, "current_mission_id": mission_id}
        try:
            signature = inspect.signature(WorkerState)
            return WorkerState(**{name: values[name] for name in signature.parameters if name in values})
        except (TypeError, ValueError):
            return SimpleNamespace(**values)

    @staticmethod
    def _dependency_ready(mission: Any, accepted: set) -> bool:
        dependencies = _field(mission, "dependencies", "depends_on", default=()) or ()
        if isinstance(dependencies, str):
            dependencies = [dependencies]
        return all(str(value) in accepted for value in dependencies)

    def _transition(self, mission: Any, state: str, worker_id: Optional[str] = None, evidence: Optional[Mapping[str, Any]] = None) -> Any:
        method = getattr(self.mission_store, "transition", None)
        if not callable(method):
            for name in ("transition_mission", "set_status", "update_status"):
                method = getattr(self.mission_store, name, None)
                if callable(method):
                    break
        if not callable(method):
            raise RuntimeSupervisorError("MissionStore does not expose lifecycle transitions")
        values = {"mission_id": _mission_id(mission), "wp_id": _mission_id(mission), "id": _mission_id(mission), "new_state": state, "state": state, "status": state, "worker_id": worker_id, "agent_id": worker_id, "evidence": dict(evidence or {}), "reason": str((evidence or {}).get("reason", "")) or None, "timestamp": float(self._clock())}
        signature = inspect.signature(method)
        args, kwargs = [], {}
        for parameter in signature.parameters.values():
            if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                continue
            if parameter.name in values:
                if parameter.kind == parameter.POSITIONAL_ONLY:
                    args.append(values[parameter.name])
                else:
                    kwargs[parameter.name] = values[parameter.name]
            elif parameter.default is parameter.empty:
                raise RuntimeSupervisorError("unsupported transition signature")
        return method(*args, **kwargs)

    def _record_progress(self, mission: Any, timestamp: float, checkpoint: Any, evidence: Any) -> Any:
        method = getattr(self.mission_store, "record_progress", None)
        return method(_mission_id(mission), timestamp=timestamp, checkpoint=checkpoint, evidence=evidence) if callable(method) else mission

    def _monitor_active(self, mission: Any, health: HealthResult, now: float) -> Dict[str, Any]:
        worker_id = str(_field(mission, "assigned_worker", "assigned_worker_id", "worker_id", "agent_id", "assignee", default=""))
        metadata = _field(mission, "metadata", default={}) or {}
        last = _field(mission, "material_progress_at", default=metadata.get("material_progress_at"))
        checkpoint = _field(mission, "material_checkpoint", default=metadata.get("material_checkpoint"))
        evidence = _field(mission, "material_evidence", default=metadata.get("material_evidence"))
        identity = _field(mission, "material_identity", default=metadata.get("material_identity"))
        payload = health.payload if health and health.healthy and health.agent_id == worker_id else {}
        reported_mission = payload.get("mission_id") or payload.get("wp_id")
        candidate_checkpoint = payload.get("checkpoint") or payload.get("material_checkpoint")
        candidate_evidence = payload.get("evidence") or payload.get("material_evidence")
        if (candidate_checkpoint is not None or candidate_evidence is not None) and (reported_mission is None or str(reported_mission) == _mission_id(mission)):
            candidate_identity = _identity(candidate_checkpoint, candidate_evidence)
            if candidate_identity != identity:
                self._record_progress(mission, now, candidate_checkpoint, candidate_evidence)
                last, checkpoint, evidence = now, candidate_checkpoint, candidate_evidence
        if last is None:
            checkpoint = checkpoint or "lifecycle:%s" % _state_text(mission)
            evidence = evidence if evidence is not None else {"state": _state_text(mission)}
            self._record_progress(mission, now, checkpoint, evidence)
            last = now
        elapsed = max(0.0, now - float(last))
        state = "PROGRESSING" if elapsed == 0 else "WORKING"
        reason = "material evidence advanced" if elapsed == 0 else "active mission awaiting material advancement"
        attempts = int(_field(mission, "recovery_attempts", default=metadata.get("recovery_attempts", 0)) or 0)
        outcome = _field(mission, "recovery_outcome", default=metadata.get("recovery_outcome"))
        recovery_state = "NONE"
        if elapsed > self.stall_threshold:
            state, reason = "STALLED", "no material advancement for %.3f seconds" % elapsed
            if attempts >= self.max_recovery_attempts:
                recovery_state = "EXHAUSTED"
            elif self._recovery_handler is None:
                recovery_state = "PENDING"
            else:
                attempts += 1
                try:
                    outcome = str(self._recovery_handler(self._config_by_id[worker_id], mission, attempts))
                    recovery_state = "ATTEMPTED"
                except Exception as exc:
                    outcome = "%s: %s" % (type(exc).__name__, exc)
                    recovery_state = "FAILED"
                recorder = getattr(self.mission_store, "record_recovery", None)
                if callable(recorder):
                    recorder(_mission_id(mission), attempts, recovery_state, outcome)
        result = {"state": state, "mission_id": _mission_id(mission), "detail": reason, "reason": reason, "last_material_progress": float(last), "no_progress_duration": elapsed, "material_checkpoint": checkpoint, "material_evidence": _plain(evidence), "recovery_state": recovery_state, "recovery_attempts": attempts, "recovery_outcome": outcome}
        self._work_states[worker_id] = result
        return result

    @staticmethod
    def _decision_parts(decision: Any, missions: Sequence[Any], workers: Sequence[Any]) -> Tuple[Any, Any, str]:
        if decision is None:
            return None, None, "deterministic dispatch found no eligible pairing"
        reason = str(_field(decision, "reason", "detail", default=""))
        if isinstance(decision, (tuple, list)) and len(decision) >= 2:
            return decision[0], decision[1], reason
        mission_id, worker_id = _field(decision, "mission_id", "wp_id"), _field(decision, "worker_id", "agent_id")
        mission = _field(decision, "mission", "selected_mission") or next((item for item in missions if mission_id is not None and _mission_id(item) == str(mission_id)), None)
        worker = _field(decision, "worker", "selected_worker") or next((item for item in workers if worker_id is not None and str(_field(item, "worker_id", "agent_id")) == str(worker_id)), None)
        return mission, worker, reason

    def _http_execute(self, config: WorkerConfig, mission: Mapping[str, Any]) -> Any:
        request = urllib.request.Request("http://127.0.0.1:%s/execute" % config.port, data=json.dumps(dict(mission)).encode(), headers={"Accept": "application/json", "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=self.execute_timeout) as response:
            status = getattr(response, "status", response.getcode())
            body = response.read().decode()
            if not 200 <= status < 300:
                raise RuntimeSupervisorError("worker execute returned HTTP %s" % status)
            return json.loads(body) if body else {}

    @staticmethod
    def _result_tokens(value: Any) -> set:
        if isinstance(value, Mapping):
            return set().union(*(RuntimeSupervisor._result_tokens(item) for item in value.values())) if value else set()
        if isinstance(value, (list, tuple, set)):
            return set().union(*(RuntimeSupervisor._result_tokens(item) for item in value)) if value else set()
        if isinstance(value, Enum):
            return {str(value.value).upper()}
        return {value.upper()} if isinstance(value, str) else set()

    @staticmethod
    def _qa_identity(value: Any) -> Optional[str]:
        if isinstance(value, Mapping):
            for key in ("qa_agent_id", "qa_worker_id", "reviewer_id"):
                if value.get(key):
                    return str(value[key])
            for nested in value.values():
                found = RuntimeSupervisor._qa_identity(nested)
                if found:
                    return found
        elif isinstance(value, (list, tuple)):
            for nested in value:
                found = RuntimeSupervisor._qa_identity(nested)
                if found:
                    return found
        return None

    def _is_quinn(self, qa_id: Optional[str], implementer: str) -> bool:
        if not qa_id or qa_id == implementer:
            return False
        config = self._config_by_id.get(qa_id)
        return qa_id == "QA-001" or bool(config and config.role == "qa" and config.name.lower() == "quinn")

    def ingest_mission(self, contract: Mapping[str, Any]) -> Dict[str, Any]:
        if self.mission_store is None or not isinstance(self.mission_store, MissionStore):
            raise RuntimeSupervisorError("durable MissionStore is required for mission ingestion")
        return self.mission_store.add_contract(contract, supported_projects=SUPPORTED_PROJECTS).to_dict()

    def queue_status(self) -> Dict[str, Any]:
        if self.mission_store is None:
            return {"state": "DISABLED", "missions": [], "next_eligible_work": None}
        missions = self._store_missions()
        records, eligible = [], []
        for mission in missions:
            mission_id = _mission_id(mission)
            state = _state_text(mission)
            ready, dependency_state = self.mission_store.readiness(mission_id) if isinstance(self.mission_store, MissionStore) else (state in _QUEUED_STATES, None)
            if ready:
                eligible.append(mission)
            metadata = _field(mission, "metadata", default={}) or {}
            records.append({"mission_id": mission_id, "project": _field(mission, "project_id", "project"), "lifecycle": state, "dependencies": list(_field(mission, "dependencies", default=()) or ()), "dependency_state": "ready" if ready else dependency_state, "assigned_worker": _field(mission, "assigned_worker", "worker_id"), "material_progress_at": _field(mission, "material_progress_at"), "material_checkpoint": _field(mission, "material_checkpoint"), "material_evidence": _plain(_field(mission, "material_evidence")), "qa_worker": _field(mission, "qa_worker"), "qa_evidence": _plain(_field(mission, "qa_evidence")), "rework_attempts": int(_field(mission, "rework_attempts", default=0) or 0), "corrective_mission_id": metadata.get("corrective_mission_id"), "blocker": _field(mission, "block_reason", "rejection_reason"), "recovery_state": _field(mission, "recovery_state", default="NONE"), "recovery_attempts": int(_field(mission, "recovery_attempts", default=0) or 0), "recovery_outcome": _field(mission, "recovery_outcome")})
        eligible.sort(key=lambda item: (int(_field(item, "priority", default=999)), int(_field(item, "sequence", default=-1)), _mission_id(item)))
        return {"state": "IDLE" if not eligible else "READY", "missions": records, "next_eligible_work": _mission_id(eligible[0]) if eligible else None, "work_control": self.work_control_status}

    def work_control_once(self) -> Dict[str, Any]:
        with self._lock:
            if self.mission_store is None:
                return dict(self._last_work_control)
            now = float(self._clock())
            missions = self._store_missions()
            accepted = {_mission_id(mission) for mission in missions if _state_text(mission) in _ACCEPTED_STATES}
            active_by_worker = {}
            for mission in missions:
                if _state_text(mission) in _ACTIVE_STATES:
                    worker_id = _field(mission, "assigned_worker", "assigned_worker_id", "worker_id", "agent_id", "assignee")
                    if worker_id:
                        active_by_worker[str(worker_id)] = mission
            workers, health_by_id = [], {}
            for config in self._configs:
                health = self.check_health(config)
                health_by_id[config.agent_id] = health
                verified = health.healthy and health.agent_id == config.agent_id
                available = bool(verified and config.role == "engineering" and config.agent_id not in active_by_worker)
                if verified:
                    workers.append(self._make_worker_state(config, health, available, _mission_id(active_by_worker[config.agent_id]) if config.agent_id in active_by_worker else None))
                if config.agent_id in active_by_worker:
                    self._monitor_active(active_by_worker[config.agent_id], health, now)
                elif available:
                    self._work_states[config.agent_id] = {"state": "AVAILABLE", "mission_id": None, "detail": "verified healthy and available", "reason": "no assignment"}
                else:
                    self._work_states[config.agent_id] = {"state": "IDLE" if verified else "BLOCKED", "mission_id": None, "detail": "not an available engineering worker" if verified else "worker health not verified", "reason": "role or health prevents assignment"}
            source = self.mission_store if isinstance(self.mission_store, MissionStore) else missions
            decision = select_dispatch(source, workers)
            mission, worker, reason = self._decision_parts(decision, missions, workers)
            queued = [item for item in missions if _state_text(item) in _QUEUED_STATES and self._dependency_ready(item, accepted)]
            next_work = _mission_id(queued[0]) if queued else None
            if mission is None or worker is None:
                if queued:
                    key = "|".join(sorted(_mission_id(item) for item in queued))
                    if key != self._starvation_key:
                        self._starvation_key, self._starvation_since = key, now
                    elapsed = max(0.0, now - float(self._starvation_since if self._starvation_since is not None else now))
                    state = "STARVED" if elapsed >= self.starvation_threshold else "AVAILABLE"
                    why = reason or "eligible work has no assignment"
                else:
                    self._starvation_key = self._starvation_since = None
                    elapsed, state, why = 0.0, "IDLE", reason or "no eligible work"
                self._last_work_control = {"state": state, "reason": why, "queued_ready": [_mission_id(item) for item in queued], "no_progress_duration": elapsed, "next_eligible_work": next_work}
                return dict(self._last_work_control)
            self._starvation_key = self._starvation_since = None
            worker_id = str(_field(worker, "worker_id", "agent_id", "identity"))
            config = self._config_by_id.get(worker_id)
            health = health_by_id.get(worker_id)
            project = str(_field(mission, "project", "project_id", default="")).lower()
            mission_id = _mission_id(mission)
            if not config or config.role != "engineering" or not health or not health.healthy or health.agent_id != worker_id or project not in self._authorized_projects(config, health):
                self._last_work_control = {"state": "STARVED", "mission_id": mission_id, "worker_id": worker_id, "reason": "selected worker failed health, role, availability, or project authorization validation", "next_eligible_work": mission_id}
                return dict(self._last_work_control)
            try:
                if not isinstance(self.mission_store, MissionStore):
                    raise RuntimeSupervisorError("validated durable MissionStore is required for engineering dispatch")
                dispatch_contract = self.mission_store.dispatch_contract(mission_id, worker_id, supported_projects=SUPPORTED_PROJECTS)
            except Exception as exc:
                evidence = {"event": "contract_validation_failure", "worker_id": worker_id, "reason": "%s: %s" % (type(exc).__name__, exc), "retryable": False}
                try:
                    self._transition(mission, "blocked", worker_id, evidence)
                except Exception as persist:
                    evidence["persistence_error"] = "%s: %s" % (type(persist).__name__, persist)
                self._work_states[worker_id] = {"state": "BLOCKED", "mission_id": mission_id, "detail": evidence["reason"], "reason": evidence["reason"]}
                self._last_work_control = {"state": "BLOCKED", "mission_id": mission_id, "worker_id": worker_id, "reason": evidence["reason"], "evidence": evidence, "next_eligible_work": None}
                return dict(self._last_work_control)
            current = self._transition(mission, "assigned", worker_id, {"event": "dispatch_reserved", "worker_id": worker_id}) or mission
            current = self._transition(current, "running", worker_id, {"event": "dispatch_started", "worker_id": worker_id}) or current
            self._work_states[worker_id] = {"state": "WORKING", "mission_id": mission_id, "detail": "dispatch in progress", "last_material_progress": now, "no_progress_duration": 0.0, "material_checkpoint": "lifecycle:running", "material_evidence": {"event": "dispatch_started"}}
            self._last_work_control = {"state": "WORKING", "mission_id": mission_id, "worker_id": worker_id, "reason": "durably reserved and dispatched", "last_material_progress": now}
        try:
            result = self._execute_requester(config, dispatch_contract)
            tokens = self._result_tokens(result)
            qa_id = self._qa_identity(result)
            evidence = {"event": "worker_result", "worker_id": worker_id, "qa_worker_id": qa_id, "result": _plain(result)}
            if ("QA_ACCEPTED" in tokens or "QA_REJECTED" in tokens or "REJECTED" in tokens) and not self._is_quinn(qa_id, worker_id):
                evidence["reason"] = "QA outcome was not independently attributed to Quinn"
                self._transition(current, "blocked", worker_id, evidence)
                final, detail = "BLOCKED", evidence["reason"]
            elif "QA_ACCEPTED" in tokens:
                current = self._transition(current, "completed", worker_id, evidence) or current
                outcome = self.mission_store.process_qa_outcome(mission_id, "QA_ACCEPTED", qa_worker=qa_id or "", evidence=evidence, timestamp=float(self._clock()), max_rework_attempts=self.max_rework_attempts)
                final, detail = "ACCEPTED", "engineering result independently QA accepted"
                evidence["qa_control_outcome"] = outcome.outcome
            elif "QA_REJECTED" in tokens or "REJECTED" in tokens:
                current = self._transition(current, "completed", worker_id, evidence) or current
                outcome = self.mission_store.process_qa_outcome(mission_id, "QA_REJECTED", qa_worker=qa_id or "", evidence=evidence, timestamp=float(self._clock()), max_rework_attempts=self.max_rework_attempts)
                evidence.update({"qa_control_outcome": outcome.outcome, "corrective_mission_id": outcome.corrective_mission_id, "rework_attempts": outcome.rework_attempts})
                if outcome.outcome == "REWORK_EXHAUSTED":
                    final, detail = "EXCEPTION", "QA rework threshold exhausted"
                else:
                    final, detail = "REWORK_QUEUED", "QA rejected; corrective engineering work queued"
            else:
                evidence["reason"] = "worker result did not contain independently attributed QA outcome"
                self._transition(current, "blocked", worker_id, evidence)
                final, detail = "BLOCKED", evidence["reason"]
        except Exception as exc:
            evidence = {"event": "dispatch_failure", "worker_id": worker_id, "reason": "%s: %s" % (type(exc).__name__, exc), "retryable": True}
            try:
                self._transition(current, "blocked", worker_id, evidence)
            except Exception as persist:
                evidence["persistence_error"] = "%s: %s" % (type(persist).__name__, persist)
            final, detail = "BLOCKED", evidence["reason"]
        with self._lock:
            timestamp = float(self._clock())
            self._work_states[worker_id] = {"state": final, "mission_id": mission_id, "detail": detail, "reason": detail, "last_material_progress": timestamp, "no_progress_duration": 0.0, "material_checkpoint": "lifecycle:%s" % final.lower(), "material_evidence": evidence}
            self._last_work_control = {"state": final, "mission_id": mission_id, "worker_id": worker_id, "reason": detail, "last_material_progress": timestamp, "evidence": evidence, "next_eligible_work": evidence.get("corrective_mission_id")}
            return dict(self._last_work_control)

    dispatch_once = work_control_once

    def status(self) -> List[WorkerStatus]:
        with self._lock:
            return [self._status_for(config) for config in self._configs]

    def status_dicts(self) -> List[Dict[str, Any]]:
        return [status.as_dict() for status in self.status()]

    def _status_for(self, config: WorkerConfig, known_health: Optional[HealthResult] = None) -> WorkerStatus:
        process = self._owned.get(config.agent_id)
        owned = process is not None and process.poll() is None
        health = known_health or self.check_health(config)
        ready = bool(health.healthy and health.agent_id == config.agent_id)
        payload = health.as_dict()
        if config.agent_id in self._last_errors:
            payload["supervisor_error"] = self._last_errors[config.agent_id]
        work = self._work_states.get(config.agent_id, {})
        return WorkerStatus(config.agent_id, config.name, config.port, bool(owned or ready or self._port_checker(config.port)), ready, payload, owned, self._restart_counts[config.agent_id], str(work.get("state", "AVAILABLE" if ready else "IDLE")), work.get("mission_id"), str(work.get("detail", "")), work.get("last_material_progress"), work.get("no_progress_duration"), work.get("material_checkpoint"), work.get("material_evidence"), str(work.get("reason", "")), str(work.get("recovery_state", "NONE")), int(work.get("recovery_attempts", 0)), work.get("recovery_outcome"), self._last_work_control.get("next_eligible_work"))

    def _resolve(self, worker: Any) -> WorkerConfig:
        if isinstance(worker, WorkerConfig):
            if self._config_by_id.get(worker.agent_id) != worker:
                raise KeyError("worker is not managed by this supervisor")
            return worker
        if str(worker) not in self._config_by_id:
            raise KeyError("unknown worker identity: %s" % worker)
        return self._config_by_id[str(worker)]

    def request_shutdown(self) -> None:
        self._shutdown_requested.set()

    def run(self, poll_interval: float = 1.0) -> None:
        self.start_all()
        if self.mission_store is not None:
            self.work_control_once()
        try:
            while not self._shutdown_requested.wait(max(.05, poll_interval)):
                self.poll_once()
        finally:
            self.stop_all()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RVSC Golden Team runtime supervisor")
    parser.add_argument("action", nargs="?", choices=("run", "status", "add"), default="run")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--max-restarts", type=int, default=3)
    parser.add_argument("--qa-endpoint", default=DEFAULT_QA_ENDPOINT)
    parser.add_argument("--worker-module", default="controller.generic_worker_host")
    parser.add_argument("--mission-store")
    parser.add_argument("--mission-file")
    parser.add_argument("--stall-threshold", type=float, default=300.0)
    parser.add_argument("--starvation-threshold", type=float, default=0.0)
    parser.add_argument("--max-recovery-attempts", type=int, default=2)
    parser.add_argument("--max-rework-attempts", type=int, default=2)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    store_path = Path(args.mission_store).expanduser() if args.mission_store else production_mission_store_path()
    try:
        supervisor = RuntimeSupervisor(qa_endpoint=args.qa_endpoint, worker_module=args.worker_module, max_restarts=args.max_restarts, mission_store_path=str(store_path), stall_threshold=args.stall_threshold, starvation_threshold=args.starvation_threshold, max_recovery_attempts=args.max_recovery_attempts, max_rework_attempts=args.max_rework_attempts)
        if args.action == "add":
            if not args.mission_file:
                raise RuntimeSupervisorError("add requires --mission-file")
            with Path(args.mission_file).expanduser().open("r", encoding="utf-8") as handle:
                contract = json.load(handle)
            if not isinstance(contract, Mapping):
                raise RuntimeSupervisorError("mission file must contain a JSON object")
            mission = supervisor.ingest_mission(contract)
            print(json.dumps({"added": mission, "queue": supervisor.queue_status(), "mission_store": str(store_path)}, indent=2, sort_keys=True))
            return 0
        if args.action == "status":
            print(json.dumps({"workers": supervisor.status_dicts(), "work_control": supervisor.work_control_status, "queue": supervisor.queue_status(), "mission_store": str(store_path)}, indent=2, sort_keys=True))
            return 0
    except (OSError, ValueError, json.JSONDecodeError, OrchestrationError, RuntimeSupervisorError) as exc:
        print("runtime supervisor input error: %s" % exc, file=sys.stderr)
        return 2

    def stop_handler(_signum: int, _frame: Any) -> None:
        supervisor.request_shutdown()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    try:
        supervisor.run(args.poll_interval)
    except RuntimeConflictError as exc:
        print("runtime supervisor conflict: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
