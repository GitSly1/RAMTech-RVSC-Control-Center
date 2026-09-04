from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional


@dataclass(frozen=True)
class Event:
    event_type: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ExecutionPlan:
    trigger_id: str
    route: str
    actions: tuple[str, ...]


class OrchestrationError(ValueError):
    pass


def _matches(expected: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    return all(payload.get(k) == v for k, v in expected.items())


def worker_signal_event(signal: Any) -> Event:
    signal_type = getattr(signal, "signal_type", None)
    raw_type = getattr(signal_type, "value", signal_type)
    if not isinstance(raw_type, str) or not raw_type.strip(): raise OrchestrationError("worker signal requires signal_type")
    wp_id, agent_id = str(getattr(signal, "wp_id", "")).strip(), str(getattr(signal, "agent_id", "")).strip()
    if not wp_id or not agent_id: raise OrchestrationError("worker signal requires wp_id and agent_id")
    return Event("worker.%s" % raw_type.strip(), {"wp_id": wp_id, "agent_id": agent_id, "checkpoint": getattr(signal, "checkpoint", None), "evidence": tuple(getattr(signal, "evidence", ()) or ()), "failure_reason": getattr(signal, "failure_reason", None)})


def resolve_trigger(config: Mapping[str, Any], event: Event) -> Mapping[str, Any]:
    matches = [t for t in config.get("triggers", []) if t.get("event") == event.event_type and _matches(t.get("when", {}), event.payload)]
    if not matches: raise OrchestrationError("no trigger matched event: %s" % event.event_type)
    if len(matches) > 1: raise OrchestrationError("ambiguous trigger match: %s" % ", ".join(str(v.get("id")) for v in matches))
    return matches[0]


def build_execution_plan(config: Mapping[str, Any], event: Event) -> ExecutionPlan:
    trigger = resolve_trigger(config, event); route_name = trigger.get("route"); route = config.get("routes", {}).get(route_name)
    if not route: raise OrchestrationError("route not found: %s" % route_name)
    actions = tuple(route.get("actions", []))
    if not actions: raise OrchestrationError("route has no actions: %s" % route_name)
    return ExecutionPlan(str(trigger.get("id")), str(route_name), actions)


def dispatch_order(projects: Mapping[str, Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(k for k, _ in sorted(projects.items(), key=lambda item: (_priority_rank(item[1].get("priority", "P999")), item[0])))


class MissionState(str, Enum):
    QUEUED="queued"; BLOCKED="blocked"; ASSIGNED="assigned"; RUNNING="running"; COMPLETED="completed"; QA_PENDING="qa_pending"; ACCEPTED="accepted"; REJECTED="rejected"


_TRANSITIONS = {
    MissionState.QUEUED: {MissionState.BLOCKED, MissionState.ASSIGNED}, MissionState.BLOCKED: {MissionState.QUEUED},
    MissionState.ASSIGNED: {MissionState.QUEUED, MissionState.BLOCKED, MissionState.RUNNING}, MissionState.RUNNING: {MissionState.BLOCKED, MissionState.COMPLETED},
    MissionState.COMPLETED: {MissionState.QA_PENDING}, MissionState.QA_PENDING: {MissionState.ACCEPTED, MissionState.REJECTED},
    MissionState.ACCEPTED: set(), MissionState.REJECTED: {MissionState.QUEUED},
}


def _canonical(checkpoint: Any, evidence: Any) -> str:
    return json.dumps({"checkpoint": checkpoint, "evidence": evidence}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


@dataclass
class Mission:
    mission_id: str
    project_id: str
    state: MissionState = MissionState.QUEUED
    dependencies: tuple[str, ...] = ()
    dependency_policy: str = "accepted"
    priority: int = 999
    sequence: int = -1
    assigned_worker: Optional[str] = None
    implementer: Optional[str] = None
    qa_worker: Optional[str] = None
    block_reason: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    material_progress_at: Optional[float] = None
    material_checkpoint: Optional[str] = None
    material_evidence: Any = None
    material_identity: Optional[str] = None
    recovery_attempts: int = 0
    recovery_state: str = "NONE"
    recovery_outcome: Optional[str] = None

    def __post_init__(self) -> None:
        self.mission_id, self.project_id = str(self.mission_id).strip(), str(self.project_id).strip()
        try: self.state = MissionState(self.state)
        except ValueError as exc: raise OrchestrationError("unknown mission state: %s" % self.state) from exc
        self.dependencies = tuple(str(v).strip() for v in self.dependencies); self.dependency_policy = str(self.dependency_policy).strip().lower(); self.priority = _priority_rank(self.priority); self.metadata = dict(self.metadata)
        if not self.mission_id or not self.project_id: raise OrchestrationError("mission requires mission_id and project_id")
        if any(not v for v in self.dependencies) or len(set(self.dependencies)) != len(self.dependencies): raise OrchestrationError("mission dependencies must be non-empty and unique")
        if self.mission_id in self.dependencies: raise OrchestrationError("mission cannot depend on itself")
        if self.dependency_policy not in {"accepted", "completed"}: raise OrchestrationError("dependency_policy must be accepted or completed")
        if self.material_progress_at is not None: self.material_progress_at = float(self.material_progress_at)
        self.recovery_attempts = max(0, int(self.recovery_attempts))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["state"] = self.state.value; data["dependencies"] = list(self.dependencies); return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Mission": return cls(**dict(data))


class MissionStore:
    SCHEMA_VERSION = 2

    def __init__(self, path: Optional[os.PathLike[str] | str] = None) -> None:
        self.path = Path(path) if path is not None else None; self._missions: dict[str, Mission] = {}; self._next_sequence = 0

    @classmethod
    def load_or_create(cls, path: os.PathLike[str] | str) -> "MissionStore":
        return cls.load(path) if Path(path).exists() else cls(path)

    def _persist(self) -> None:
        if self.path is not None: self.save()

    def add(self, mission: Mission) -> Mission:
        if mission.mission_id in self._missions: raise OrchestrationError("duplicate mission: %s" % mission.mission_id)
        if mission.sequence < 0: mission.sequence = self._next_sequence
        self._next_sequence = max(self._next_sequence, mission.sequence + 1); self._missions[mission.mission_id] = mission; return mission

    def get(self, mission_id: str) -> Mission:
        try: return self._missions[mission_id]
        except KeyError as exc: raise OrchestrationError("mission not found: %s" % mission_id) from exc

    def all(self) -> tuple[Mission, ...]: return tuple(sorted(self._missions.values(), key=lambda m: (m.sequence, m.mission_id)))
    list_missions = all

    def readiness(self, mission_id: str) -> tuple[bool, Optional[str]]:
        mission = self.get(mission_id)
        if mission.state != MissionState.QUEUED: return False, "state:%s" % mission.state.value
        acceptable = {MissionState.ACCEPTED}
        if mission.dependency_policy == "completed": acceptable.update({MissionState.COMPLETED, MissionState.QA_PENDING})
        for dep_id in mission.dependencies:
            dep = self._missions.get(dep_id)
            if dep is None: return False, "dependency_missing:%s" % dep_id
            if dep.state not in acceptable: return False, "dependency_not_%s:%s:%s" % (mission.dependency_policy, dep_id, dep.state.value)
        return True, None

    def record_progress(self, mission_id: str, *, timestamp: float, checkpoint: Any, evidence: Any) -> Mission:
        mission = self.get(mission_id); identity = _canonical(checkpoint, evidence)
        if identity == mission.material_identity: return mission
        timestamp = float(timestamp)
        if mission.material_progress_at is not None and timestamp < mission.material_progress_at: raise OrchestrationError("material progress timestamp cannot move backwards")
        mission.material_progress_at = timestamp; mission.material_checkpoint = None if checkpoint is None else str(checkpoint); mission.material_evidence = evidence; mission.material_identity = identity
        mission.recovery_attempts = 0; mission.recovery_state = "NONE"; mission.recovery_outcome = None; self._persist(); return mission

    def record_recovery(self, mission_id: str, attempts: int, state: str, outcome: Optional[str]) -> Mission:
        mission = self.get(mission_id)
        if int(attempts) < mission.recovery_attempts: raise OrchestrationError("recovery attempts cannot decrease")
        mission.recovery_attempts = int(attempts); mission.recovery_state = str(state); mission.recovery_outcome = None if outcome is None else str(outcome); self._persist(); return mission

    def transition(self, mission_id: str, new_state: MissionState | str, *, worker_id: Optional[str] = None, reason: Optional[str] = None, evidence: Optional[Mapping[str, Any]] = None, timestamp: Optional[float] = None) -> Mission:
        mission = self.get(mission_id)
        try: target = MissionState(new_state)
        except ValueError as exc: raise OrchestrationError("unknown mission state: %s" % new_state) from exc
        if target == mission.state: raise OrchestrationError("ambiguous no-op transition: %s" % mission.state.value)
        if target not in _TRANSITIONS[mission.state]: raise OrchestrationError("invalid mission transition: %s -> %s" % (mission.state.value, target.value))
        worker = str(worker_id).strip() if worker_id is not None else None
        if worker_id is not None and not worker: raise OrchestrationError("worker_id cannot be empty")
        if target == MissionState.ASSIGNED:
            ready, blocked = self.readiness(mission_id)
            if not ready: raise OrchestrationError("mission is not dispatch-eligible: %s" % blocked)
            if not worker: raise OrchestrationError("assignment requires worker_id")
            mission.assigned_worker = mission.implementer = worker
        elif target in {MissionState.RUNNING, MissionState.COMPLETED}:
            if not mission.assigned_worker: raise OrchestrationError("active transition requires an assigned worker")
            if worker and worker != mission.assigned_worker: raise OrchestrationError("worker does not own the mission assignment")
        elif target == MissionState.QA_PENDING:
            if not worker: raise OrchestrationError("QA transition requires worker_id")
            if worker == mission.implementer: raise OrchestrationError("implementer cannot satisfy independent QA")
            mission.qa_worker = worker
        elif target in {MissionState.ACCEPTED, MissionState.REJECTED}:
            if not mission.qa_worker: raise OrchestrationError("QA outcome requires an assigned QA worker")
            if worker and worker != mission.qa_worker: raise OrchestrationError("QA outcome worker does not own the QA assignment")
            if mission.qa_worker == mission.implementer: raise OrchestrationError("implementer cannot satisfy independent QA")
        if target == MissionState.BLOCKED:
            if not reason or not str(reason).strip(): raise OrchestrationError("blocked transition requires a reason")
            mission.block_reason = str(reason).strip()
        elif target == MissionState.QUEUED:
            mission.assigned_worker = mission.qa_worker = None; mission.block_reason = None
        elif target == MissionState.COMPLETED: mission.assigned_worker = None
        mission.state = target
        if timestamp is not None:
            identity = _canonical("lifecycle:%s" % target.value, dict(evidence or {}))
            if identity != mission.material_identity:
                mission.material_progress_at = float(timestamp); mission.material_checkpoint = "lifecycle:%s" % target.value; mission.material_evidence = dict(evidence or {}); mission.material_identity = identity; mission.recovery_attempts = 0; mission.recovery_state = "NONE"; mission.recovery_outcome = None
        self._persist(); return mission

    def save(self, path: Optional[os.PathLike[str] | str] = None) -> None:
        destination = Path(path) if path is not None else self.path
        if destination is None: raise OrchestrationError("mission store path is required")
        destination.parent.mkdir(parents=True, exist_ok=True); document = {"schema_version": self.SCHEMA_VERSION, "missions": [m.to_dict() for m in self.all()]}; temporary_name = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, prefix=".%s." % destination.name, suffix=".tmp", delete=False) as handle:
                temporary_name = handle.name; json.dump(document, handle, ensure_ascii=False, sort_keys=True, indent=2); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary_name, destination)
        except (OSError, TypeError, ValueError) as exc:
            if temporary_name:
                try: os.unlink(temporary_name)
                except OSError: pass
            raise OrchestrationError("unable to persist mission store: %s" % exc) from exc
        self.path = destination

    @classmethod
    def load(cls, path: os.PathLike[str] | str) -> "MissionStore":
        source = Path(path)
        try:
            with source.open("r", encoding="utf-8") as handle: document = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc: raise OrchestrationError("unable to load mission store: %s" % exc) from exc
        if not isinstance(document, Mapping) or document.get("schema_version") not in {1, cls.SCHEMA_VERSION}: raise OrchestrationError("unsupported mission store schema")
        raw = document.get("missions")
        if not isinstance(raw, list): raise OrchestrationError("mission store requires a missions list")
        store = cls(source)
        for item in raw:
            if not isinstance(item, Mapping): raise OrchestrationError("invalid mission record")
            store.add(Mission.from_dict(item))
        return store


MissionQueue = MissionStore


@dataclass(frozen=True)
class WorkerState:
    worker_id: str
    authorized_projects: tuple[str, ...]
    available: bool = True
    healthy: bool = True
    active_mission_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.worker_id.strip(): raise OrchestrationError("worker requires worker_id")
        if not self.authorized_projects: raise OrchestrationError("worker requires at least one authorized project")


@dataclass(frozen=True)
class DispatchDecision:
    outcome: str
    mission_id: Optional[str] = None
    worker_id: Optional[str] = None
    reason: Optional[str] = None

    @property
    def is_dispatch(self) -> bool: return self.outcome == "dispatch"


def _priority_rank(value: Any) -> int:
    raw = str(value).strip().upper().removeprefix("P")
    try: return int(raw)
    except ValueError: return 999


def select_dispatch(store: MissionStore, workers: Iterable[WorkerState | Mapping[str, Any]]) -> DispatchDecision:
    missions = []
    for mission in store.all():
        if store.readiness(mission.mission_id)[0]: missions.append(mission)
    missions.sort(key=lambda m: (m.priority, m.sequence, m.mission_id))
    if not missions: return DispatchDecision("idle", reason="no_eligible_work")
    normalized = []
    for worker in workers:
        if isinstance(worker, Mapping):
            wid = str(worker.get("worker_id", worker.get("agent_id", ""))).strip(); projects = worker.get("authorized_projects", worker.get("projects", ())); available = bool(worker.get("available", True)); healthy = bool(worker.get("healthy", True)); active = worker.get("active_mission_id")
        else:
            wid, projects, available, healthy, active = worker.worker_id.strip(), worker.authorized_projects, worker.available, worker.healthy, worker.active_mission_id
        if not wid: raise OrchestrationError("worker requires worker_id")
        if isinstance(projects, str): projects = (projects,)
        normalized.append((wid, tuple(str(p).strip() for p in projects), available, healthy, active))
    ids = [w[0] for w in normalized]
    if len(ids) != len(set(ids)): raise OrchestrationError("worker ids must be unique")
    for mission in missions:
        for wid, projects, available, healthy, active in sorted(normalized):
            if available and healthy and not active and mission.project_id in projects: return DispatchDecision("dispatch", mission.mission_id, wid, "eligible_worker_selected")
    return DispatchDecision("starved", reason="eligible_work_has_no_authorized_available_worker")


def dispatch_next(store: MissionStore, workers: Iterable[WorkerState | Mapping[str, Any]]) -> DispatchDecision:
    decision = select_dispatch(store, workers)
    if decision.is_dispatch: store.transition(decision.mission_id or "", MissionState.ASSIGNED, worker_id=decision.worker_id)
    return decision


deterministic_dispatch = select_dispatch
