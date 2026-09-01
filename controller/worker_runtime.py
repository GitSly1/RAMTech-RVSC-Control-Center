from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, Mapping, Sequence


class WorkerState(str, Enum):
    AVAILABLE = "available"
    ASSIGNED = "assigned"
    EXECUTING = "executing"
    EVIDENCE_DELIVERED = "evidence_delivered"
    QA_ACCEPTED = "qa_accepted"
    BLOCKED = "blocked"


class WorkerSignalType(str, Enum):
    HEARTBEAT = "heartbeat"
    CHECKPOINT = "checkpoint"
    COMPLETION = "completion"
    STALL = "stall"
    BLOCKED = "blocked"


class WatchdogAction(str, Enum):
    HEALTHY = "healthy"
    STALL = "stall"
    RETRY = "retry"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class Agent:
    agent_id: str
    name: str
    capabilities: frozenset[str]
    projects: frozenset[str]
    worker_enabled: bool = True
    qa_eligible: bool = False


@dataclass(frozen=True)
class WorkPackage:
    wp_id: str
    project: str
    required_capabilities: frozenset[str]
    branch: str
    allowed_paths: tuple[str, ...]
    priority: int = 999


@dataclass(frozen=True)
class WorkerSignal:
    signal_type: WorkerSignalType
    wp_id: str
    agent_id: str
    checkpoint: str | None = None
    evidence: tuple[str, ...] = ()
    failure_reason: str | None = None


@dataclass(frozen=True)
class WorkerExecution:
    wp_id: str
    agent_id: str
    state: WorkerState
    attempt: int = 0
    evidence: tuple[str, ...] = ()
    failure_reason: str | None = None
    last_checkpoint: str | None = None


@dataclass(frozen=True)
class WorkerHealth:
    reachable: bool
    ready: bool
    busy: bool
    active_mission: str | None = None
    last_checkpoint: str | None = None
    last_activity: str | None = None
    last_heartbeat_at: str | None = None
    last_checkpoint_at: str | None = None
    mission_acknowledged_at: str | None = None


@dataclass(frozen=True)
class WatchdogDecision:
    action: WatchdogAction
    reason: str


class WorkerRuntimeError(ValueError):
    pass


def eligible_agents(agents: Iterable[Agent], wp: WorkPackage) -> tuple[Agent, ...]:
    matches = []
    for agent in agents:
        if not agent.worker_enabled:
            continue
        if wp.project not in agent.projects:
            continue
        if not wp.required_capabilities.issubset(agent.capabilities):
            continue
        matches.append(agent)
    return tuple(sorted(matches, key=lambda item: item.agent_id))


def select_agent(agents: Iterable[Agent], wp: WorkPackage) -> Agent:
    matches = eligible_agents(agents, wp)
    if not matches:
        raise WorkerRuntimeError(f"no eligible agent for {wp.wp_id}")
    return matches[0]


def start_execution(execution: WorkerExecution) -> WorkerExecution:
    if execution.state not in {WorkerState.ASSIGNED, WorkerState.BLOCKED}:
        raise WorkerRuntimeError(f"cannot start from {execution.state.value}")
    return replace(execution, state=WorkerState.EXECUTING, attempt=execution.attempt + 1, failure_reason=None)


def heartbeat_signal(execution: WorkerExecution) -> WorkerSignal:
    return WorkerSignal(signal_type=WorkerSignalType.HEARTBEAT, wp_id=execution.wp_id, agent_id=execution.agent_id, checkpoint=execution.last_checkpoint, evidence=execution.evidence, failure_reason=execution.failure_reason)


def checkpoint_execution(execution: WorkerExecution, checkpoint: str, evidence: Sequence[str] = ()) -> tuple[WorkerExecution, WorkerSignal]:
    if execution.state is not WorkerState.EXECUTING:
        raise WorkerRuntimeError("checkpoint requires executing state")
    normalized_checkpoint = checkpoint.strip()
    if not normalized_checkpoint:
        raise WorkerRuntimeError("checkpoint name is required")
    normalized_evidence = tuple(str(item).strip() for item in evidence if str(item).strip())
    updated = replace(execution, last_checkpoint=normalized_checkpoint, evidence=execution.evidence + normalized_evidence)
    signal = WorkerSignal(signal_type=WorkerSignalType.CHECKPOINT, wp_id=updated.wp_id, agent_id=updated.agent_id, checkpoint=normalized_checkpoint, evidence=normalized_evidence)
    return updated, signal


def deliver_evidence(execution: WorkerExecution, evidence: Sequence[str]) -> WorkerExecution:
    if execution.state is not WorkerState.EXECUTING:
        raise WorkerRuntimeError("evidence can only be delivered from executing")
    normalized = tuple(item for item in evidence if str(item).strip())
    if not normalized:
        raise WorkerRuntimeError("evidence bundle is empty")
    return replace(execution, state=WorkerState.EVIDENCE_DELIVERED, evidence=normalized)


def completion_signal(execution: WorkerExecution) -> WorkerSignal:
    if execution.state not in {WorkerState.EVIDENCE_DELIVERED, WorkerState.QA_ACCEPTED}:
        raise WorkerRuntimeError("completion signal requires delivered or QA-accepted evidence")
    return WorkerSignal(signal_type=WorkerSignalType.COMPLETION, wp_id=execution.wp_id, agent_id=execution.agent_id, checkpoint=execution.last_checkpoint, evidence=execution.evidence)


def block_execution(execution: WorkerExecution, reason: str) -> WorkerExecution:
    if not reason.strip():
        raise WorkerRuntimeError("blocked execution requires a reason")
    return replace(execution, state=WorkerState.BLOCKED, failure_reason=reason)


def block_signal(execution: WorkerExecution, stalled: bool = False) -> WorkerSignal:
    if execution.state is not WorkerState.BLOCKED:
        raise WorkerRuntimeError("block signal requires blocked state")
    return WorkerSignal(signal_type=WorkerSignalType.STALL if stalled else WorkerSignalType.BLOCKED, wp_id=execution.wp_id, agent_id=execution.agent_id, checkpoint=execution.last_checkpoint, evidence=execution.evidence, failure_reason=execution.failure_reason)


def retry_allowed(execution: WorkerExecution, max_attempts: int) -> bool:
    return execution.state is WorkerState.BLOCKED and execution.attempt < max_attempts


def _parse_activity(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def watchdog_decision(execution: WorkerExecution, health: WorkerHealth, *, now: datetime, checkpoint_timeout_seconds: int, max_attempts: int) -> WatchdogDecision:
    """Evaluate liveness and mission progress independently.

    last_heartbeat_at/last_activity indicate worker liveness. last_checkpoint_at
    indicates mission progress. A fresh heartbeat must never conceal a stale
    checkpoint. last_activity remains a compatibility fallback for older workers.
    """
    if checkpoint_timeout_seconds <= 0:
        raise WorkerRuntimeError("checkpoint timeout must be positive")
    if not health.reachable:
        action = WatchdogAction.RETRY if execution.attempt < max_attempts else WatchdogAction.ESCALATE
        return WatchdogDecision(action, "worker unreachable")
    if execution.state is WorkerState.BLOCKED:
        action = WatchdogAction.RETRY if retry_allowed(execution, max_attempts) else WatchdogAction.ESCALATE
        return WatchdogDecision(action, execution.failure_reason or "worker blocked")
    if execution.state is not WorkerState.EXECUTING:
        return WatchdogDecision(WatchdogAction.HEALTHY, f"execution state {execution.state.value} requires no checkpoint watchdog")
    if not health.busy or health.active_mission != execution.wp_id:
        action = WatchdogAction.RETRY if execution.attempt < max_attempts else WatchdogAction.ESCALATE
        return WatchdogDecision(action, "worker did not acknowledge active mission")
    checkpoint_activity = _parse_activity(health.last_checkpoint_at or health.last_activity)
    if checkpoint_activity is None:
        return WatchdogDecision(WatchdogAction.STALL, "worker has no observable checkpoint timestamp")
    normalized_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    age = (normalized_now.astimezone(timezone.utc) - checkpoint_activity).total_seconds()
    if age > checkpoint_timeout_seconds:
        return WatchdogDecision(WatchdogAction.STALL, f"checkpoint stale for {int(age)} seconds")
    return WatchdogDecision(WatchdogAction.HEALTHY, f"checkpoint progress observed {int(max(age, 0))} seconds ago")


def select_qa_agent(agents: Iterable[Agent], implementer_id: str, project: str) -> Agent:
    candidates = [agent for agent in agents if agent.worker_enabled and agent.qa_eligible and agent.agent_id != implementer_id and project in agent.projects]
    if not candidates:
        raise WorkerRuntimeError("no independent QA agent available")
    return sorted(candidates, key=lambda item: item.agent_id)[0]


def dispatch_queue(work_packages: Mapping[str, WorkPackage]) -> tuple[WorkPackage, ...]:
    return tuple(sorted(work_packages.values(), key=lambda wp: (wp.priority, wp.wp_id)))
