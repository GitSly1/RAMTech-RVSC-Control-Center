from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from controller.engineering_environment import EngineeringEnvironmentError
from controller.generic_engineering_worker import _configure_git_identity, _git_identity, _repo_root, _validations, _worker_request


class GenericEngineeringWorkerTests(unittest.TestCase):
    def test_worker_request_is_mission_driven(self):
        request = _worker_request({"agent_id": "DEV-001", "wp_id": "SEM-123", "project": "semantiq", "repository": "GitSly1/RAMTech-SEMANTIQ", "base_branch": "main", "work_branch": "rvsc/SEM-123", "objective": "change", "allowed_paths": ["source.py"], "acceptance_criteria": ["works"]})
        self.assertEqual(request.agent_id, "DEV-001")
        self.assertEqual(request.wp_id, "SEM-123")
        self.assertEqual(request.allowed_paths, ("source.py",))

    def test_semantiq_uses_controlled_semantiq_repository(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"RVSC_SEMANTIQ_REPO": temporary}, clear=False):
            self.assertEqual(_repo_root({"project": "semantiq"}), Path(temporary).resolve())

    def test_rvsc_mapping_remains_supported(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"RVSC_RVSC_REPO": temporary}, clear=False):
            self.assertEqual(_repo_root({"project": "rvsc"}), Path(temporary).resolve())

    def test_unknown_project_fails_closed(self):
        with self.assertRaises(ValueError):
            _repo_root({"project": "unknown"})

    def test_validations_reject_uncontrolled_executable(self):
        with self.assertRaises(ValueError):
            _validations({"validation_commands": [{"name": "bad", "argv": ["sh", "-c", "true"]}]})

    def test_git_identity_is_agent_specific(self):
        self.assertEqual(_git_identity("DEV-001", "Daniel"), ("DEV-001 Daniel", "dev-001@rvsc.local"))

    def test_git_identity_configuration_is_repository_local(self):
        environment = Mock()
        environment.run.return_value = Mock(returncode=0, stdout="", stderr="")
        _configure_git_identity(environment, "DEV-001", "Daniel")
        self.assertEqual(environment.run.call_args_list, [call(("git", "config", "--local", "user.name", "DEV-001 Daniel")), call(("git", "config", "--local", "user.email", "dev-001@rvsc.local"))])

    def test_git_identity_failure_stops_execution(self):
        environment = Mock()
        environment.run.return_value = Mock(returncode=1, stdout="", stderr="failed")
        with self.assertRaises(EngineeringEnvironmentError):
            _configure_git_identity(environment, "DEV-001", "Daniel")


if __name__ == "__main__":
    unittest.main()
