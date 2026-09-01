import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from controller.ops_console import read_events, record_event, system_state, telemetry_snapshot


class OpsConsoleTests(unittest.TestCase):
    def test_record_and_read_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "activity.jsonl"
            record_event("DEV-001", "CHECKPOINT", "WORKING", wp_id="SEM-DANIEL-004", detail="tests_passed", evidence=("test:pass",), path=path)
            events = read_events(path)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["actor"], "DEV-001")
        self.assertEqual(events[0]["wp_id"], "SEM-DANIEL-004")
        self.assertEqual(events[0]["evidence"], ["test:pass"])

    def test_stalled_event_is_visible_even_when_recent(self):
        now = datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc)
        events = [{"timestamp": now.isoformat(), "action": "STALL", "status": "STALLED"}]
        state, _ = system_state(events, now=now)
        self.assertEqual(state, "STALLED")

    def test_old_activity_reports_idle(self):
        now = datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc)
        events = [{"timestamp": (now - timedelta(minutes=6)).isoformat(), "action": "CHECKPOINT", "status": "WORKING"}]
        state, reason = system_state(events, now=now, idle_seconds=300)
        self.assertEqual(state, "IDLE")
        self.assertIn("No material event", reason)

    def test_heartbeat_does_not_reset_material_progress_age(self):
        now = datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc)
        checkpoint_time = now - timedelta(minutes=11)
        events = [
            {"timestamp": checkpoint_time.isoformat(), "actor": "DEV-001", "action": "CHECKPOINT", "status": "WORKING", "detail": "implementation_applied"},
            {"timestamp": (now - timedelta(seconds=3)).isoformat(), "actor": "DEV-001", "action": "HEARTBEAT", "status": "ONLINE"},
        ]
        snapshot = telemetry_snapshot(events, now=now)
        self.assertEqual(snapshot["heartbeat_age"], 3)
        self.assertEqual(snapshot["checkpoint_age"], 660)
        self.assertEqual(snapshot["material_age"], 660)

    def test_fresh_heartbeat_with_stale_checkpoint_reports_stalled(self):
        now = datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc)
        events = [
            {"timestamp": (now - timedelta(minutes=11)).isoformat(), "actor": "DEV-001", "action": "CHECKPOINT", "status": "WORKING", "detail": "implementation_applied"},
            {"timestamp": (now - timedelta(seconds=3)).isoformat(), "actor": "DEV-001", "action": "HEARTBEAT", "status": "ONLINE"},
        ]
        state, reason = system_state(events, now=now, idle_seconds=300, checkpoint_stall_seconds=600)
        self.assertEqual(state, "STALLED")
        self.assertIn("Heartbeat fresh", reason)
        self.assertIn("implementation_applied", reason)

    def test_heartbeat_without_material_progress_is_idle(self):
        now = datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc)
        events = [{"timestamp": (now - timedelta(seconds=2)).isoformat(), "action": "HEARTBEAT", "status": "ONLINE"}]
        state, reason = system_state(events, now=now)
        self.assertEqual(state, "IDLE")
        self.assertIn("no material progress", reason.lower())


if __name__ == "__main__":
    unittest.main()
