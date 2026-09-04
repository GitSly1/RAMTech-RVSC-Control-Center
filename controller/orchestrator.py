from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence


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
    return all(payload.get(key) == value for key, value in expected.items())


def worker_signal_event(signal: Any) -> Event:
    """Normalize a worker-runtime signal into an orchestration event."""
    signal_type = getattr(signal, "signal_type", None)
    raw_type = getattr(signal_type, "value", signal_type)
    if not isinstance(raw_type, str) or not raw_type.strip():
        raise OrchestrationError("worker signal requires signal_type")
    wp_id = str(getattr(signal, "wp_id", "")).strip()
    agent_id = str(getattr(signal, "agent_id", "")).strip()
    if not wp_id or not agent_id:
        raise OrchestrationError("worker signal requires wp_id and agent_id")
    payload = {
        "wp_id": wp_id,
        "agent_id": agent_id,
        "checkpoint": getattr(signal, "checkpoint", None),
        "evidence": tuple(getattr(signal, "evidence", ()) or ()),
        "failure_reason": getattr(signal, "failure_reason", None),
    }
    return Event(event_type=f"worker.{raw_type.strip()}", payload=payload)


def resolve_trigger(config: Mapping[str, Any], event: Event) -> Mapping[str, Any]:
    matches = []
    for trigger in config.get("triggers", []):
        if trigger.get("event") != event.event_type:
            continue
        if not _matches(trigger.get("when", {}), event.payload):
            continue
        matches.append(trigger)

    if not matches:
        raise OrchestrationError(f"no trigger matched event: {event.event_type}")
    if len(matches) > 1:
        ids = ", ".join(str(item.get("id")) for item in matches)
        raise OrchestrationError(f"ambiguous trigger match: {ids}")
    return matches[0]


def build_execution_plan(config: Mapping[str, Any], event: Event) -> ExecutionPlan:
    trigger = resolve_trigger(config, event)
    route_name = trigger.get("route")
    route = config.get("routes", {}).get(route_name)
    if not route:
        raise OrchestrationError(f"route not found: {route_name}")

    actions = tuple(route.get("actions", []))
    if not actions:
        raise OrchestrationError(f"route has no actions: {route_name}")

    return ExecutionPlan(trigger_id=str(trigger.get("id")), route=str(route_name), actions=actions)


def dispatch_order(projects: Mapping[str, Mapping[str, Any]]) -> tuple[str, ...]:
    """Return project ids in deterministic dispatch priority order."""
    def priority(item: tuple[str, Mapping[str, Any]]) -> tuple[int, str]:
        project_id, policy = item
        raw = str(policy.get("priority", "P999")).upper()
        try:
            rank = int(raw.removeprefix("P"))
        except ValueError:
            rank = 999
        return rank, project_id

    return tuple(project_id for project_id, _ in sorted(projects.items(), key=priority))


class MissionState(str, Enum):
    QUEUED = "queued"
    BLOCKED = "blocked"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    QA_PENDING = "qa_pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


_TRANSITIONS = {
    MissionState.QUEUED: {MissionState.BLOCKED, MissionState.ASSIGNED},
    MissionState.BLOCKED: {MissionState.QUEUED},
    MissionState.ASSIGNED: {MissionState.QUEUED, MissionState.BLOCKED, MissionState.RUNNING},
    MissionState.RUNNING: {MissionState.BLOCKED, MissionState.COMPLETED},
    MissionState.COMPLETED: {MissionState.QA_PENDING},
    MissionState.QA_PENDING: {MissionState.ACCEPTED, MissionState.REJECTED},
    MissionState.ACCEPTED: set(),
    MissionState.REJECTED: {MissionState.QUEUED},
}


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

    def __post_init__(self) -> None:
        self.mission_id = str(self.mission_id).strip()
        self.project_id = str(self.project_id).strip()
        try:
            self.state = MissionState(self.state)
        except ValueError as exc:
            raise OrchestrationError(f"unknown mission state: {self.state}") from exc
        self.dependencies = tuple(str(item).strip() for item in self.dependencies)
        self.dependency_policy = str(self.dependency_policy).strip().lower()
        self.priority = _priority_rank(self.priority)
        self.metadata = dict(self.metadata)
        if not self.mission_id or not self.project_id:
            raise OrchestrationError("mission requires mission_id and project_id")
        if any(not item for item in self.dependencies):
            raise OrchestrationError("mission dependencies cannot be empty")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise OrchestrationError("mission dependencies must be unique")
        if self.mission_id in self.dependencies:
            raise OrchestrationError("mission cannot depend on itself")
        if self.dependency_policy not in {"accepted", "completed"}:
            raise OrchestrationError("dependency_policy must be accepted or completed")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        data["dependencies"] = list(self.dependencies)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Mission":
        return cls(**dict(data))


class MissionStore:
    """Durable mission store with atomic, deterministic JSON persistence."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Optional[os.PathLike[str] | str] = None) -> None:
        self.path = Path(path) if path is not None else None
        self._missions: dict[str, Mission] = {}
        self._next_sequence = 0

    def add(self, mission: Mission) -> Mission:
        if mission.mission_id in self._missions:
            raise OrchestrationError(f"duplicate mission: {mission.mission_id}")
        if mission.sequence < 0:
            mission.sequence = self._next_sequence
        self._next_sequence = max(self._next_sequence, mission.sequence + 1)
        self._missions[mission.mission_id] = mission
        return mission

    def get(self, mission_id: str) -> Mission:
        try:
            return self._missions[mission_id]
        except KeyError as exc:
            raise OrchestrationError(f"mission not found: {mission_id}") from exc

    def all(self) -> tuple[Mission, ...]:
        return tuple(sorted(self._missions.values(), key=lambda item: (item.sequence, item.mission_id)))

    def readiness(self, mission_id: str) -> tuple[bool, Optional[str]]:
        mission = self.get(mission_id)
        if mission.state != MissionState.QUEUED:
            return False, f"state:{mission.state.value}"
        acceptable = {MissionState.ACCEPTED}
        if mission.dependency_policy == "completed":
            acceptable.update({MissionState.COMPLETED, MissionState.QA_PENDING})
        for dependency_id in mission.dependencies:
            dependency = self._missions.get(dependency_id)
            if dependency is None:
                return False, f"dependency_missing:{dependency_id}"
            if dependency.state not in acceptable:
                return False, f"dependency_not_{mission.dependency_policy}:{dependency_id}:{dependency.state.value}"
        return True, None

    def transition(
        self,
        mission_id: str,
        new_state: MissionState | str,
        *,
        worker_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Mission:
        mission = self.get(mission_id)
        try:
            target = MissionState(new_state)
        except ValueError as exc:
            raise OrchestrationError(f"unknown mission state: {new_state}") from exc
        if target == mission.state:
            raise OrchestrationError(f"ambiguous no-op transition: {mission.state.value}")
        if target not in _TRANSITIONS[mission.state]:
            raise OrchestrationError(f"invalid mission transition: {mission.state.value} -> {target.value}")

        normalized_worker = str(worker_id).strip() if worker_id is not None else None
        if worker_id is not None and not normalized_worker:
            raise OrchestrationError("worker_id cannot be empty")

        if target == MissionState.ASSIGNED:
            ready, blocked_reason = self.readiness(mission_id)
            if not ready:
                raise OrchestrationError(f"mission is not dispatch-eligible: {blocked_reason}")
            if not normalized_worker:
                raise OrchestrationError("assignment requires worker_id")
            mission.assigned_worker = normalized_worker
            mission.implementer = normalized_worker
        elif target in {MissionState.RUNNING, MissionState.COMPLETED}:
            if not mission.assigned_worker:
                raise OrchestrationError("active transition requires an assigned worker")
            if normalized_worker and normalized_worker != mission.assigned_worker:
                raise OrchestrationError("worker does not own the mission assignment")
        elif target == MissionState.QA_PENDING:
            if not normalized_worker:
                raise OrchestrationError("QA transition requires worker_id")
            if normalized_worker == mission.implementer:
                raise OrchestrationError("implementer cannot satisfy independent QA")
            mission.qa_worker = normalized_worker
        elif target in {MissionState.ACCEPTED, MissionState.REJECTED}:
            if not mission.qa_worker:
                raise OrchestrationError("QA outcome requires an assigned QA worker")
            if normalized_worker and normalized_worker != mission.qa_worker:
                raise OrchestrationError("QA outcome worker does not own the QA assignment")
            if mission.qa_worker == mission.implementer:
                raise OrchestrationError("implementer cannot satisfy independent QA")

        if target == MissionState.BLOCKED:
            if not reason or not str(reason).strip():
                raise OrchestrationError("blocked transition requires a reason")
            mission.block_reason = str(reason).strip()
        elif target == MissionState.QUEUED:
            mission.assigned_worker = None
            mission.qa_worker = None
            mission.block_reason = None
        elif target == MissionState.COMPLETED:
            mission.assigned_worker = None

        mission.state = target
        return mission

    def save(self, path: Optional[os.PathLike[str] | str] = None) -> None:
        destination = Path(path) if path is not None else self.path
        if destination is None:
            raise OrchestrationError("mission store path is required")
        destination.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema_version": self.SCHEMA_VERSION,
            "missions": [mission.to_dict() for mission in self.all()],
        }
        temporary_name: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                json.dump(document, temporary, ensure_ascii=False, sort_keys=True, indent=2)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, destination)
        except (OSError, TypeError, ValueError) as exc:
            if temporary_name:
                try:
                    os.unlink(temporary_name)
                except OSError:
                    pass
            raise OrchestrationError(f"unable to persist mission store: {exc}") from exc
        self.path = destination

    @classmethod
    def load(cls, path: os.PathLike[str] | str) -> "MissionStore":
        source = Path(path)
        try:
            with source.open("r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise OrchestrationError(f"unable to load mission store: {exc}") from exc
        if not isinstance(document, Mapping) or document.get("schema_version") != cls.SCHEMA_VERSION:
            raise OrchestrationError("unsupported mission store schema")
        raw_missions = document.get("missions")
        if not isinstance(raw_missions, list):
            raise OrchestrationError("mission store requires a missions list")
        store = cls(source)
        try:
            for raw_mission in raw_missions:
                if not isinstance(raw_mission, Mapping):
                    raise OrchestrationError("invalid mission record")
                store.add(Mission.from_dict(raw_mission))
        except (TypeError, ValueError) as exc:
            if isinstance(exc, OrchestrationError):
                raise
            raise OrchestrationError(f"invalid mission store: {exc}") from exc
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
        if not self.worker_id.strip():
            raise OrchestrationError("worker requires worker_id")
        if not self.authorized_projects:
            raise OrchestrationError("worker requires at least one authorized project")


@dataclass(frozen=True)
class DispatchDecision:
    outcome: str
    mission_id: Optional[str] = None
    worker_id: Optional[str] = None
    reason: Optional[str] = None

    @property
    def is_dispatch(self) -> bool:
        return self.outcome == "dispatch"


@dataclass(frozen=True)
class _NormalizedWorker:
    worker_id: str
    authorized_projects: tuple[str, ...]
    available: bool
    healthy: bool
    active_mission_id: Optional[str]


def _priority_rank(value: Any) -> int:
    raw = str(value).strip().upper()
    if raw.startswith("P"):
        raw = raw[1:]
    try:
        return int(raw)
    except ValueError:
        return 999


def _normalize_workers(workers: Iterable[WorkerState | Mapping[str, Any]]) -> tuple[_NormalizedWorker, ...]:
    normalized = []
    for worker in workers:
        if isinstance(worker, Mapping):
            worker_id = str(worker.get("worker_id", worker.get("agent_id", ""))).strip()
            projects = worker.get("authorized_projects", worker.get("projects", ()))
            available = bool(worker.get("available", True))
            healthy = bool(worker.get("healthy", True))
            active = worker.get("active_mission_id", worker.get("active_wp_id"))
        else:
            worker_id = worker.worker_id.strip()
            projects = worker.authorized_projects
            available = worker.available
            healthy = worker.healthy
            active = worker.active_mission_id
        if not worker_id:
            raise OrchestrationError("worker requires worker_id")
        if isinstance(projects, str):
            projects = (projects,)
        normalized.append(
            _NormalizedWorker(
                worker_id=worker_id,
                authorized_projects=tuple(str(project).strip() for project in projects),
                available=available,
                healthy=healthy,
                active_mission_id=str(active).strip() if active else None,
            )
        )
    ids = [worker.worker_id for worker in normalized]
    if len(ids) != len(set(ids)):
        raise OrchestrationError("worker ids must be unique")
    return tuple(sorted(normalized, key=lambda worker: worker.worker_id))


def select_dispatch(
    store: MissionStore,
    workers: Iterable[WorkerState | Mapping[str, Any]],
) -> DispatchDecision:
    """Select one deterministic mission/worker pair without mutating the store."""
    eligible_missions = []
    for mission in store.all():
        ready, _ = store.readiness(mission.mission_id)
        if ready:
            eligible_missions.append(mission)
    eligible_missions.sort(key=lambda mission: (mission.priority, mission.sequence, mission.mission_id))

    if not eligible_missions:
        return DispatchDecision(outcome="idle", reason="no_eligible_work")

    normalized_workers = _normalize_workers(workers)
    for mission in eligible_missions:
        for worker in normalized_workers:
            if not worker.available or not worker.healthy or worker.active_mission_id:
                continue
            if mission.project_id not in worker.authorized_projects:
                continue
            return DispatchDecision(
                outcome="dispatch",
                mission_id=mission.mission_id,
                worker_id=worker.worker_id,
                reason="eligible_worker_selected",
            )

    return DispatchDecision(
        outcome="starved",
        reason="eligible_work_has_no_authorized_available_worker",
    )


def dispatch_next(
    store: MissionStore,
    workers: Iterable[WorkerState | Mapping[str, Any]],
) -> DispatchDecision:
    """Select and record one assignment, or return an explicit idle/starvation result."""
    decision = select_dispatch(store, workers)
    if decision.is_dispatch:
        store.transition(decision.mission_id or "", MissionState.ASSIGNED, worker_id=decision.worker_id)
    return decision


deterministic_dispatch = select_dispatch
