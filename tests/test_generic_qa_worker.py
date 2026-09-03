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
        self.base = Path(self.temporary.name)
        self.root = self.base / "workspace"
        self.root.mkdir()
        (self.root / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-b", "qa-review"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "QA Fixture"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "qa@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "source.py"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "fixture"], cwd=self.root, check=True, capture_output=True)

    def tearDown(self):
        self.temporary.cleanup()

    def git_value(self, *args: str, cwd: Path | None = None) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.root,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

    def create_remote_target(self) -> tuple[str, str, Path]:
        qa_commit = self.git_value("rev-parse", "HEAD")
        subprocess.run(["git", "checkout", "-b", "engineering-review"], cwd=self.root, check=True, capture_output=True)
        (self.root / "source.py").write_text("VALUE = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "source.py"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "engineering target"], cwd=self.root, check=True, capture_output=True)
        engineering_commit = self.git_value("rev-parse", "HEAD")
        subprocess.run(["git", "checkout", "qa-review"], cwd=self.root, check=True, capture_output=True)
        origin = self.base / "origin.git"
        subprocess.run(["git", "clone", "--bare", str(self.root), str(origin)], check=True, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=self.root, check=True)
        return qa_commit, engineering_commit, origin

    def mission(self, command: list[str] | None = None):
        return {
            "run_id": "QA-RUN-001",
            "work_branch": "qa-review",
            "allowed_paths": ["source.py"],
            "validation_commands": [
                {
                    "name": "controlled validation",
                    "argv": command or [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; assert Path('source.py').read_text() == 'VALUE = 1\\n'",
                    ],
                }
            ],
        }

    def automatic_mission(self, commit_sha: str, branch: str = "engineering-review"):
        mission = self.mission([
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('source.py').read_text() == 'VALUE = 2\\n'",
        ])
        mission["work_branch"] = branch
        mission["reviewed_commit_sha"] = commit_sha
        return mission

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
        expected_sha = self.git_value("rev-parse", "HEAD")

        self.assertTrue(result["success"])
        self.assertEqual(result["verdict"], "QA_ACCEPTED")
        self.assertEqual(result["reviewed_branch"], "qa-review")
        self.assertEqual(result["reviewed_commit_sha"], expected_sha)
        self.assertIn("authorization:qa_eligible", result["evidence"])
        self.assertIn("target_acquisition:existing_workspace", result["evidence"])
        self.assertIn("source_execution:isolated_copy", result["evidence"])

    def test_acquires_exact_remote_target_without_switching_or_modifying_workspace(self):
        _, engineering_commit, origin = self.create_remote_target()
        workspace_branch = self.git_value("branch", "--show-current")
        workspace_commit = self.git_value("rev-parse", "HEAD")
        remote_commit = self.git_value("rev-parse", "refs/heads/engineering-review", cwd=origin)

        result = self.execute(self.automatic_mission(engineering_commit))

        self.assertTrue(result["success"])
        self.assertEqual(result["reviewed_branch"], "engineering-review")
        self.assertEqual(result["reviewed_commit_sha"], engineering_commit)
        self.assertIn("target_acquisition:origin_fetch", result["evidence"])
        self.assertIn("target_verification:branch_tip_matches_commit", result["evidence"])
        self.assertEqual(self.git_value("branch", "--show-current"), workspace_branch)
        self.assertEqual(self.git_value("rev-parse", "HEAD"), workspace_commit)
        self.assertEqual((self.root / "source.py").read_text(encoding="utf-8"), "VALUE = 1\n")
        self.assertEqual(self.git_value("rev-parse", "refs/heads/engineering-review", cwd=origin), remote_commit)

    def test_fails_closed_when_requested_remote_branch_is_unavailable(self):
        _, engineering_commit, _ = self.create_remote_target()

        result = self.execute(self.automatic_mission(engineering_commit, branch="missing-branch"))

        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertIn("requested branch is unavailable", result["summary"])

    def test_fails_closed_when_requested_branch_is_invalid(self):
        _, engineering_commit, _ = self.create_remote_target()

        result = self.execute(self.automatic_mission(engineering_commit, branch="invalid..branch"))

        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertIn("requested branch is invalid or unavailable", result["summary"])

    def test_fails_closed_when_requested_commit_is_unavailable(self):
        self.create_remote_target()

        result = self.execute(self.automatic_mission("0" * 40))

        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertIn("requested commit SHA is invalid or unavailable", result["summary"])

    def test_fails_closed_for_branch_commit_mismatch(self):
        qa_commit, _, _ = self.create_remote_target()

        result = self.execute(self.automatic_mission(qa_commit))

        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertIn("requested branch/commit mismatch", result["summary"])

    def test_fails_closed_for_malformed_requested_commit(self):
        self.create_remote_target()

        result = self.execute(self.automatic_mission("not-a-commit"))

        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertIn("requested commit SHA is invalid or unavailable", result["summary"])

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

    def test_fails_closed_for_wrong_branch_in_manual_mode(self):
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
