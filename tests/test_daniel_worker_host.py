from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from controller import daniel_worker_host as host


class DanielWorkerHostCoreTests(unittest.TestCase):
    def test_load_daniel_core_reads_nonempty_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "daniel.md"
            path.write_text("DANIEL CORE MARKER", encoding="utf-8")
            with patch.object(host, "DANIEL_CORE_PATH", path):
                self.assertEqual(host._load_daniel_core(), "DANIEL CORE MARKER")

    def test_load_max_core_reads_nonempty_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "max.md"
            path.write_text("MAX CORE MARKER", encoding="utf-8")
            with patch.object(host, "MAX_CORE_PATH", path):
                self.assertEqual(host._load_max_platinum_core(), "MAX CORE MARKER")

    def test_missing_core_fails_closed(self) -> None:
        missing = Path(tempfile.gettempdir()) / "rvsc-core-does-not-exist.md"
        if missing.exists():
            missing.unlink()
        with patch.object(host, "MAX_CORE_PATH", missing):
            with self.assertRaisesRegex(RuntimeError, "unable to load Max Platinum Engineering Core"):
                host._load_max_platinum_core()

    def test_empty_core_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "empty.md"
            path.write_text("   \n", encoding="utf-8")
            with patch.object(host, "DANIEL_CORE_PATH", path):
                with self.assertRaisesRegex(RuntimeError, "Daniel Golden Core is empty"):
                    host._load_daniel_core()

    def test_engineering_prompt_injects_both_cores(self) -> None:
        mission = {
            "agent_id": "DEV-001",
            "wp_id": "SEM-DANIEL-001",
            "objective": "Implement a bounded test feature",
            "allowed_paths": list(host.SEM_DANIEL_ALLOWED),
        }
        source_files = {path: f"baseline:{path}" for path in host.SEM_DANIEL_ALLOWED}
        with patch.object(host, "_load_daniel_core", return_value="DANIEL UNIQUE CORE MARKER"), patch.object(
            host, "_load_max_platinum_core", return_value="MAX UNIQUE CORE MARKER"
        ):
            prompt = host._engineering_prompt(mission, source_files)

        self.assertIn("DANIEL GOLDEN AGENT CORE:\nDANIEL UNIQUE CORE MARKER", prompt)
        self.assertIn("MAX PLATINUM ENGINEERING CORE:\nMAX UNIQUE CORE MARKER", prompt)
        self.assertIn("Mission scope", prompt)
        self.assertIn("Never expose credentials", prompt)
        self.assertIn("Implement a bounded test feature", prompt)

    def test_engineering_prompt_does_not_include_api_key(self) -> None:
        sentinel_key = "sk-proj-THIS-MUST-NOT-APPEAR-IN-PROMPT"
        mission = {"agent_id": "DEV-001", "wp_id": "SEM-DANIEL-001", "objective": "safe task"}
        with patch.dict(os.environ, {"OPENAI_API_KEY": sentinel_key}, clear=False), patch.object(
            host, "_load_daniel_core", return_value="DANIEL CORE"
        ), patch.object(host, "_load_max_platinum_core", return_value="MAX CORE"):
            prompt = host._engineering_prompt(mission, {})
        self.assertNotIn(sentinel_key, prompt)


if __name__ == "__main__":
    unittest.main()
