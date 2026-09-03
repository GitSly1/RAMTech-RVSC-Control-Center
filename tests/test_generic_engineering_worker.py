from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, call, patch

from controller.engineering_environment import EngineeringEnvironmentError
from controller.generic_engineering_worker import _configure_git_identity, _git_identity, _repo_root, _validations, _worker_request


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

    def test_git_identity_is_derived_from_executing_agent(self):
        self.assertEqual(
            _git_identity("OPS-001", "Noah"),
            ("OPS-001 Noah", "ops-001@rvsc.local"),
        )
        self.assertEqual(
            _git_identity("DEV-001", "Daniel"),
            ("DEV-001 Daniel", "dev-001@rvsc.local"),
        )

    def test_noah_git_identity_does_not_reuse_daniel_attribution(self):
        self.assertNotEqual(
            _git_identity("OPS-001", "Noah"),
            _git_identity("DEV-001", "Daniel"),
        )

    def test_git_identity_requires_agent_id(self):
        with self.assertRaisesRegex(ValueError, "agent_id is required"):
            _git_identity(" ", "Noah")

    def test_git_identity_is_agent_specific_and_repository_local(self):
        environment = Mock()
        environment.run.return_value = Mock(returncode=0, stdout="", stderr="")

        _configure_git_identity(environment, "OPS-001", "Noah")

        self.assertEqual(environment.run.call_args_list, [
            call(("git", "config", "--local", "user.name", "OPS-001 Noah")),
            call(("git", "config", "--local", "user.email", "ops-001@rvsc.local")),
        ])

    def test_git_identity_configuration_failure_stops_execution(self):
        environment = Mock()
        environment.run.return_value = Mock(returncode=1, stdout="", stderr="configuration failed")

        with self.assertRaisesRegex(EngineeringEnvironmentError, "configuration failed"):
            _configure_git_identity(environment, "OPS-001", "Noah")

        environment.run.assert_called_once_with(("git", "config", "--local", "user.name", "OPS-001 Noah"))


if __name__ == "__main__":
    unittest.main()
