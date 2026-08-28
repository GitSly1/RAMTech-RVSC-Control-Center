from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable, Mapping, Sequence


class WorkerState(str, Enum):
    AVAILABLE = "available"
    ASSIGNED = "assigned"
    EXECUTING = "executing"
    EVIDENCE_DELIVERED = "evidence_delivered"
    QA_ACCEPTED = "qa_accepted"
    BLOCKED = "blocked"


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
class WorkerExecution:
    wp_id: str
    agent_id: str
    state: WorkerState
    attempt: int = 0
    evidence: tuple[str, ...] = ()
    failure_reason: str | None = None


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
    return replace(
        execution,
        state=WorkerState.EXECUTING,
        attempt=execution.attempt + 1,
        failure_reason=None,
    )


def deliver_evidence(execution: WorkerExecution, evidence: Sequence[str]) -> WorkerExecution:
    if execution.state is not WorkerState.EXECUTING:
        raise WorkerRuntimeError("evidence can only be delivered from executing")
    normalized = tuple(item for item in evidence if str(item).strip())
    if not normalized:
        raise WorkerRuntimeError("evidence bundle is empty")
    return replace(execution, state=WorkerState.EVIDENCE_DELIVERED, evidence=normalized)


def block_execution(execution: WorkerExecution, reason: str) -> WorkerExecution:
    if not reason.strip():
        raise WorkerRuntimeError("blocked execution requires a reason")
    return replace(execution, state=WorkerState.BLOCKED, failure_reason=reason)


def retry_allowed(execution: WorkerExecution, max_attempts: int) -> bool:
    return execution.state is WorkerState.BLOCKED and execution.attempt < max_attempts


def select_qa_agent(agents: Iterable[Agent], implementer_id: str, project: str) -> Agent:
    candidates = [
        agent
        for agent in agents
        if agent.worker_enabled
        and agent.qa_eligible
        and agent.agent_id != implementer_id
        and project in agent.projects
    ]
    if not candidates:
        raise WorkerRuntimeError("no independent QA agent available")
    return sorted(candidates, key=lambda item: item.agent_id)[0]


def dispatch_queue(work_packages: Mapping[str, WorkPackage]) -> tuple[WorkPackage, ...]:
    return tuple(sorted(work_packages.values(), key=lambda wp: (wp.priority, wp.wp_id)))
