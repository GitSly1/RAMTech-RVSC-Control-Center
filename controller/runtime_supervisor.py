"""Controlled runtime supervision, durable dispatch, and productivity monitoring."""
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
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from controller.orchestrator import MissionStore, WorkerState, select_dispatch

REPOSITORY_ENV_KEYS = ("RVSC_RVSC_REPO", "RVSC_SEMANTIQ_REPO", "RVSC_MOXIE_REPO")
QA_ROUTING_ENV_KEYS = ("RVSC_QA_ENDPOINT", "RVSC_QA_URL", "RVSC_QA_WORKER_ENDPOINT", "RVSC_QA_WORKER_URL")
DEFAULT_QA_ENDPOINT = "http://127.0.0.1:8771/execute"
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
    return (
        WorkerConfig("OPS-001", "Noah", 8770, "engineering"),
        WorkerConfig("DEV-001", "Daniel", 8765, "engineering"),
        WorkerConfig("QA-001", "Quinn", 8771, "qa"),
    )


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
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
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
    def __init__(
        self,
        configs: Optional[Iterable[WorkerConfig]] = None,
        repository_mappings: Optional[Mapping[str, str]] = None,
        qa_endpoint: str = DEFAULT_QA_ENDPOINT,
        worker_module: str = "controller.generic_worker_host",
        max_restarts: int = 3,
        health_timeout: float = 1.0,
        process_factory: Optional[Callable[..., Any]] = None,
        health_checker: Optional[Callable[[WorkerConfig], Any]] = None,
        port_checker: Optional[Callable[[int], bool]] = None,
        mission_store: Optional[Any] = None,
        mission_store_path: Optional[str] = None,
        execute_timeout: float = 300.0,
        execute_requester: Optional[Callable[[WorkerConfig, Mapping[str, Any]], Any]] = None,
        clock: Optional[Callable[[], float]] = None,
        stall_threshold: float = 300.0,
        starvation_threshold: float = 0.0,
        max_recovery_attempts: int = 2,
        recovery_handler: Optional[Callable[[WorkerConfig, Any, int], Any]] = None,
    ) -> None:
        self._configs = self._validate_configs(tuple(configs or golden_team_configs()))
        self._config_by_id = {c.agent_id: c for c in self._configs}
        self.qa_endpoint = qa_endpoint.rstrip("/")
        self.worker_module = worker_module
        self.max_restarts = max(0, int(max_restarts))
        self.max_recovery_attempts = max(0, int(max_recovery_attempts))
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
        self._restart_counts = {c.agent_id: 0 for c in self._configs}
        self._intentional_stops = set()
        self._last_errors: Dict[str, str] = {}
        self._work_states: Dict[str, Dict[str, Any]] = {}
        self._last_work_control = {"state": "DISABLED", "reason": "no mission store configured"}
        self._starvation_key: Optional[str] = None
        self._starvation_since: Optional[float] = None
        self._lock = threading.RLock()
        self._shutdown_requested = threading.Event()
        supplied = repository_mappings or {}
        unknown = set(supplied).difference(REPOSITORY_ENV_KEYS)
        if unknown:
            raise ValueError("unsupported repository mapping keys: %s" % sorted(unknown))
        self.repository_mappings = {}
        for key in REPOSITORY_ENV_KEYS:
            value = supplied.get(key, os.environ.get(key))
            if value:
                self.repository_mappings[key] = str(value)
        if mission_store is not None and mission_store_path is not None:
            raise ValueError("supply mission_store or mission_store_path, not both")
        self.mission_store = mission_store if mission_store_path is None else MissionStore.load_or_create(mission_store_path)

    @staticmethod
    def _validate_configs(configs: Sequence[WorkerConfig]) -> Tuple[WorkerConfig, ...]:
        if not configs:
            raise ValueError("at least one worker configuration is required")
        ids, ports = set(), set()
        for c in configs:
            if not c.agent_id or not c.name or c.agent_id in ids or c.port in ports or not 1 <= int(c.port) <= 65535:
                raise ValueError("invalid or duplicate worker configuration: %s" % c.agent_id)
            ids.add(c.agent_id); ports.add(c.port)
        return tuple(configs)

    @property
    def configs(self) -> Tuple[WorkerConfig, ...]:
        return self._configs

    @property
    def work_control_status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._last_work_control)

    def build_launch(self, worker: Any) -> Tuple[List[str], Dict[str, str]]:
        c = self._resolve(worker)
        command = list(c.command) if c.command else [sys.executable, "-m", self.worker_module]
        env = os.environ.copy(); env.update(self.repository_mappings)
        env.update({"RVSC_AGENT_ID": c.agent_id, "RVSC_WORKER_AGENT_ID": c.agent_id, "RVSC_AGENT_NAME": c.name, "RVSC_AGENT_ROLE": c.role, "RVSC_WORKER_PORT": str(c.port), "RVSC_PORT": str(c.port)})
        if c.role == "engineering":
            env.update({key: self.qa_endpoint for key in QA_ROUTING_ENV_KEYS})
        else:
            for key in QA_ROUTING_ENV_KEYS:
                env.pop(key, None)
        return command, env

    def start(self, worker: Any) -> WorkerStatus:
        c = self._resolve(worker)
        with self._lock:
            process = self._owned.get(c.agent_id)
            if process is not None and process.poll() is None:
                return self._status_for(c)
            self._owned.pop(c.agent_id, None)
            health = self.check_health(c)
            occupied = bool(self._port_checker(c.port))
            if health.healthy:
                if health.agent_id != c.agent_id:
                    raise RuntimeConflictError("port %s reports identity %r; expected %s" % (c.port, health.agent_id, c.agent_id))
                self._restart_counts[c.agent_id] = 0
                self._last_errors.pop(c.agent_id, None)
                self._intentional_stops.discard(c.agent_id)
                return self._status_for(c, health)
            if occupied:
                raise RuntimeConflictError("port %s is occupied without a healthy matching identity" % c.port)
            self._restart_counts[c.agent_id] = 0
            self._intentional_stops.discard(c.agent_id)
            self._spawn(c)
            return self._status_for(c)

    def start_all(self) -> List[WorkerStatus]:
        started = []
        try:
            for c in self._configs:
                owned = c.agent_id in self._owned
                self.start(c)
                if not owned and c.agent_id in self._owned:
                    started.append(c.agent_id)
        except Exception:
            for worker_id in reversed(started):
                self.stop(worker_id)
            raise
        return self.status()

    def _spawn(self, c: WorkerConfig) -> Any:
        command, env = self.build_launch(c)
        process = self._process_factory(command, env=env)
        self._owned[c.agent_id] = process
        self._last_errors.pop(c.agent_id, None)
        return process

    def stop(self, worker: Any, timeout: float = 5.0) -> bool:
        c = self._resolve(worker)
        with self._lock:
            self._intentional_stops.add(c.agent_id)
            process = self._owned.get(c.agent_id)
            if process is None:
                return False
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=max(0.0, timeout))
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=max(0.0, timeout))
            finally:
                self._owned.pop(c.agent_id, None)
            return True

    def stop_all(self, timeout: float = 5.0) -> None:
        for c in reversed(self._configs):
            self.stop(c, timeout)

    def check_health(self, worker: Any) -> HealthResult:
        c = self._resolve(worker)
        try:
            return self._normalise_health(self._health_checker(c))
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
        identity = payload.get("agent_id") or payload.get("identity") or payload.get("id")
        for key in ("worker", "agent"):
            nested = payload.get(key)
            if identity is None and isinstance(nested, Mapping):
                identity = nested.get("agent_id") or nested.get("identity") or nested.get("id")
        if "healthy" in payload: healthy = bool(payload["healthy"])
        elif "ok" in payload: healthy = bool(payload["ok"])
        elif "ready" in payload: healthy = bool(payload["ready"])
        elif "status" in payload: healthy = str(payload["status"]).lower() in {"ok", "healthy", "ready", "running"}
        else: healthy = True
        return HealthResult(healthy, str(identity) if identity is not None else None, str(payload.get("detail") or payload.get("message") or ""), payload)

    def _http_health(self, c: WorkerConfig) -> HealthResult:
        request = urllib.request.Request("http://127.0.0.1:%s/health" % c.port, headers={"Accept": "application/json"})
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
            for c in self._configs:
                process = self._owned.get(c.agent_id)
                if process is None or process.poll() is None:
                    continue
                self._owned.pop(c.agent_id, None)
                if c.agent_id in self._intentional_stops:
                    continue
                count = self._restart_counts[c.agent_id]
                if count >= self.max_restarts:
                    self._last_errors[c.agent_id] = "restart limit reached"; continue
                self._restart_counts[c.agent_id] = count + 1
                health = self.check_health(c)
                if health.healthy:
                    if health.agent_id != c.agent_id:
                        self._last_errors[c.agent_id] = "identity conflict after exit"
                    continue
                if self._port_checker(c.port):
                    self._last_errors[c.agent_id] = "port conflict after exit"; continue
                try:
                    self._spawn(c)
                except Exception as exc:
                    self._last_errors[c.agent_id] = "restart failed: %s" % exc
        if self.mission_store is not None:
            self.work_control_once()
        return self.status()

    def _store_missions(self) -> List[Any]:
        for name in ("list_missions", "missions", "all", "list"):
            member = getattr(self.mission_store, name, None)
            if member is None: continue
            value = member() if callable(member) else member
            if isinstance(value, Mapping): value = value.get("missions", list(value.values()))
            return list(value or [])
        raise RuntimeSupervisorError("MissionStore does not expose durable missions")

    def _authorized_projects(self, c: WorkerConfig, health: HealthResult) -> Tuple[str, ...]:
        projects = {str(v).lower() for v in c.authorized_projects}
        advertised = health.payload.get("authorized_projects") or health.payload.get("projects") or ()
        if isinstance(advertised, str): advertised = [advertised]
        projects.update(str(v).lower() for v in advertised)
        projects.update(key[5:-5].lower() for key in self.repository_mappings)
        return tuple(sorted(projects))

    def _make_worker_state(self, c: WorkerConfig, health: HealthResult, available: bool, mission_id: Optional[str]) -> Any:
        values = {"worker_id": c.agent_id, "agent_id": c.agent_id, "identity": c.agent_id, "authorized_projects": self._authorized_projects(c, health), "projects": self._authorized_projects(c, health), "available": available, "healthy": True, "active_mission_id": mission_id, "current_mission_id": mission_id}
        try:
            signature = inspect.signature(WorkerState)
            return WorkerState(**{name: values[name] for name in signature.parameters if name in values})
        except (TypeError, ValueError):
            return SimpleNamespace(**values)

    @staticmethod
    def _dependency_ready(mission: Any, accepted: set) -> bool:
        dependencies = _field(mission, "dependencies", "depends_on", default=()) or ()
        if isinstance(dependencies, str): dependencies = [dependencies]
        return all(str(v) in accepted for v in dependencies)

    def _transition(self, mission: Any, state: str, worker_id: Optional[str] = None, evidence: Optional[Mapping[str, Any]] = None) -> Any:
        method = getattr(self.mission_store, "transition", None)
        if not callable(method):
            for name in ("transition_mission", "set_status", "update_status"):
                method = getattr(self.mission_store, name, None)
                if callable(method): break
        if not callable(method): raise RuntimeSupervisorError("MissionStore does not expose lifecycle transitions")
        values = {"mission_id": _mission_id(mission), "wp_id": _mission_id(mission), "id": _mission_id(mission), "new_state": state, "state": state, "status": state, "worker_id": worker_id, "agent_id": worker_id, "evidence": dict(evidence or {}), "reason": str((evidence or {}).get("reason", "")) or None, "timestamp": float(self._clock())}
        signature = inspect.signature(method); args, kwargs = [], {}
        for p in signature.parameters.values():
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD): continue
            if p.name in values:
                if p.kind == p.POSITIONAL_ONLY: args.append(values[p.name])
                else: kwargs[p.name] = values[p.name]
            elif p.default is p.empty: raise RuntimeSupervisorError("unsupported transition signature")
        return method(*args, **kwargs)

    def _record_progress(self, mission: Any, timestamp: float, checkpoint: Any, evidence: Any) -> Any:
        method = getattr(self.mission_store, "record_progress", None)
        if callable(method):
            return method(_mission_id(mission), timestamp=timestamp, checkpoint=checkpoint, evidence=evidence)
        return mission

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
                mission = self._record_progress(mission, now, candidate_checkpoint, candidate_evidence)
                last, checkpoint, evidence, identity = now, candidate_checkpoint, candidate_evidence, candidate_identity
        if last is None:
            checkpoint = checkpoint or "lifecycle:%s" % _state_text(mission)
            evidence = evidence if evidence is not None else {"state": _state_text(mission)}
            mission = self._record_progress(mission, now, checkpoint, evidence)
            last = now
        elapsed = max(0.0, now - float(last))
        state = "PROGRESSING" if elapsed == 0 else "WORKING"
        reason = "material evidence advanced" if elapsed == 0 else "active mission awaiting material advancement"
        attempts = int(_field(mission, "recovery_attempts", default=metadata.get("recovery_attempts", 0)) or 0)
        outcome = _field(mission, "recovery_outcome", default=metadata.get("recovery_outcome"))
        recovery_state = "NONE"
        if elapsed > self.stall_threshold:
            state = "STALLED"; reason = "no material advancement for %.3f seconds" % elapsed
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
                    outcome = "%s: %s" % (type(exc).__name__, exc); recovery_state = "FAILED"
                recorder = getattr(self.mission_store, "record_recovery", None)
                if callable(recorder): recorder(_mission_id(mission), attempts, recovery_state, outcome)
        result = {"state": state, "mission_id": _mission_id(mission), "detail": reason, "reason": reason, "last_material_progress": float(last), "no_progress_duration": elapsed, "material_checkpoint": checkpoint, "material_evidence": _plain(evidence), "recovery_state": recovery_state, "recovery_attempts": attempts, "recovery_outcome": outcome}
        self._work_states[worker_id] = result
        return result

    @staticmethod
    def _decision_parts(decision: Any, missions: Sequence[Any], workers: Sequence[Any]) -> Tuple[Any, Any, str]:
        if decision is None: return None, None, "deterministic dispatch found no eligible pairing"
        reason = str(_field(decision, "reason", "detail", default=""))
        if isinstance(decision, (tuple, list)) and len(decision) >= 2: return decision[0], decision[1], reason
        mid, wid = _field(decision, "mission_id", "wp_id"), _field(decision, "worker_id", "agent_id")
        mission = _field(decision, "mission", "selected_mission") or next((m for m in missions if mid is not None and _mission_id(m) == str(mid)), None)
        worker = _field(decision, "worker", "selected_worker") or next((w for w in workers if wid is not None and str(_field(w, "worker_id", "agent_id")) == str(wid)), None)
        return mission, worker, reason

    def _http_execute(self, c: WorkerConfig, mission: Mapping[str, Any]) -> Any:
        request = urllib.request.Request("http://127.0.0.1:%s/execute" % c.port, data=json.dumps(dict(mission)).encode(), headers={"Accept": "application/json", "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=self.execute_timeout) as response:
            status = getattr(response, "status", response.getcode()); body = response.read().decode()
            if not 200 <= status < 300: raise RuntimeSupervisorError("worker execute returned HTTP %s" % status)
            return json.loads(body) if body else {}

    @staticmethod
    def _result_tokens(value: Any) -> set:
        if isinstance(value, Mapping): return set().union(*(RuntimeSupervisor._result_tokens(v) for v in value.values())) if value else set()
        if isinstance(value, (list, tuple, set)): return set().union(*(RuntimeSupervisor._result_tokens(v) for v in value)) if value else set()
        if isinstance(value, Enum): return {str(value.value).upper()}
        return {value.upper()} if isinstance(value, str) else set()

    @staticmethod
    def _qa_identity(value: Any) -> Optional[str]:
        if isinstance(value, Mapping):
            for key in ("qa_agent_id", "qa_worker_id", "reviewer_id"):
                if value.get(key): return str(value[key])
            for nested in value.values():
                found = RuntimeSupervisor._qa_identity(nested)
                if found: return found
        elif isinstance(value, (list, tuple)):
            for nested in value:
                found = RuntimeSupervisor._qa_identity(nested)
                if found: return found
        return None

    def work_control_once(self) -> Dict[str, Any]:
        with self._lock:
            if self.mission_store is None:
                return dict(self._last_work_control)
            now = float(self._clock()); missions = self._store_missions()
            accepted = {_mission_id(m) for m in missions if _state_text(m) in _ACCEPTED_STATES}
            active_by_worker = {}
            for m in missions:
                if _state_text(m) in _ACTIVE_STATES:
                    worker_id = _field(m, "assigned_worker", "assigned_worker_id", "worker_id", "agent_id", "assignee")
                    if worker_id: active_by_worker[str(worker_id)] = m
            workers, health_by_id = [], {}
            for c in self._configs:
                health = self.check_health(c); health_by_id[c.agent_id] = health
                verified = health.healthy and health.agent_id == c.agent_id
                available = bool(verified and c.role == "engineering" and c.agent_id not in active_by_worker)
                if verified: workers.append(self._make_worker_state(c, health, available, _mission_id(active_by_worker[c.agent_id]) if c.agent_id in active_by_worker else None))
                if c.agent_id in active_by_worker:
                    self._monitor_active(active_by_worker[c.agent_id], health, now)
                elif available:
                    self._work_states[c.agent_id] = {"state": "AVAILABLE", "mission_id": None, "detail": "verified healthy and available", "reason": "no assignment"}
                else:
                    self._work_states[c.agent_id] = {"state": "IDLE" if verified else "BLOCKED", "mission_id": None, "detail": "not an available engineering worker" if verified else "worker health not verified", "reason": "role or health prevents assignment"}
            source = self.mission_store if isinstance(self.mission_store, MissionStore) else missions
            decision = select_dispatch(source, workers)
            mission, worker, reason = self._decision_parts(decision, missions, workers)
            queued = [m for m in missions if _state_text(m) in _QUEUED_STATES and self._dependency_ready(m, accepted)]
            next_work = _mission_id(queued[0]) if queued else None
            if mission is None or worker is None:
                if queued:
                    key = "|".join(sorted(_mission_id(m) for m in queued))
                    if key != self._starvation_key: self._starvation_key, self._starvation_since = key, now
                    elapsed = max(0.0, now - float(self._starvation_since or now))
                    state = "STARVED" if elapsed >= self.starvation_threshold else "AVAILABLE"
                    why = reason or "eligible work has no assignment"
                else:
                    self._starvation_key = self._starvation_since = None; elapsed = 0.0; state = "IDLE"; why = reason or "no eligible work"
                self._last_work_control = {"state": state, "reason": why, "queued_ready": [_mission_id(m) for m in queued], "no_progress_duration": elapsed, "next_eligible_work": next_work}
                return dict(self._last_work_control)
            self._starvation_key = self._starvation_since = None
            worker_id = str(_field(worker, "worker_id", "agent_id", "identity")); c = self._config_by_id.get(worker_id); health = health_by_id.get(worker_id)
            project = str(_field(mission, "project", "project_id", default="")).lower()
            if not c or c.role != "engineering" or not health or not health.healthy or health.agent_id != worker_id or project not in self._authorized_projects(c, health):
                self._last_work_control = {"state": "STARVED", "mission_id": _mission_id(mission), "worker_id": worker_id, "reason": "selected worker failed health, role, availability, or project authorization validation", "next_eligible_work": _mission_id(mission)}
                return dict(self._last_work_control)
            mission_id = _mission_id(mission)
            current = self._transition(mission, "assigned", worker_id, {"event": "dispatch_reserved", "worker_id": worker_id}) or mission
            current = self._transition(current, "running", worker_id, {"event": "dispatch_started", "worker_id": worker_id}) or current
            self._work_states[worker_id] = {"state": "WORKING", "mission_id": mission_id, "detail": "dispatch in progress", "last_material_progress": now, "no_progress_duration": 0.0, "material_checkpoint": "lifecycle:running", "material_evidence": {"event": "dispatch_started"}}
            self._last_work_control = {"state": "WORKING", "mission_id": mission_id, "worker_id": worker_id, "reason": "durably reserved and dispatched", "last_material_progress": now}
        try:
            result = self._execute_requester(c, _plain(current)); tokens = self._result_tokens(result); qa_id = self._qa_identity(result); self_qa = qa_id == worker_id
            evidence = {"event": "worker_result", "worker_id": worker_id, "result": _plain(result)}
            if "QA_ACCEPTED" in tokens and qa_id and not self_qa:
                try:
                    current = self._transition(current, "completed", worker_id, evidence) or current
                    current = self._transition(current, "qa_pending", qa_id, evidence) or current
                    self._transition(current, "accepted", qa_id, evidence)
                except Exception:
                    current = self._transition(current, "qa_pending", worker_id, evidence) or current
                    self._transition(current, "accepted", worker_id, evidence)
                final, detail = "ACCEPTED", "engineering result independently QA accepted"
            elif "QA_REJECTED" in tokens or "REJECTED" in tokens or self_qa:
                try:
                    current = self._transition(current, "completed", worker_id, evidence) or current
                    current = self._transition(current, "qa_pending", qa_id or "QA-UNSPECIFIED", evidence) or current
                    self._transition(current, "rejected", qa_id or "QA-UNSPECIFIED", evidence)
                except Exception:
                    current = self._transition(current, "qa_pending", worker_id, evidence) or current
                    self._transition(current, "rejected", worker_id, evidence)
                final, detail = "REJECTED", "self-QA prohibited" if self_qa else "QA rejected"
            else:
                evidence["reason"] = "worker result did not contain independently attributed QA_ACCEPTED"
                self._transition(current, "blocked", worker_id, evidence); final, detail = "BLOCKED", evidence["reason"]
        except Exception as exc:
            evidence = {"event": "dispatch_failure", "worker_id": worker_id, "reason": "%s: %s" % (type(exc).__name__, exc), "retryable": True}
            try: self._transition(current, "blocked", worker_id, evidence)
            except Exception as persist: evidence["persistence_error"] = "%s: %s" % (type(persist).__name__, persist)
            final, detail = "BLOCKED", evidence["reason"]
        with self._lock:
            self._work_states[worker_id] = {"state": final, "mission_id": mission_id, "detail": detail, "reason": detail, "last_material_progress": float(self._clock()), "no_progress_duration": 0.0, "material_checkpoint": "lifecycle:%s" % final.lower(), "material_evidence": evidence}
            self._last_work_control = {"state": final, "mission_id": mission_id, "worker_id": worker_id, "reason": detail, "last_material_progress": float(self._clock()), "evidence": evidence}
            return dict(self._last_work_control)

    dispatch_once = work_control_once

    def status(self) -> List[WorkerStatus]:
        with self._lock: return [self._status_for(c) for c in self._configs]

    def status_dicts(self) -> List[Dict[str, Any]]:
        return [s.as_dict() for s in self.status()]

    def _status_for(self, c: WorkerConfig, known_health: Optional[HealthResult] = None) -> WorkerStatus:
        process = self._owned.get(c.agent_id); owned = process is not None and process.poll() is None
        health = known_health or self.check_health(c); ready = bool(health.healthy and health.agent_id == c.agent_id)
        payload = health.as_dict()
        if c.agent_id in self._last_errors: payload["supervisor_error"] = self._last_errors[c.agent_id]
        work = self._work_states.get(c.agent_id, {})
        return WorkerStatus(c.agent_id, c.name, c.port, bool(owned or ready or self._port_checker(c.port)), ready, payload, owned, self._restart_counts[c.agent_id], str(work.get("state", "AVAILABLE" if ready else "IDLE")), work.get("mission_id"), str(work.get("detail", "")), work.get("last_material_progress"), work.get("no_progress_duration"), work.get("material_checkpoint"), work.get("material_evidence"), str(work.get("reason", "")), str(work.get("recovery_state", "NONE")), int(work.get("recovery_attempts", 0)), work.get("recovery_outcome"), self._last_work_control.get("next_eligible_work"))

    def _resolve(self, worker: Any) -> WorkerConfig:
        if isinstance(worker, WorkerConfig):
            if self._config_by_id.get(worker.agent_id) != worker: raise KeyError("worker is not managed by this supervisor")
            return worker
        if str(worker) not in self._config_by_id: raise KeyError("unknown worker identity: %s" % worker)
        return self._config_by_id[str(worker)]

    def request_shutdown(self) -> None: self._shutdown_requested.set()

    def run(self, poll_interval: float = 1.0) -> None:
        self.start_all()
        try:
            while not self._shutdown_requested.wait(max(.05, poll_interval)): self.poll_once()
        finally: self.stop_all()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RVSC Golden Team runtime supervisor")
    parser.add_argument("action", nargs="?", choices=("run", "status"), default="run")
    parser.add_argument("--poll-interval", type=float, default=1.0); parser.add_argument("--max-restarts", type=int, default=3)
    parser.add_argument("--qa-endpoint", default=DEFAULT_QA_ENDPOINT); parser.add_argument("--worker-module", default="controller.generic_worker_host"); parser.add_argument("--mission-store")
    parser.add_argument("--stall-threshold", type=float, default=300.0); parser.add_argument("--starvation-threshold", type=float, default=0.0); parser.add_argument("--max-recovery-attempts", type=int, default=2)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    supervisor = RuntimeSupervisor(qa_endpoint=args.qa_endpoint, worker_module=args.worker_module, max_restarts=args.max_restarts, mission_store_path=args.mission_store, stall_threshold=args.stall_threshold, starvation_threshold=args.starvation_threshold, max_recovery_attempts=args.max_recovery_attempts)
    if args.action == "status":
        print(json.dumps({"workers": supervisor.status_dicts(), "work_control": supervisor.work_control_status}, indent=2, sort_keys=True)); return 0
    def stop_handler(_signum: int, _frame: Any) -> None: supervisor.request_shutdown()
    signal.signal(signal.SIGINT, stop_handler); signal.signal(signal.SIGTERM, stop_handler)
    try: supervisor.run(args.poll_interval)
    except RuntimeConflictError as exc:
        print("runtime supervisor conflict: %s" % exc, file=sys.stderr); return 2
    return 0


if __name__ == "__main__": raise SystemExit(main())
