from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVENT_LOG = Path(os.environ.get("RVSC_EVENT_LOG", ".rvsc/activity.jsonl"))

LIVENESS_ACTIONS = {"HEARTBEAT"}
MATERIAL_ACTIONS = {"ACK", "CHECKPOINT", "COMPLETION", "BLOCK", "STALL", "RETRY", "ESCALATE", "QA", "DISPATCH"}
MATERIAL_STATUSES = {"ACKNOWLEDGED", "WORKING", "CHECKPOINT", "COMPLETED", "BLOCKED", "STALLED", "FAILED", "ESCALATE", "RETRYING", "QA"}
TERMINAL_ALERT_STATUSES = {"BLOCKED", "STALLED", "FAILED", "ESCALATE"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class OpsEvent:
    timestamp: str
    actor: str
    action: str
    status: str
    wp_id: str | None = None
    detail: str = ""
    evidence: tuple[str, ...] = ()


def record_event(actor: str, action: str, status: str, *, wp_id: str | None = None, detail: str = "", evidence: tuple[str, ...] = (), path: Path = EVENT_LOG) -> OpsEvent:
    event = OpsEvent(utc_now(), actor, action, status, wp_id, detail, tuple(evidence))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(asdict(event), separators=(",", ":")) + "\n")
    return event


def read_events(path: Path = EVENT_LOG, limit: int = 30) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def _parse_timestamp(event: dict[str, Any]) -> datetime | None:
    raw = str(event.get("timestamp", ""))
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _age_seconds(event: dict[str, Any], current: datetime) -> int | None:
    stamp = _parse_timestamp(event)
    if stamp is None:
        return None
    return max(0, int((current - stamp).total_seconds()))


def _is_heartbeat(event: dict[str, Any]) -> bool:
    return str(event.get("action", "")).upper() in LIVENESS_ACTIONS


def _is_material(event: dict[str, Any]) -> bool:
    action = str(event.get("action", "")).upper()
    status = str(event.get("status", "")).upper()
    return not _is_heartbeat(event) and (action in MATERIAL_ACTIONS or status in MATERIAL_STATUSES)


def telemetry_snapshot(events: list[dict[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    heartbeat = next((event for event in reversed(events) if _is_heartbeat(event)), None)
    material = next((event for event in reversed(events) if _is_material(event)), None)
    checkpoint = next((event for event in reversed(events) if str(event.get("action", "")).upper() == "CHECKPOINT"), None)
    return {
        "heartbeat": heartbeat,
        "heartbeat_age": _age_seconds(heartbeat, current) if heartbeat else None,
        "material": material,
        "material_age": _age_seconds(material, current) if material else None,
        "checkpoint": checkpoint,
        "checkpoint_age": _age_seconds(checkpoint, current) if checkpoint else None,
    }


def system_state(events: list[dict[str, Any]], *, now: datetime | None = None, idle_seconds: int = 300, checkpoint_stall_seconds: int | None = None) -> tuple[str, str]:
    if not events:
        return "IDLE", "No recorded RVSC activity"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    snapshot = telemetry_snapshot(events, now=current)
    material = snapshot["material"]
    heartbeat = snapshot["heartbeat"]

    if material is None:
        if heartbeat is not None:
            return "IDLE", f"Heartbeat seen {snapshot['heartbeat_age']}s ago; no material progress recorded"
        return "IDLE", "No material RVSC activity recorded"

    material_age = snapshot["material_age"]
    if material_age is None:
        return "UNKNOWN", "Latest material event has invalid timestamp"

    status = str(material.get("status", "")).upper()
    if status in TERMINAL_ALERT_STATUSES:
        return status, f"Latest material event {material_age}s ago"

    stall_after = checkpoint_stall_seconds if checkpoint_stall_seconds is not None else idle_seconds
    if heartbeat is not None and snapshot["heartbeat_age"] is not None and snapshot["heartbeat_age"] <= idle_seconds:
        checkpoint = snapshot["checkpoint"]
        checkpoint_age = snapshot["checkpoint_age"]
        if checkpoint is not None and checkpoint_age is not None and checkpoint_age > stall_after:
            checkpoint_name = str(checkpoint.get("detail") or checkpoint.get("status") or "checkpoint")
            return "STALLED", f"Heartbeat fresh ({snapshot['heartbeat_age']}s); checkpoint '{checkpoint_name}' stale for {checkpoint_age}s"
        if checkpoint is None and material_age > stall_after:
            return "STALLED", f"Heartbeat fresh ({snapshot['heartbeat_age']}s); no checkpoint progress for {material_age}s"

    if material_age > idle_seconds:
        return "IDLE", f"No material event for {material_age}s"
    return "ACTIVE", f"Last material event {material_age}s ago"


def render(events: list[dict[str, Any]]) -> str:
    state, reason = system_state(events)
    snapshot = telemetry_snapshot(events)
    heartbeat_age = snapshot["heartbeat_age"]
    checkpoint_age = snapshot["checkpoint_age"]
    checkpoint = snapshot["checkpoint"]
    checkpoint_name = str(checkpoint.get("detail") or checkpoint.get("status") or "-") if checkpoint else "-"
    rows = [
        "=" * 78,
        "RVSC LIVE OPERATIONS CONSOLE",
        f"Updated: {utc_now()}   System: {state}   {reason}",
        f"Heartbeat age: {heartbeat_age if heartbeat_age is not None else '-'}s   Checkpoint: {checkpoint_name}   Checkpoint age: {checkpoint_age if checkpoint_age is not None else '-'}s",
        "=" * 78,
        "TIME       ACTOR        ACTION           STATUS       WORK PACKAGE / DETAIL",
        "-" * 78,
    ]
    for event in events[-20:]:
        timestamp = str(event.get("timestamp", ""))
        clock = timestamp[11:19] if len(timestamp) >= 19 else "--:--:--"
        actor = str(event.get("actor", "?"))[:12]
        action = str(event.get("action", "?"))[:16]
        status = str(event.get("status", "?"))[:12]
        wp_id = str(event.get("wp_id") or "-")
        detail = str(event.get("detail") or "")
        rows.append(f"{clock:<10} {actor:<12} {action:<16} {status:<12} {wp_id} {detail}".rstrip())
    rows.append("-" * 78)
    if not events:
        rows.append("No events yet. Controller/worker actions will appear here when recorded.")
    return "\n".join(rows)


def watch(path: Path = EVENT_LOG, interval: float = 1.0) -> None:
    while True:
        print("\033[2J\033[H" + render(read_events(path)), flush=True)
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="RVSC live operational activity console")
    parser.add_argument("--log", type=Path, default=EVENT_LOG)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    if args.once:
        print(render(read_events(args.log)))
    else:
        watch(args.log, args.interval)


if __name__ == "__main__":
    main()
