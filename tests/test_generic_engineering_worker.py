from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from controller.generic_engineering_worker import _repo_root, _validations, _worker_request


class GenericEngineeringWorkerTests(unittest.TestCase):
    def test_worker_request_is_mission_driven(self):
        request = _worker_request({
            "agent_id": "OPS-001",
            "wp_id": "RVSC-025C-PROOF",
            "project": "rvsc",
            "repository": "RAMTech-RVSC-Control-Center",
            "base_branch": "rvsc/RVSC-025C-generic-mission-runner",
            "work_branch": "rvsc/RVSC-025C-noah-proof",
            "objective": "prove generic controlled engineering",
            "allowed_paths": ["tests/noah_proof.txt"],
            "acceptance_criteria": ["controlled change committed"],
        })
        self.assertEqual(request.agent_id, "OPS-001")
        self.assertEqual(request.wp_id, "RVSC-025C-PROOF")
        self.assertEqual(request.allowed_paths, ("tests/noah_proof.txt",))

    def test_validations_require_explicit_commands(self):
        with self.assertRaises(ValueError):
            _validations({})

    def test_validations_reject_empty_argv(self):
        with self.assertRaises(ValueError):
            _validations({"validation_commands": [{"name": "empty", "argv": []}]})

    def test_validations_allow_python_and_git(self):
        checks = _validations({"validation_commands": [
            {"name": "unit", "argv": ["python", "-m", "unittest", "discover"]},
            {"name": "status", "argv": ["git", "status", "--short"]},
        ]})
        self.assertEqual([item.name for item in checks], ["unit", "status"])

    def test_validations_reject_uncontrolled_executable(self):
        with self.assertRaises(ValueError):
            _validations({"validation_commands": [{"name": "bad", "argv": ["powershell", "-Command", "echo bad"]}]})

    def test_repo_root_uses_controlled_project_mapping(self):
        with patch.dict(os.environ, {"RVSC_RVSC_REPO": r"C:\controlled\rvsc"}, clear=False):
            root = _repo_root({"project": "rvsc"})
        self.assertTrue(str(root).lower().endswith("controlled\\rvsc"))

    def test_repo_root_rejects_unknown_project(self):
        with self.assertRaises(ValueError):
            _repo_root({"project": "unknown"})


if __name__ == "__main__":
    unittest.main()
