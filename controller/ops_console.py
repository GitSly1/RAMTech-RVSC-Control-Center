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


def system_state(events: list[dict[str, Any]], *, now: datetime | None = None, idle_seconds: int = 300) -> tuple[str, str]:
    if not events:
        return "IDLE", "No recorded RVSC activity"
    latest = events[-1]
    raw = str(latest.get("timestamp", ""))
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return "UNKNOWN", "Latest event has invalid timestamp"
    current = now or datetime.now(timezone.utc)
    age = max(0, int((current - stamp).total_seconds()))
    status = str(latest.get("status", "")).upper()
    if status in {"BLOCKED", "STALLED", "FAILED", "ESCALATE"}:
        return status, f"Latest material event {age}s ago"
    if age > idle_seconds:
        return "IDLE", f"No material event for {age}s"
    return "ACTIVE", f"Last material event {age}s ago"


def render(events: list[dict[str, Any]]) -> str:
    state, reason = system_state(events)
    rows = [
        "=" * 78,
        "RVSC LIVE OPERATIONS CONSOLE",
        f"Updated: {utc_now()}   System: {state}   {reason}",
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
