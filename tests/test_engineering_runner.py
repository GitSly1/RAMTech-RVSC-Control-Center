from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from controller.adapters import WorkerRequest
from controller.engineering_environment import EngineeringEnvironmentError
from controller.engineering_runner import EngineeringMissionRunner, ValidationCommand


class EngineeringMissionRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "rvsc/TEST-001-bounded"], cwd=self.repo, check=True, capture_output=True)
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "seed.md").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/seed.md"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@rvsc.local", "commit", "-m", "seed"], cwd=self.repo, check=True, capture_output=True)
        self.request = WorkerRequest(
            agent_id="DEV-001",
            wp_id="TEST-001",
            project="SEMANTIQ",
            repository="GitSly1/RAMTech-SEMANTIQ",
            base_branch="main",
            work_branch="rvsc/TEST-001-bounded",
            objective="bounded qualification",
            allowed_paths=("docs/**",),
            acceptance_criteria=("validation passes", "commit evidence returned"),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_preflight_requires_exact_branch_and_clean_repo(self) -> None:
        runner = EngineeringMissionRunner(self.request, self.repo, validations=())
        evidence = runner.preflight()
        self.assertIn("branch:rvsc/TEST-001-bounded", evidence)
        self.assertIn("repo_clean:true", evidence)
        (self.repo / "docs" / "dirty.md").write_text("dirty", encoding="utf-8")
        with self.assertRaises(EngineeringEnvironmentError):
            runner.preflight()

    def test_validation_and_commit_produce_attributable_evidence(self) -> None:
        runner = EngineeringMissionRunner(
            self.request,
            self.repo,
            validations=(ValidationCommand("PYTHON", ("python", "-c", "print('pass')")),),
        )
        runner.preflight()
        runner.environment.write_text("docs/result.md", "qualified\n")
        self.assertIn("validation:PYTHON:returncode:0", runner.validate())
        evidence = runner.evidence_after_change(("docs/result.md",))
        self.assertIn("file:docs/result.md", evidence)
        self.assertIn("diff_present:true", evidence)
        commit_evidence = runner.commit(("docs/result.md",), "TEST-001: bounded change")
        self.assertTrue(commit_evidence[0].startswith("commit:"))
        status = runner.environment.git_status()
        self.assertEqual(status.stdout.strip(), "")

    def test_branch_mismatch_fails_closed(self) -> None:
        wrong = WorkerRequest(
            agent_id=self.request.agent_id,
            wp_id=self.request.wp_id,
            project=self.request.project,
            repository=self.request.repository,
            base_branch=self.request.base_branch,
            work_branch="rvsc/WRONG",
            objective=self.request.objective,
            allowed_paths=self.request.allowed_paths,
            acceptance_criteria=self.request.acceptance_criteria,
        )
        runner = EngineeringMissionRunner(wrong, self.repo, validations=())
        with self.assertRaises(EngineeringEnvironmentError):
            runner.preflight()


if __name__ == "__main__":
    unittest.main()
