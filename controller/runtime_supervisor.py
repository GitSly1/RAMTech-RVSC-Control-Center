"""Controlled runtime supervision and durable RVSC work dispatch."""

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
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from controller.orchestrator import MissionStore, WorkerState, select_dispatch


REPOSITORY_ENV_KEYS = (
    "RVSC_RVSC_REPO",
    "RVSC_SEMANTIQ_REPO",
    "RVSC_MOXIE_REPO",
)
QA_ROUTING_ENV_KEYS = (
    "RVSC_QA_ENDPOINT",
    "RVSC_QA_URL",
    "RVSC_QA_WORKER_ENDPOINT",
    "RVSC_QA_WORKER_URL",
)
DEFAULT_QA_ENDPOINT = "http://127.0.0.1:8771/execute"
_ACTIVE_STATES = {"assigned", "running", "qa_pending", "qa_review"}
_QUEUED_STATES = {"queued", "retryable"}
_ACCEPTED_STATES = {"accepted", "qa_accepted"}


class RuntimeSupervisorError(RuntimeError):
    """Base error raised by the runtime supervisor."""


class RuntimeConflictError(RuntimeSupervisorError):
    """Raised when a configured port is occupied by an unexpected worker."""


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
        return {
            "healthy": self.healthy,
            "agent_id": self.agent_id,
            "detail": self.detail,
            "payload": self.payload,
        }


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
        return {key: _plain(item) for key, item in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "as_dict"):
        return _plain(value.as_dict())
    if hasattr(value, "to_dict"):
        return _plain(value.to_dict())
    if hasattr(value, "__dict__"):
        return {
            key: _plain(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)


class RuntimeSupervisor:
    """Monitor worker processes and optionally operate a durable work cycle."""

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
    ) -> None:
        selected = tuple(configs or golden_team_configs())
        self._configs = self._validate_configs(selected)
        self._config_by_id = {item.agent_id: item for item in self._configs}
        self.qa_endpoint = qa_endpoint.rstrip("/")
        self.worker_module = worker_module
        self.max_restarts = max(0, int(max_restarts))
        self.health_timeout = max(0.01, float(health_timeout))
        self.execute_timeout = max(0.01, float(execute_timeout))
        self._process_factory = process_factory or subprocess.Popen
        self._health_checker = health_checker or self._http_health
        self._port_checker = port_checker or self._is_port_open
        self._execute_requester = execute_requester or self._http_execute
        self._owned: Dict[str, Any] = {}
        self._restart_counts = {item.agent_id: 0 for item in self._configs}
        self._intentional_stops = set()
        self._last_errors: Dict[str, str] = {}
        self._work_states: Dict[str, Dict[str, Any]] = {}
        self._last_work_control: Dict[str, Any] = {
            "state": "DISABLED",
            "reason": "no mission store configured",
        }
        self._lock = threading.RLock()
        self._shutdown_requested = threading.Event()

        supplied = repository_mappings or {}
        unknown = set(supplied).difference(REPOSITORY_ENV_KEYS)
        if unknown:
            raise ValueError("unsupported repository mapping keys: %s" % sorted(unknown))
        self.repository_mappings: Dict[str, str] = {}
        for key in REPOSITORY_ENV_KEYS:
            value = supplied.get(key, os.environ.get(key))
            if value:
                self.repository_mappings[key] = str(value)

        if mission_store is not None and mission_store_path is not None:
            raise ValueError("supply mission_store or mission_store_path, not both")
        self.mission_store = mission_store
        if mission_store_path is not None:
            self.mission_store = MissionStore(mission_store_path)

    @staticmethod
    def _validate_configs(configs: Sequence[WorkerConfig]) -> Tuple[WorkerConfig, ...]:
        if not configs:
            raise ValueError("at least one worker configuration is required")
        identities = set()
        ports = set()
        for config in configs:
            if not config.agent_id or not config.name:
                raise ValueError("worker identity and name are required")
            if config.agent_id in identities:
                raise ValueError("duplicate worker identity: %s" % config.agent_id)
            if config.port in ports:
                raise ValueError("duplicate worker port: %s" % config.port)
            if not 1 <= int(config.port) <= 65535:
                raise ValueError("invalid worker port: %s" % config.port)
            identities.add(config.agent_id)
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
        environment = os.environ.copy()
        environment.update(self.repository_mappings)
        environment.update({
            "RVSC_AGENT_ID": config.agent_id,
            "RVSC_WORKER_AGENT_ID": config.agent_id,
            "RVSC_AGENT_NAME": config.name,
            "RVSC_AGENT_ROLE": config.role,
            "RVSC_WORKER_PORT": str(config.port),
            "RVSC_PORT": str(config.port),
        })
        if config.role == "engineering":
            environment.update({key: self.qa_endpoint for key in QA_ROUTING_ENV_KEYS})
        else:
            for key in QA_ROUTING_ENV_KEYS:
                environment.pop(key, None)
        return command, environment

    def start(self, worker: Any) -> WorkerStatus:
        config = self._resolve(worker)
        with self._lock:
            process = self._owned.get(config.agent_id)
            if process is not None and process.poll() is None:
                return self._status_for(config)
            if process is not None:
                self._owned.pop(config.agent_id, None)
            health = self.check_health(config)
            occupied = bool(self._port_checker(config.port))
            if health.healthy:
                if health.agent_id != config.agent_id:
                    raise RuntimeConflictError(
                        "port %s reports identity %r; expected %s"
                        % (config.port, health.agent_id, config.agent_id)
                    )
                self._intentional_stops.discard(config.agent_id)
                self._restart_counts[config.agent_id] = 0
                self._last_errors.pop(config.agent_id, None)
                return self._status_for(config, known_health=health)
            if occupied:
                raise RuntimeConflictError(
                    "port %s is occupied without a healthy matching identity" % config.port
                )
            self._intentional_stops.discard(config.agent_id)
            self._restart_counts[config.agent_id] = 0
            self._spawn(config)
            return self._status_for(config)

    def start_all(self) -> List[WorkerStatus]:
        started_here = []
        try:
            for config in self._configs:
                was_owned = config.agent_id in self._owned
                self.start(config)
                if not was_owned and config.agent_id in self._owned:
                    started_here.append(config.agent_id)
        except Exception:
            for agent_id in reversed(started_here):
                self.stop(agent_id)
            raise
        return self.status()

    def _spawn(self, config: WorkerConfig) -> Any:
        command, environment = self.build_launch(config)
        process = self._process_factory(command, env=environment)
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
            self.stop(config, timeout=timeout)

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
        identity = payload.get("agent_id") or payload.get("identity") or payload.get("id")
        for nested_key in ("worker", "agent"):
            nested = payload.get(nested_key)
            if identity is None and isinstance(nested, Mapping):
                identity = nested.get("agent_id") or nested.get("identity") or nested.get("id")
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
        detail = str(payload.get("detail") or payload.get("message") or "")
        return HealthResult(healthy, str(identity) if identity is not None else None, detail, payload)

    def _http_health(self, config: WorkerConfig) -> HealthResult:
        request = urllib.request.Request(
            "http://127.0.0.1:%s/health" % config.port,
            headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.health_timeout) as response:
                status = getattr(response, "status", response.getcode())
                if status < 200 or status >= 300:
                    return HealthResult(False, detail="HTTP %s" % status)
                return self._normalise_health(json.loads(response.read().decode("utf-8")))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return HealthResult(False, detail="unavailable: %s" % exc)

    @staticmethod
    def _is_port_open(port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(0.2)
            return sock.connect_ex(("127.0.0.1", int(port))) == 0
        finally:
            sock.close()

    def poll_once(self) -> List[WorkerStatus]:
        """Apply bounded process recovery and one optional work-control cycle."""
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
                    if health.agent_id == config.agent_id:
                        continue
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
        for name in ("list_missions", "missions", "list", "all"):
            member = getattr(self.mission_store, name, None)
            if member is None:
                continue
            value = member() if callable(member) else member
            if isinstance(value, Mapping):
                value = value.get("missions", list(value.values()))
            return list(value or [])
        raise RuntimeSupervisorError("MissionStore does not expose durable missions")

    def _authorized_projects(self, config: WorkerConfig, health: HealthResult) -> Tuple[str, ...]:
        projects = set(str(item).lower() for item in config.authorized_projects)
        advertised = health.payload.get("authorized_projects") or health.payload.get("projects") or ()
        if isinstance(advertised, str):
            advertised = [advertised]
        projects.update(str(item).lower() for item in advertised)
        for key in self.repository_mappings:
            projects.add(key[len("RVSC_"):-len("_REPO")].lower())
        return tuple(sorted(projects))

    def _make_worker_state(
        self,
        config: WorkerConfig,
        health: HealthResult,
        available: bool,
        mission_id: Optional[str],
    ) -> Any:
        values = {
            "agent_id": config.agent_id,
            "worker_id": config.agent_id,
            "identity": config.agent_id,
            "role": config.role,
            "healthy": True,
            "ready": True,
            "available": available,
            "status": "AVAILABLE" if available else "ASSIGNED",
            "state": "AVAILABLE" if available else "ASSIGNED",
            "authorized_projects": self._authorized_projects(config, health),
            "projects": self._authorized_projects(config, health),
            "current_mission_id": mission_id,
            "mission_id": mission_id,
        }
        try:
            signature = inspect.signature(WorkerState)
            kwargs = {
                name: values[name]
                for name in signature.parameters
                if name in values
            }
            return WorkerState(**kwargs)
        except (TypeError, ValueError):
            return SimpleNamespace(**values)

    @staticmethod
    def _dependency_ready(mission: Any, accepted: set) -> bool:
        dependencies = _field(mission, "dependencies", "depends_on", default=()) or ()
        if isinstance(dependencies, str):
            dependencies = [dependencies]
        return all(str(item) in accepted for item in dependencies)

    def _transition(
        self,
        mission: Any,
        state: str,
        worker_id: Optional[str] = None,
        evidence: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        mission_id = _mission_id(mission)
        current = _field(mission, "status", "state", "lifecycle_state")
        target: Any = state
        if isinstance(current, Enum):
            enum_type = type(current)
            for candidate in (state, state.lower(), state.upper()):
                try:
                    target = enum_type(candidate)
                    break
                except ValueError:
                    try:
                        target = enum_type[candidate.upper()]
                        break
                    except KeyError:
                        pass
        values = {
            "mission_id": mission_id,
            "wp_id": mission_id,
            "id": mission_id,
            "state": target,
            "status": target,
            "new_state": target,
            "target_state": target,
            "to_state": target,
            "from_state": current,
            "current_state": current,
            "worker_id": worker_id,
            "agent_id": worker_id,
            "assigned_worker_id": worker_id,
            "assignee": worker_id,
            "evidence": dict(evidence or {}),
            "detail": dict(evidence or {}),
            "reason": str((evidence or {}).get("reason", "")),
        }
        for name in ("transition", "transition_mission", "set_status", "update_status"):
            method = getattr(self.mission_store, name, None)
            if not callable(method):
                continue
            signature = inspect.signature(method)
            args = []
            kwargs = {}
            for parameter in signature.parameters.values():
                if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                    continue
                if parameter.name in values:
                    if parameter.kind == parameter.POSITIONAL_ONLY:
                        args.append(values[parameter.name])
                    else:
                        kwargs[parameter.name] = values[parameter.name]
                elif parameter.default is parameter.empty:
                    break
            else:
                return method(*args, **kwargs)
        raise RuntimeSupervisorError("MissionStore does not expose lifecycle transitions")

    @staticmethod
    def _decision_parts(decision: Any, missions: Sequence[Any], workers: Sequence[Any]) -> Tuple[Any, Any, str]:
        if decision is None:
            return None, None, "deterministic dispatch found no eligible pairing"
        reason = str(_field(decision, "reason", "detail", default=""))
        if isinstance(decision, (tuple, list)) and len(decision) >= 2:
            mission, worker = decision[0], decision[1]
        else:
            mission = _field(decision, "mission", "selected_mission")
            worker = _field(decision, "worker", "selected_worker")
            mission_key = _field(decision, "mission_id", "wp_id")
            worker_key = _field(decision, "worker_id", "agent_id")
            if mission is None and mission_key is not None:
                mission = next((item for item in missions if _mission_id(item) == str(mission_key)), None)
            if worker is None and worker_key is not None:
                worker = next(
                    (item for item in workers if str(_field(item, "agent_id", "worker_id", "identity")) == str(worker_key)),
                    None,
                )
        return mission, worker, reason

    def _http_execute(self, config: WorkerConfig, mission: Mapping[str, Any]) -> Any:
        body = json.dumps(dict(mission)).encode("utf-8")
        request = urllib.request.Request(
            "http://127.0.0.1:%s/execute" % config.port,
            data=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.execute_timeout) as response:
            status = getattr(response, "status", response.getcode())
            payload = response.read().decode("utf-8")
            if status < 200 or status >= 300:
                raise RuntimeSupervisorError("worker execute returned HTTP %s" % status)
            return json.loads(payload) if payload else {}

    @staticmethod
    def _result_tokens(value: Any) -> set:
        tokens = set()
        if isinstance(value, Mapping):
            for item in value.values():
                tokens.update(RuntimeSupervisor._result_tokens(item))
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                tokens.update(RuntimeSupervisor._result_tokens(item))
        elif isinstance(value, Enum):
            tokens.add(str(value.value).upper())
        elif isinstance(value, str):
            tokens.add(value.upper())
        return tokens

    @staticmethod
    def _qa_identity(result: Any) -> Optional[str]:
        if isinstance(result, Mapping):
            for key in ("qa_agent_id", "qa_worker_id", "reviewer_id"):
                if result.get(key):
                    return str(result[key])
            for value in result.values():
                found = RuntimeSupervisor._qa_identity(value)
                if found:
                    return found
        elif isinstance(result, (list, tuple)):
            for value in result:
                found = RuntimeSupervisor._qa_identity(value)
                if found:
                    return found
        return None

    def work_control_once(self) -> Dict[str, Any]:
        """Select and synchronously dispatch at most one durable mission."""
        with self._lock:
            if self.mission_store is None:
                self._last_work_control = {"state": "DISABLED", "reason": "no mission store configured"}
                return dict(self._last_work_control)
            missions = self._store_missions()
            accepted = {
                _mission_id(item) for item in missions if _state_text(item) in _ACCEPTED_STATES
            }
            active_by_worker: Dict[str, str] = {}
            for item in missions:
                if _state_text(item) in _ACTIVE_STATES:
                    worker_id = _field(item, "assigned_worker_id", "worker_id", "agent_id", "assignee")
                    if worker_id:
                        active_by_worker[str(worker_id)] = _mission_id(item)
            worker_states = []
            health_by_id: Dict[str, HealthResult] = {}
            for config in self._configs:
                health = self.check_health(config)
                health_by_id[config.agent_id] = health
                verified = bool(health.healthy and health.agent_id == config.agent_id)
                available = verified and config.role == "engineering" and config.agent_id not in active_by_worker
                if verified:
                    worker_states.append(
                        self._make_worker_state(
                            config,
                            health,
                            available,
                            active_by_worker.get(config.agent_id),
                        )
                    )
                if config.agent_id in active_by_worker:
                    self._work_states[config.agent_id] = {
                        "state": "RUNNING",
                        "mission_id": active_by_worker[config.agent_id],
                        "detail": "durable active mission",
                    }
                elif available:
                    self._work_states[config.agent_id] = {
                        "state": "AVAILABLE",
                        "mission_id": None,
                        "detail": "verified healthy and available",
                    }
                else:
                    self._work_states[config.agent_id] = {
                        "state": "IDLE" if verified else "BLOCKED",
                        "mission_id": None,
                        "detail": "not an available engineering worker" if verified else "worker health not verified",
                    }
            decision = select_dispatch(missions, worker_states)
            mission, worker, reason = self._decision_parts(decision, missions, worker_states)
            queued_ready = [
                item for item in missions
                if _state_text(item) in _QUEUED_STATES and self._dependency_ready(item, accepted)
            ]
            if mission is None or worker is None:
                state = "STARVED" if queued_ready else "IDLE"
                self._last_work_control = {
                    "state": state,
                    "reason": reason or ("no eligible worker" if queued_ready else "no dependency-ready queued mission"),
                    "queued_ready": [_mission_id(item) for item in queued_ready],
                }
                return dict(self._last_work_control)
            worker_id = str(_field(worker, "agent_id", "worker_id", "identity"))
            config = self._config_by_id.get(worker_id)
            project = str(_field(mission, "project", "project_id", default="")).lower()
            health = health_by_id.get(worker_id)
            authorized = config and health and project in self._authorized_projects(config, health)
            if not config or config.role != "engineering" or not health or not health.healthy or health.agent_id != worker_id or not authorized:
                self._last_work_control = {
                    "state": "STARVED",
                    "mission_id": _mission_id(mission),
                    "worker_id": worker_id,
                    "reason": "selected worker failed health, role, availability, or project authorization validation",
                }
                return dict(self._last_work_control)
            mission_id = _mission_id(mission)
            assigned = self._transition(
                mission,
                "assigned",
                worker_id,
                {"event": "dispatch_reserved", "worker_id": worker_id},
            )
            current = assigned if assigned is not None else mission
            running = self._transition(
                current,
                "running",
                worker_id,
                {"event": "dispatch_started", "worker_id": worker_id},
            )
            current = running if running is not None else current
            self._work_states[worker_id] = {
                "state": "RUNNING",
                "mission_id": mission_id,
                "detail": "dispatch in progress",
            }
            self._last_work_control = {
                "state": "RUNNING",
                "mission_id": mission_id,
                "worker_id": worker_id,
                "reason": "durably reserved and dispatched",
            }
        try:
            result = self._execute_requester(config, _plain(current))
            tokens = self._result_tokens(result)
            qa_identity = self._qa_identity(result)
            self_qa = qa_identity == worker_id
            evidence = {"event": "worker_result", "worker_id": worker_id, "result": _plain(result)}
            if "QA_ACCEPTED" in tokens and not self_qa:
                qa_pending = self._transition(current, "qa_pending", worker_id, evidence)
                current = qa_pending if qa_pending is not None else current
                self._transition(current, "accepted", worker_id, evidence)
                final_state = "ACCEPTED"
                detail = "engineering result independently QA accepted"
            elif "QA_REJECTED" in tokens or "REJECTED" in tokens or self_qa:
                qa_pending = self._transition(current, "qa_pending", worker_id, evidence)
                current = qa_pending if qa_pending is not None else current
                self._transition(current, "rejected", worker_id, evidence)
                final_state = "REJECTED"
                detail = "self-QA prohibited" if self_qa else "QA rejected"
            else:
                evidence["reason"] = "worker result did not contain QA_ACCEPTED"
                self._transition(current, "blocked", worker_id, evidence)
                final_state = "BLOCKED"
                detail = evidence["reason"]
        except Exception as exc:
            evidence = {
                "event": "dispatch_failure",
                "worker_id": worker_id,
                "reason": "%s: %s" % (type(exc).__name__, exc),
                "retryable": True,
            }
            try:
                self._transition(current, "blocked", worker_id, evidence)
            except Exception as transition_exc:
                evidence["persistence_error"] = "%s: %s" % (type(transition_exc).__name__, transition_exc)
            final_state = "BLOCKED"
            detail = evidence["reason"]
        with self._lock:
            self._work_states[worker_id] = {
                "state": final_state,
                "mission_id": mission_id,
                "detail": detail,
            }
            self._last_work_control = {
                "state": final_state,
                "mission_id": mission_id,
                "worker_id": worker_id,
                "reason": detail,
            }
            return dict(self._last_work_control)

    dispatch_once = work_control_once

    def status(self) -> List[WorkerStatus]:
        with self._lock:
            return [self._status_for(config) for config in self._configs]

    def status_dicts(self) -> List[Dict[str, Any]]:
        return [item.as_dict() for item in self.status()]

    def _status_for(self, config: WorkerConfig, known_health: Optional[HealthResult] = None) -> WorkerStatus:
        process = self._owned.get(config.agent_id)
        owned_running = process is not None and process.poll() is None
        health = known_health or self.check_health(config)
        ready = bool(health.healthy and health.agent_id == config.agent_id)
        running = bool(owned_running or ready or self._port_checker(config.port))
        health_payload = health.as_dict()
        if config.agent_id in self._last_errors:
            health_payload["supervisor_error"] = self._last_errors[config.agent_id]
        work = self._work_states.get(config.agent_id, {})
        return WorkerStatus(
            agent_id=config.agent_id,
            name=config.name,
            port=config.port,
            running=running,
            ready=ready,
            health_result=health_payload,
            supervisor_owned=owned_running,
            restart_count=self._restart_counts[config.agent_id],
            work_state=str(work.get("state", "AVAILABLE" if ready else "IDLE")),
            mission_id=work.get("mission_id"),
            work_detail=str(work.get("detail", "")),
        )

    def _resolve(self, worker: Any) -> WorkerConfig:
        if isinstance(worker, WorkerConfig):
            configured = self._config_by_id.get(worker.agent_id)
            if configured != worker:
                raise KeyError("worker is not managed by this supervisor")
            return configured
        try:
            return self._config_by_id[str(worker)]
        except KeyError:
            raise KeyError("unknown worker identity: %s" % worker)

    def request_shutdown(self) -> None:
        self._shutdown_requested.set()

    def run(self, poll_interval: float = 1.0) -> None:
        self.start_all()
        try:
            while not self._shutdown_requested.wait(max(0.05, poll_interval)):
                self.poll_once()
        finally:
            self.stop_all()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RVSC Golden Team runtime supervisor")
    parser.add_argument("action", nargs="?", choices=("run", "status"), default="run")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--max-restarts", type=int, default=3)
    parser.add_argument("--qa-endpoint", default=DEFAULT_QA_ENDPOINT)
    parser.add_argument("--worker-module", default="controller.generic_worker_host")
    parser.add_argument("--mission-store")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    supervisor = RuntimeSupervisor(
        qa_endpoint=args.qa_endpoint,
        worker_module=args.worker_module,
        max_restarts=args.max_restarts,
        mission_store_path=args.mission_store,
    )
    if args.action == "status":
        print(json.dumps({
            "workers": supervisor.status_dicts(),
            "work_control": supervisor.work_control_status,
        }, indent=2, sort_keys=True))
        return 0

    def stop_handler(_signum: int, _frame: Any) -> None:
        supervisor.request_shutdown()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    try:
        supervisor.run(poll_interval=args.poll_interval)
    except RuntimeConflictError as exc:
        print("runtime supervisor conflict: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
