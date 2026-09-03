from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from controller.generic_qa_worker import execute_mission


class GenericQAWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-b", "qa-review"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "QA Fixture"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "qa@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "source.py"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "fixture"], cwd=self.root, check=True, capture_output=True)

    def tearDown(self):
        self.temporary.cleanup()

    def mission(self, command: list[str] | None = None):
        return {
            "run_id": "QA-RUN-001",
            "work_branch": "qa-review",
            "allowed_paths": ["source.py"],
            "validation_commands": [
                {
                    "name": "controlled validation",
                    "argv": command or [sys.executable, "-c", "from pathlib import Path; assert Path('source.py').read_text() == 'VALUE = 1\\n'"],
                }
            ],
        }

    def execute(self, mission=None, qa_eligible=True):
        return execute_mission(
            agent_id="QA-001",
            agent_name="Quinn",
            role="QA",
            qa_eligible=qa_eligible,
            mission=mission or self.mission(),
            repo_root=self.root,
        )

    def test_accepts_successful_validation_and_records_git_identity(self):
        result = self.execute()
        expected_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True, capture_output=True, check=True
        ).stdout.strip()

        self.assertTrue(result["success"])
        self.assertEqual(result["verdict"], "QA_ACCEPTED")
        self.assertEqual(result["reviewed_branch"], "qa-review")
        self.assertEqual(result["reviewed_commit_sha"], expected_sha)
        self.assertIn("authorization:qa_eligible", result["evidence"])
        self.assertIn("source_execution:isolated_copy", result["evidence"])

    def test_rejects_failed_validation(self):
        result = self.execute(self.mission([sys.executable, "-c", "raise SystemExit(4)"]))

        self.assertFalse(result["success"])
        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertEqual(result["validations"][0]["returncode"], 4)

    def test_validation_cannot_modify_reviewed_source(self):
        mission = self.mission([
            sys.executable,
            "-c",
            "from pathlib import Path; Path('source.py').write_text('CHANGED\\n')",
        ])

        result = self.execute(mission)

        self.assertTrue(result["success"])
        self.assertEqual((self.root / "source.py").read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_fails_closed_when_run_id_is_missing(self):
        mission = self.mission()
        mission.pop("run_id")

        result = self.execute(mission)

        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertIn("required_evidence:run_id:missing", result["evidence"])

    def test_fails_closed_when_review_file_is_missing(self):
        mission = self.mission()
        mission["allowed_paths"] = ["missing.py"]

        result = self.execute(mission)

        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertIn("required review evidence is missing", result["summary"])

    def test_fails_closed_for_wrong_branch(self):
        mission = self.mission()
        mission["work_branch"] = "another-branch"

        result = self.execute(mission)

        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertIn("reviewed branch mismatch", result["summary"])

    def test_rejects_unauthorized_agent(self):
        result = self.execute(qa_eligible=False)

        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertIn("authorization:denied", result["evidence"])

    def test_rejects_uncontrolled_executable(self):
        result = self.execute(self.mission(["sh", "-c", "true"]))

        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertIn("uncontrolled executable", result["summary"])

    def test_rejects_source_path_escape(self):
        mission = self.mission()
        mission["allowed_paths"] = ["../outside.py"]

        result = self.execute(mission)

        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertIn("escapes repository", result["summary"])


if __name__ == "__main__":
    unittest.main()
