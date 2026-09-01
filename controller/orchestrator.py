from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


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
    """Normalize a worker-runtime signal into an orchestration event.

    The adapter intentionally uses the signal contract rather than worker-specific
    implementation details so cloned Golden Agents can emit the same lifecycle.
    """
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
