from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from controller.generic_qa_worker import _repo_root, execute_mission


class GenericQAWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.qa_host = self.base / "qa-host"
        self.semantiq = self.base / "semantiq"
        self.origin = self.base / "semantiq-origin.git"
        for root in (self.qa_host, self.semantiq):
            root.mkdir()
            (root / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Fixture"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "add", "source.py"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "fixture"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "rvsc/SEM-123"], cwd=self.semantiq, check=True, capture_output=True)
        (self.semantiq / "source.py").write_text("VALUE = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "source.py"], cwd=self.semantiq, check=True)
        subprocess.run(["git", "commit", "-m", "engineering"], cwd=self.semantiq, check=True, capture_output=True)
        self.commit = self.git_value("rev-parse", "HEAD", cwd=self.semantiq)
        subprocess.run(["git", "clone", "--bare", str(self.semantiq), str(self.origin)], check=True, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", str(self.origin)], cwd=self.semantiq, check=True)

    def tearDown(self):
        self.temporary.cleanup()

    def git_value(self, *args: str, cwd: Path) -> str:
        return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True).stdout.strip()

    def mission(self) -> dict:
        return {
            "run_id": "QA-SEM-001",
            "project": "semantiq",
            "repository": "GitSly1/RAMTech-SEMANTIQ",
            "engineering_project": "semantiq",
            "engineering_repository": "GitSly1/RAMTech-SEMANTIQ",
            "work_branch": "rvsc/SEM-123",
            "engineering_commit_sha": self.commit,
            "reviewed_commit_sha": self.commit,
            "allowed_paths": ["source.py"],
            "validation_commands": [{"name": "content", "argv": [sys.executable, "-c", "from pathlib import Path; assert Path('source.py').read_text() == 'VALUE = 2\\n'"]}],
        }

    def execute(self, mission: dict | None = None):
        with patch.dict(os.environ, {"RVSC_SEMANTIQ_REPO": str(self.semantiq)}, clear=False):
            return execute_mission(agent_id="QA-001", agent_name="Quinn", role="QA", qa_eligible=True, mission=mission or self.mission())

    def test_semantiq_target_resolution_ignores_qa_host_origin(self):
        with patch.dict(os.environ, {"RVSC_SEMANTIQ_REPO": str(self.semantiq)}, clear=False):
            self.assertEqual(_repo_root(self.mission()), self.semantiq.resolve())

    def test_acquires_exact_pushed_semantiq_branch_and_commit(self):
        result = self.execute()
        self.assertTrue(result["success"])
        self.assertEqual(result["reviewed_branch"], "rvsc/SEM-123")
        self.assertEqual(result["reviewed_commit_sha"], self.commit)
        self.assertIn("target_acquisition:origin_fetch", result["evidence"])
        self.assertIn("target_verification:branch_tip_matches_commit", result["evidence"])
        self.assertIn("source_execution:isolated_copy", result["evidence"])

    def test_exact_reviewed_commit_remains_mandatory_for_engineering_handoff(self):
        mission = self.mission()
        mission.pop("reviewed_commit_sha")
        result = self.execute(mission)
        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertIn("reviewed_commit_sha is required", result["summary"])

    def test_repository_project_mismatch_fails_closed(self):
        mission = self.mission()
        mission["engineering_repository"] = "GitSly1/RAMTech-RVSC-Control-Center"
        result = self.execute(mission)
        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertIn("does not match QA project", result["summary"])

    def test_unauthorized_agent_is_rejected(self):
        result = execute_mission(agent_id="DEV-001", agent_name="Daniel", role="Development", qa_eligible=False, mission=self.mission(), repo_root=self.semantiq)
        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertIn("authorization:denied", result["evidence"])


if __name__ == "__main__":
    unittest.main()
