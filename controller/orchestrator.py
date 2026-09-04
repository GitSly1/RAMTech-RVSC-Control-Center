from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
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
    return all(payload.get(key) == value for key, value in expected.items())


def worker_signal_event(signal: Any) -> Event:
    signal_type = getattr(signal, "signal_type", None)
    raw_type = getattr(signal_type, "value", signal_type)
    wp_id = str(getattr(signal, "wp_id", "")).strip()
    agent_id = str(getattr(signal, "agent_id", "")).strip()
    if not isinstance(raw_type, str) or not raw_type.strip():
        raise OrchestrationError("worker signal requires signal_type")
    if not wp_id or not agent_id:
        raise OrchestrationError("worker signal requires wp_id and agent_id")
    return Event("worker.%s" % raw_type.strip(), {"wp_id": wp_id, "agent_id": agent_id, "checkpoint": getattr(signal, "checkpoint", None), "evidence": tuple(getattr(signal, "evidence", ()) or ()), "failure_reason": getattr(signal, "failure_reason", None)})


def resolve_trigger(config: Mapping[str, Any], event: Event) -> Mapping[str, Any]:
    matches = [trigger for trigger in config.get("triggers", []) if trigger.get("event") == event.event_type and _matches(trigger.get("when", {}), event.payload)]
    if not matches:
        raise OrchestrationError("no trigger matched event: %s" % event.event_type)
    if len(matches) > 1:
        raise OrchestrationError("ambiguous trigger match: %s" % ", ".join(str(value.get("id")) for value in matches))
    return matches[0]


def build_execution_plan(config: Mapping[str, Any], event: Event) -> ExecutionPlan:
    trigger = resolve_trigger(config, event)
    route_name = trigger.get("route")
    route = config.get("routes", {}).get(route_name)
    if not route:
        raise OrchestrationError("route not found: %s" % route_name)
    actions = tuple(route.get("actions", []))
    if not actions:
        raise OrchestrationError("route has no actions: %s" % route_name)
    return ExecutionPlan(str(trigger.get("id")), str(route_name), actions)


def _priority_rank(value: Any) -> int:
    raw = str(value).strip().upper().removeprefix("P")
    try:
        return int(raw)
    except ValueError:
        return 999


def dispatch_order(projects: Mapping[str, Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(key for key, _ in sorted(projects.items(), key=lambda item: (_priority_rank(item[1].get("priority", "P999")), item[0])))


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
    MissionState.REJECTED: {MissionState.ACCEPTED, MissionState.BLOCKED},
}


def _canonical(checkpoint: Any, evidence: Any) -> str:
    return json.dumps({"checkpoint": checkpoint, "evidence": evidence}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _required_string(contract: Mapping[str, Any], name: str) -> str:
    value = contract.get(name)
    if not isinstance(value, str) or not value.strip():
        raise OrchestrationError("mission contract requires non-empty %s" % name)
    if "\x00" in value:
        raise OrchestrationError("mission contract %s contains an unsafe null character" % name)
    return value.strip()


def _required_string_list(contract: Mapping[str, Any], name: str) -> list[str]:
    value = contract.get(name)
    if not isinstance(value, list) or not value:
        raise OrchestrationError("mission contract requires a non-empty %s list" % name)
    normalized = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or "\x00" in item:
            raise OrchestrationError("mission contract %s entries must be non-empty strings" % name)
        normalized.append(item.strip())
    if len(set(normalized)) != len(normalized):
        raise OrchestrationError("mission contract %s entries must be unique" % name)
    return normalized


def validate_mission_contract(contract: Mapping[str, Any], supported_projects: Iterable[str]) -> dict[str, Any]:
    if not isinstance(contract, Mapping):
        raise OrchestrationError("mission contract must be an object")
    data = copy.deepcopy(dict(contract))
    mission_id = _required_string(data, "wp_id")
    project = _required_string(data, "project").lower()
    for alias, expected in (("mission_id", mission_id), ("id", mission_id), ("project_id", project)):
        if alias in data and str(data[alias]).strip().lower() != expected.lower():
            raise OrchestrationError("ambiguous mission identity or project definition")
    authorized = {str(value).strip().lower() for value in supported_projects if str(value).strip()}
    if project not in authorized:
        raise OrchestrationError("unsupported project mission: %s" % project)
    data["wp_id"] = mission_id
    data["project"] = project
    for name in ("objective", "repository", "work_branch", "base_branch", "agent_id"):
        data[name] = _required_string(data, name)
    data["acceptance_criteria"] = _required_string_list(data, "acceptance_criteria")
    data["allowed_paths"] = _required_string_list(data, "allowed_paths")
    for raw_path in data["allowed_paths"]:
        normalized = raw_path.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or normalized.startswith("~") or ":" in path.parts[0]:
            raise OrchestrationError("mission contract contains an unsafe allowed path: %s" % raw_path)
    commands = data.get("validation_commands")
    if not isinstance(commands, list) or not commands:
        raise OrchestrationError("mission contract requires validation_commands")
    normalized_commands = []
    for command in commands:
        if not isinstance(command, Mapping) or not isinstance(command.get("argv"), list) or not command["argv"]:
            raise OrchestrationError("each validation command requires a non-empty argv list")
        if any(not isinstance(argument, str) or not argument.strip() or "\x00" in argument for argument in command["argv"]):
            raise OrchestrationError("validation command argv entries must be non-empty strings")
        normalized_command = copy.deepcopy(dict(command))
        normalized_command["argv"] = [argument.strip() for argument in command["argv"]]
        normalized_commands.append(normalized_command)
    data["validation_commands"] = normalized_commands
    dependencies = data.get("dependencies", [])
    if not isinstance(dependencies, list) or any(not isinstance(value, str) or not value.strip() for value in dependencies):
        raise OrchestrationError("mission dependencies must be a list of non-empty strings")
    dependencies = [value.strip() for value in dependencies]
    if len(set(dependencies)) != len(dependencies) or mission_id in dependencies:
        raise OrchestrationError("mission dependencies must be unique and cannot include the mission")
    data["dependencies"] = dependencies
    return data


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
    rework_attempts: int = 0
    qa_outcome_identity: Optional[str] = None
    qa_evidence: Any = None
    rejection_reason: Optional[str] = None
    parent_mission_id: Optional[str] = None
    root_mission_id: Optional[str] = None

    def __post_init__(self) -> None:
        self.mission_id = str(self.mission_id).strip()
        self.project_id = str(self.project_id).strip().lower()
        try:
            self.state = MissionState(self.state)
        except ValueError as exc:
            raise OrchestrationError("unknown mission state: %s" % self.state) from exc
        self.dependencies = tuple(str(value).strip() for value in self.dependencies)
        self.dependency_policy = str(self.dependency_policy).strip().lower()
        self.priority = _priority_rank(self.priority)
        self.metadata = dict(self.metadata)
        self.recovery_attempts = max(0, int(self.recovery_attempts))
        self.rework_attempts = max(0, int(self.rework_attempts))
        if not self.mission_id or not self.project_id:
            raise OrchestrationError("mission requires mission_id and project_id")
        if any(not value for value in self.dependencies) or len(set(self.dependencies)) != len(self.dependencies):
            raise OrchestrationError("mission dependencies must be non-empty and unique")
        if self.mission_id in self.dependencies:
            raise OrchestrationError("mission cannot depend on itself")
        if self.dependency_policy not in {"accepted", "completed"}:
            raise OrchestrationError("dependency_policy must be accepted or completed")
        if self.material_progress_at is not None:
            self.material_progress_at = float(self.material_progress_at)
        if self.parent_mission_id is not None:
            self.parent_mission_id = str(self.parent_mission_id)
        if self.root_mission_id is not None:
            self.root_mission_id = str(self.root_mission_id)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        data["dependencies"] = list(self.dependencies)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Mission":
        return cls(**dict(data))


@dataclass(frozen=True)
class QAOutcomeResult:
    outcome: str
    mission_id: str
    root_mission_id: str
    corrective_mission_id: Optional[str] = None
    rework_attempts: int = 0
    idempotent: bool = False
    reason: Optional[str] = None


class MissionStore:
    SCHEMA_VERSION = 3

    def __init__(self, path: Optional[os.PathLike[str] | str] = None) -> None:
        self.path = Path(path) if path is not None else None
        self._missions: dict[str, Mission] = {}
        self._next_sequence = 0

    @classmethod
    def load_or_create(cls, path: os.PathLike[str] | str) -> "MissionStore":
        destination = Path(path)
        if destination.exists():
            return cls.load(destination)
        store = cls(destination)
        store.save()
        return store

    def _persist(self) -> None:
        if self.path is not None:
            self.save()

    def add(self, mission: Mission) -> Mission:
        if not isinstance(mission, Mission):
            raise OrchestrationError("mission store accepts Mission instances")
        if mission.mission_id in self._missions:
            raise OrchestrationError("duplicate mission: %s" % mission.mission_id)
        if mission.sequence < 0:
            mission.sequence = self._next_sequence
        self._next_sequence = max(self._next_sequence, mission.sequence + 1)
        self._missions[mission.mission_id] = mission
        self._persist()
        return mission

    def add_contract(self, contract: Mapping[str, Any], *, supported_projects: Iterable[str]) -> Mission:
        data = validate_mission_contract(contract, supported_projects)
        mission = Mission(data["wp_id"], data["project"], dependencies=tuple(data.get("dependencies", ())), dependency_policy=str(data.get("dependency_policy", "accepted")), priority=data.get("priority", 999), metadata={"contract": data, "requires_independent_qa": True, "ingestion": "validated_cli"})
        return self.add(mission)

    def dispatch_contract(self, mission_id: str, worker_id: str, *, supported_projects: Iterable[str]) -> dict[str, Any]:
        mission = self.get(mission_id)
        stored = mission.metadata.get("contract")
        if not isinstance(stored, Mapping):
            raise OrchestrationError("queued mission has no validated engineering contract")
        contract = validate_mission_contract(stored, supported_projects)
        if contract["wp_id"] != mission.mission_id:
            raise OrchestrationError("stored contract wp_id does not match durable mission identity")
        if contract["project"] != mission.project_id:
            raise OrchestrationError("stored contract project does not match durable mission project")
        if contract["agent_id"] != str(worker_id).strip():
            raise OrchestrationError("stored contract agent_id does not match selected worker")
        return contract

    def get(self, mission_id: str) -> Mission:
        try:
            return self._missions[mission_id]
        except KeyError as exc:
            raise OrchestrationError("mission not found: %s" % mission_id) from exc

    def all(self) -> tuple[Mission, ...]:
        return tuple(sorted(self._missions.values(), key=lambda mission: (mission.sequence, mission.mission_id)))

    list_missions = all

    def readiness(self, mission_id: str) -> tuple[bool, Optional[str]]:
        mission = self.get(mission_id)
        if mission.state != MissionState.QUEUED:
            return False, "state:%s" % mission.state.value
        acceptable = {MissionState.ACCEPTED}
        if mission.dependency_policy == "completed":
            acceptable.update({MissionState.COMPLETED, MissionState.QA_PENDING})
        for dependency_id in mission.dependencies:
            dependency = self._missions.get(dependency_id)
            if dependency is None:
                return False, "dependency_missing:%s" % dependency_id
            if dependency.state not in acceptable:
                return False, "dependency_not_%s:%s:%s" % (mission.dependency_policy, dependency_id, dependency.state.value)
        return True, None

    def record_progress(self, mission_id: str, *, timestamp: float, checkpoint: Any, evidence: Any) -> Mission:
        mission = self.get(mission_id)
        identity = _canonical(checkpoint, evidence)
        if identity == mission.material_identity:
            return mission
        timestamp = float(timestamp)
        if mission.material_progress_at is not None and timestamp < mission.material_progress_at:
            raise OrchestrationError("material progress timestamp cannot move backwards")
        mission.material_progress_at = timestamp
        mission.material_checkpoint = None if checkpoint is None else str(checkpoint)
        mission.material_evidence = evidence
        mission.material_identity = identity
        mission.recovery_attempts = 0
        mission.recovery_state = "NONE"
        mission.recovery_outcome = None
        self._persist()
        return mission

    def record_recovery(self, mission_id: str, attempts: int, state: str, outcome: Optional[str]) -> Mission:
        mission = self.get(mission_id)
        if int(attempts) < mission.recovery_attempts:
            raise OrchestrationError("recovery attempts cannot decrease")
        mission.recovery_attempts = int(attempts)
        mission.recovery_state = str(state)
        mission.recovery_outcome = None if outcome is None else str(outcome)
        self._persist()
        return mission

    def transition(self, mission_id: str, new_state: MissionState | str, *, worker_id: Optional[str] = None, reason: Optional[str] = None, evidence: Optional[Mapping[str, Any]] = None, timestamp: Optional[float] = None) -> Mission:
        mission = self.get(mission_id)
        try:
            target = MissionState(new_state)
        except ValueError as exc:
            raise OrchestrationError("unknown mission state: %s" % new_state) from exc
        if target == mission.state:
            raise OrchestrationError("ambiguous no-op transition: %s" % mission.state.value)
        if target not in _TRANSITIONS[mission.state]:
            raise OrchestrationError("invalid mission transition: %s -> %s" % (mission.state.value, target.value))
        worker = str(worker_id).strip() if worker_id is not None else None
        if worker_id is not None and not worker:
            raise OrchestrationError("worker_id cannot be empty")
        if target == MissionState.ASSIGNED:
            ready, blocked = self.readiness(mission_id)
            if not ready:
                raise OrchestrationError("mission is not dispatch-eligible: %s" % blocked)
            if not worker:
                raise OrchestrationError("assignment requires worker_id")
            excluded = {str(value) for value in mission.metadata.get("excluded_worker_ids", ())}
            if worker in excluded:
                raise OrchestrationError("worker is excluded from corrective engineering assignment")
            mission.assigned_worker = mission.implementer = worker
        elif target in {MissionState.RUNNING, MissionState.COMPLETED}:
            if not mission.assigned_worker:
                raise OrchestrationError("active transition requires an assigned worker")
            if worker and worker != mission.assigned_worker:
                raise OrchestrationError("worker does not own the mission assignment")
        elif target == MissionState.QA_PENDING:
            if not worker:
                raise OrchestrationError("QA transition requires worker_id")
            if worker == mission.implementer:
                raise OrchestrationError("implementer cannot satisfy independent QA")
            mission.qa_worker = worker
        elif target in {MissionState.ACCEPTED, MissionState.REJECTED}:
            if not mission.qa_worker:
                raise OrchestrationError("QA outcome requires an assigned QA worker")
            if worker and worker != mission.qa_worker:
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
        if timestamp is not None:
            identity = _canonical("lifecycle:%s" % target.value, dict(evidence or {}))
            if identity != mission.material_identity:
                mission.material_progress_at = float(timestamp)
                mission.material_checkpoint = "lifecycle:%s" % target.value
                mission.material_evidence = dict(evidence or {})
                mission.material_identity = identity
                mission.recovery_attempts = 0
                mission.recovery_state = "NONE"
                mission.recovery_outcome = None
        self._persist()
        return mission

    def process_qa_outcome(self, mission_id: str, outcome: str, *, qa_worker: str, evidence: Optional[Mapping[str, Any]] = None, timestamp: Optional[float] = None, max_rework_attempts: int = 2) -> QAOutcomeResult:
        mission = self.get(mission_id)
        normalized = str(outcome).strip().upper()
        if normalized not in {"QA_ACCEPTED", "QA_REJECTED"}:
            raise OrchestrationError("unsupported QA outcome: %s" % outcome)
        qa_worker = str(qa_worker).strip()
        if not qa_worker:
            raise OrchestrationError("QA outcome requires qa_worker")
        if qa_worker == mission.implementer:
            raise OrchestrationError("implementer cannot satisfy independent QA")
        evidence_data = dict(evidence or {})
        identity = _canonical(normalized, {"qa_worker": qa_worker, "evidence": evidence_data})
        root_id = mission.root_mission_id or mission.mission_id
        root = self.get(root_id)
        if mission.qa_outcome_identity == identity:
            return QAOutcomeResult(str(mission.metadata.get("last_qa_action", normalized)), mission_id, root_id, mission.metadata.get("corrective_mission_id"), root.rework_attempts, True, mission.block_reason)
        if mission.qa_outcome_identity is not None:
            raise OrchestrationError("mission already has a different QA outcome")
        if mission.state == MissionState.COMPLETED:
            mission = self.transition(mission_id, MissionState.QA_PENDING, worker_id=qa_worker, evidence=evidence_data, timestamp=timestamp)
        if mission.state != MissionState.QA_PENDING:
            raise OrchestrationError("QA outcome requires completed or qa_pending mission")
        if mission.qa_worker != qa_worker:
            raise OrchestrationError("QA outcome worker does not own the QA assignment")
        mission.qa_outcome_identity = identity
        mission.qa_evidence = evidence_data
        if normalized == "QA_ACCEPTED":
            self.transition(mission_id, MissionState.ACCEPTED, worker_id=qa_worker, evidence=evidence_data, timestamp=timestamp)
            mission.metadata["last_qa_action"] = "ACCEPTED"
            if root_id != mission_id:
                if root.state != MissionState.REJECTED:
                    raise OrchestrationError("corrective acceptance requires a rejected root mission")
                root.metadata["corrected_by"] = mission_id
                root.metadata["corrective_qa_evidence"] = evidence_data
                self.transition(root_id, MissionState.ACCEPTED, worker_id=root.qa_worker, evidence={"event": "corrective_work_accepted", "corrective_mission_id": mission_id, "qa_evidence": evidence_data}, timestamp=timestamp)
            self._persist()
            return QAOutcomeResult("ACCEPTED", mission_id, root_id, rework_attempts=root.rework_attempts)
        reason = str(evidence_data.get("rejection_reason") or evidence_data.get("reason") or "QA rejected")
        mission.rejection_reason = reason
        self.transition(mission_id, MissionState.REJECTED, worker_id=qa_worker, evidence=evidence_data, timestamp=timestamp)
        limit = max(0, int(max_rework_attempts))
        root.metadata["max_rework_attempts"] = limit
        root.metadata.setdefault("rejection_history", []).append({"mission_id": mission_id, "qa_worker": qa_worker, "reason": reason, "evidence": evidence_data, "attempt": root.rework_attempts})
        if root.rework_attempts >= limit:
            root.metadata["last_qa_action"] = "REWORK_EXHAUSTED"
            root.metadata["exception"] = {"type": "REWORK_EXHAUSTED", "mission_id": mission_id, "attempts": root.rework_attempts, "maximum": limit, "reason": reason}
            if root.state == MissionState.REJECTED:
                self.transition(root_id, MissionState.BLOCKED, reason="rework threshold exhausted", evidence=root.metadata["exception"], timestamp=timestamp)
            self._persist()
            return QAOutcomeResult("REWORK_EXHAUSTED", mission_id, root_id, rework_attempts=root.rework_attempts, reason="rework threshold exhausted")
        attempt = root.rework_attempts + 1
        corrective_id = "%s::rework:%d" % (root_id, attempt)
        root.rework_attempts = attempt
        root.metadata["last_qa_action"] = "REWORK_QUEUED"
        root.metadata["corrective_mission_id"] = corrective_id
        mission.metadata["last_qa_action"] = "REWORK_QUEUED"
        mission.metadata["corrective_mission_id"] = corrective_id
        if corrective_id not in self._missions:
            originating = root.metadata.get("contract")
            if not isinstance(originating, Mapping):
                raise OrchestrationError("cannot create corrective mission without originating validated contract")
            corrective_contract = copy.deepcopy(dict(originating))
            corrective_contract["wp_id"] = corrective_id
            corrective_contract["dependencies"] = []
            corrective_contract["objective"] = "Correct independent QA rejection for %s: %s\n\nOriginal objective:\n%s" % (root_id, reason, originating.get("objective", ""))
            corrective_contract = validate_mission_contract(corrective_contract, (root.project_id,))
            corrective = Mission(corrective_id, root.project_id, priority=root.priority, metadata={"contract": corrective_contract, "corrective_work": True, "attempt_number": attempt, "originating_mission_id": root_id, "parent_mission_id": mission_id, "qa_rejection_evidence": evidence_data, "rejection_reason": reason, "reviewed_commit": evidence_data.get("reviewed_commit") or evidence_data.get("commit"), "reviewed_branch": evidence_data.get("reviewed_branch") or evidence_data.get("branch"), "excluded_worker_ids": [qa_worker], "requires_independent_qa": True, "ingestion": "validated_corrective"}, parent_mission_id=mission_id, root_mission_id=root_id)
            self.add(corrective)
        self._persist()
        return QAOutcomeResult("REWORK_QUEUED", mission_id, root_id, corrective_id, attempt)

    def save(self, path: Optional[os.PathLike[str] | str] = None) -> None:
        destination = Path(path) if path is not None else self.path
        if destination is None:
            raise OrchestrationError("mission store path is required")
        destination.parent.mkdir(parents=True, exist_ok=True)
        document = {"schema_version": self.SCHEMA_VERSION, "missions": [mission.to_dict() for mission in self.all()]}
        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, prefix=".%s." % destination.name, suffix=".tmp", delete=False) as handle:
                temporary_name = handle.name
                json.dump(document, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, destination)
        except (OSError, TypeError, ValueError) as exc:
            if temporary_name:
                try:
                    os.unlink(temporary_name)
                except OSError:
                    pass
            raise OrchestrationError("unable to persist mission store: %s" % exc) from exc
        self.path = destination

    @classmethod
    def load(cls, path: os.PathLike[str] | str) -> "MissionStore":
        source = Path(path)
        try:
            with source.open("r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise OrchestrationError("unable to load mission store: %s" % exc) from exc
        if not isinstance(document, Mapping) or document.get("schema_version") not in {1, 2, cls.SCHEMA_VERSION}:
            raise OrchestrationError("unsupported mission store schema")
        raw = document.get("missions")
        if not isinstance(raw, list):
            raise OrchestrationError("mission store requires a missions list")
        store = cls()
        for item in raw:
            if not isinstance(item, Mapping):
                raise OrchestrationError("invalid mission record")
            store.add(Mission.from_dict(item))
        store.path = source
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


def select_dispatch(store: MissionStore, workers: Iterable[WorkerState | Mapping[str, Any]]) -> DispatchDecision:
    missions = [mission for mission in store.all() if store.readiness(mission.mission_id)[0]]
    missions.sort(key=lambda mission: (mission.priority, mission.sequence, mission.mission_id))
    if not missions:
        return DispatchDecision("idle", reason="no_eligible_work")
    normalized = []
    for worker in workers:
        if isinstance(worker, Mapping):
            worker_id = str(worker.get("worker_id", worker.get("agent_id", ""))).strip()
            projects = worker.get("authorized_projects", worker.get("projects", ()))
            available = bool(worker.get("available", True))
            healthy = bool(worker.get("healthy", True))
            active = worker.get("active_mission_id")
        else:
            worker_id, projects, available, healthy, active = worker.worker_id.strip(), worker.authorized_projects, worker.available, worker.healthy, worker.active_mission_id
        if not worker_id:
            raise OrchestrationError("worker requires worker_id")
        if isinstance(projects, str):
            projects = (projects,)
        normalized.append((worker_id, tuple(str(project).strip().lower() for project in projects), available, healthy, active))
    ids = [worker[0] for worker in normalized]
    if len(ids) != len(set(ids)):
        raise OrchestrationError("worker ids must be unique")
    for mission in missions:
        excluded = {str(value) for value in mission.metadata.get("excluded_worker_ids", ())}
        contract = mission.metadata.get("contract")
        authorized_worker = str(contract.get("agent_id", "")).strip() if isinstance(contract, Mapping) else None
        for worker_id, projects, available, healthy, active in sorted(normalized):
            if authorized_worker and worker_id != authorized_worker:
                continue
            if worker_id not in excluded and available and healthy and not active and mission.project_id in projects:
                return DispatchDecision("dispatch", mission.mission_id, worker_id, "eligible_worker_selected")
    return DispatchDecision("starved", reason="eligible_work_has_no_authorized_available_worker")


def dispatch_next(store: MissionStore, workers: Iterable[WorkerState | Mapping[str, Any]]) -> DispatchDecision:
    decision = select_dispatch(store, workers)
    if decision.is_dispatch:
        store.transition(decision.mission_id or "", MissionState.ASSIGNED, worker_id=decision.worker_id)
    return decision


deterministic_dispatch = select_dispatch
