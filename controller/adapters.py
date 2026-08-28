from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


@dataclass(frozen=True)
class WorkerRequest:
    agent_id: str
    wp_id: str
    project: str
    repository: str
    base_branch: str
    work_branch: str
    objective: str
    allowed_paths: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]


@dataclass(frozen=True)
class WorkerResult:
    success: bool
    summary: str
    evidence: tuple[str, ...] = ()
    retryable: bool = False


class WorkerAdapter(Protocol):
    name: str

    def execute(self, request: WorkerRequest) -> WorkerResult:
        ...


class AdapterRegistry:
    def __init__(self, adapters: Mapping[str, WorkerAdapter] | None = None) -> None:
        self._adapters = dict(adapters or {})

    def register(self, adapter: WorkerAdapter) -> None:
        if not getattr(adapter, "name", ""):
            raise ValueError("adapter requires a name")
        self._adapters[adapter.name] = adapter

    def resolve(self, name: str) -> WorkerAdapter:
        try:
            return self._adapters[name]
        except KeyError as exc:
            raise ValueError(f"worker adapter not configured: {name}") from exc


class DryRunAdapter:
    """Safe qualification adapter used before external credentials are enabled."""

    name = "dry_run"

    def execute(self, request: WorkerRequest) -> WorkerResult:
        return WorkerResult(
            success=True,
            summary=f"qualified dispatch for {request.agent_id} on {request.wp_id}",
            evidence=(f"branch:{request.work_branch}", "adapter:dry_run"),
        )
