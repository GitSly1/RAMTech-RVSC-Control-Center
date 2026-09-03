from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from controller.engineering_environment import ControlledEngineeringEnvironment, EngineeringEnvironmentError


class EngineeringEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        (self.repo / "docs").mkdir()
        (self.repo / "src").mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_write_and_read_are_limited_to_allowed_paths(self) -> None:
        env = ControlledEngineeringEnvironment(self.repo, allowed_paths=("docs/**",))
        evidence = env.write_text("docs/result.md", "verified\n")
        self.assertEqual(env.read_text("docs/result.md"), "verified\n")
        self.assertEqual(evidence.relative_path, "docs/result.md")
        self.assertEqual(evidence.size, len(b"verified\n"))
        self.assertEqual(len(evidence.sha256), 64)
        with self.assertRaises(EngineeringEnvironmentError):
            env.write_text("src/forbidden.py", "x = 1\n")

    def test_path_traversal_is_rejected(self) -> None:
        env = ControlledEngineeringEnvironment(self.repo, allowed_paths=("docs/**",))
        with self.assertRaises(EngineeringEnvironmentError):
            env.read_text("docs/../../outside.txt")

    def test_unapproved_executable_is_rejected(self) -> None:
        env = ControlledEngineeringEnvironment(self.repo, allowed_paths=("docs/**",), allowed_executables=("git",))
        with self.assertRaises(EngineeringEnvironmentError):
            env.run(("python", "-c", "print('no')"))

    def test_git_operations_are_observable(self) -> None:
        env = ControlledEngineeringEnvironment(self.repo, allowed_paths=("docs/**",), allowed_executables=("git",))
        env.write_text("docs/result.md", "evidence\n")
        status = env.git_status()
        self.assertEqual(status.returncode, 0)
        self.assertIn("docs/", status.stdout)

    def test_git_status_ignores_generated_python_cache_only(self) -> None:
        env = ControlledEngineeringEnvironment(self.repo, allowed_paths=("docs/**",), allowed_executables=("git",))
        cache = self.repo / "src" / "__pycache__"
        cache.mkdir()
        (cache / "module.cpython-312.pyc").write_bytes(b"generated")
        raw = env.git_raw_status()
        self.assertEqual(raw.returncode, 0)
        self.assertTrue(raw.stdout.strip())
        self.assertEqual(env.git_status().stdout, "")

        (self.repo / "src" / "real_change.py").write_text("x = 1\n", encoding="utf-8")
        material = env.git_status()
        self.assertIn("src/real_change.py", material.stdout)
        self.assertNotIn("__pycache__", material.stdout)

    def test_git_status_still_blocks_modified_tracked_file(self) -> None:
        tracked = self.repo / "docs" / "tracked.md"
        tracked.write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "add", "docs/tracked.md"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "baseline"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )
        tracked.write_text("changed\n", encoding="utf-8")
        env = ControlledEngineeringEnvironment(self.repo, allowed_paths=("docs/**",), allowed_executables=("git",))
        self.assertIn("docs/tracked.md", env.git_status().stdout)

    def test_search_is_scoped(self) -> None:
        (self.repo / "docs" / "allowed.md").write_text("golden evidence", encoding="utf-8")
        (self.repo / "src" / "hidden.py").write_text("golden evidence", encoding="utf-8")
        env = ControlledEngineeringEnvironment(self.repo, allowed_paths=("docs/**",))
        self.assertEqual(env.search_text("golden evidence"), ("docs/allowed.md",))

    def test_staging_rejects_out_of_scope_paths(self) -> None:
        env = ControlledEngineeringEnvironment(self.repo, allowed_paths=("docs/**",), allowed_executables=("git",))
        (self.repo / "src" / "forbidden.py").write_text("x = 1\n", encoding="utf-8")
        with self.assertRaises(EngineeringEnvironmentError):
            env.stage(("src/forbidden.py",))

    def test_commit_requires_explicit_identity_and_overrides_inherited_git_identity(self) -> None:
        env = ControlledEngineeringEnvironment(self.repo, allowed_paths=("docs/**",), allowed_executables=("git",))
        env.write_text("docs/result.md", "attributed\n")
        staged = env.stage(("docs/result.md",))
        self.assertEqual(staged.returncode, 0)

        with self.assertRaises(TypeError):
            env.commit("missing identity")

        inherited = {
            "GIT_AUTHOR_NAME": "DEV-001 Daniel",
            "GIT_AUTHOR_EMAIL": "dev-001@rvsc.local",
            "GIT_COMMITTER_NAME": "DEV-001 Daniel",
            "GIT_COMMITTER_EMAIL": "dev-001@rvsc.local",
        }
        with patch.dict(os.environ, inherited, clear=False):
            committed = env.commit(
                "OPS-001 attributable commit",
                author_name="OPS-001 Noah",
                author_email="ops-001@rvsc.local",
            )
        self.assertEqual(committed.returncode, 0, committed.stderr)

        identity = env.run(("git", "show", "-s", "--format=%an|%ae|%cn|%ce", "HEAD"))
        self.assertEqual(identity.returncode, 0)
        self.assertEqual(
            identity.stdout.strip(),
            "OPS-001 Noah|ops-001@rvsc.local|OPS-001 Noah|ops-001@rvsc.local",
        )


if __name__ == "__main__":
    unittest.main()
