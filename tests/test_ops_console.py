import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from controller.ops_console import read_events, record_event, system_state


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
        events = [{"timestamp": now.isoformat(), "status": "STALLED"}]
        state, _ = system_state(events, now=now)
        self.assertEqual(state, "STALLED")

    def test_old_activity_reports_idle(self):
        now = datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc)
        events = [{"timestamp": (now - timedelta(minutes=6)).isoformat(), "status": "WORKING"}]
        state, reason = system_state(events, now=now, idle_seconds=300)
        self.assertEqual(state, "IDLE")
        self.assertIn("No material event", reason)


if __name__ == "__main__":
    unittest.main()
