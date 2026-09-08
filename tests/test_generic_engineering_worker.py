from __future__ import annotations

import os
import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from controller.engineering_environment import EngineeringEnvironmentError
from controller.engineering_runner import EngineeringValidationError
from controller.generic_engineering_worker import _budget_context_files, _bounded_context_text, _configure_git_identity, _git_identity, _ollama_call, _ollama_proposal_schema, _read_context_files, _repo_root, _validations, _worker_request, execute_mission


class GenericEngineeringWorkerTests(unittest.TestCase):
    def test_ollama_proposal_schema_requires_exact_engineering_contract(self):
        allowed_paths = ("controller/a.py", "tests/test_a.py")
        schema = _ollama_proposal_schema(allowed_paths)

        self.assertEqual(schema["type"], "object")
        self.assertEqual(
            set(schema["required"]),
            {"files", "commit_message", "engineering_summary"},
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            set(schema["properties"]),
            {"files", "commit_message", "engineering_summary"},
        )

        files_schema = schema["properties"]["files"]
        self.assertEqual(files_schema["type"], "object")
        self.assertEqual(
            set(files_schema["properties"]),
            set(allowed_paths),
        )
        self.assertEqual(
            files_schema["required"],
            list(allowed_paths),
        )
        self.assertFalse(files_schema["additionalProperties"])

        for path in allowed_paths:
            self.assertEqual(
                files_schema["properties"][path],
                {"type": "string"},
            )

        self.assertEqual(
            schema["properties"]["commit_message"],
            {"type": "string"},
        )
        self.assertEqual(
            schema["properties"]["engineering_summary"],
            {"type": "string"},
        )

        with self.assertRaises(ValueError):
            _ollama_proposal_schema(())

    @patch("controller.generic_engineering_worker.urllib.request.urlopen")
    def test_ollama_call_sends_structured_proposal_schema(self, urlopen):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"response":"{}"}'
        urlopen.return_value = response

        allowed_paths = ("controller/a.py",)
        _ollama_call("bounded mission", allowed_paths)

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))

        self.assertEqual(
            payload["format"],
            _ollama_proposal_schema(allowed_paths),
        )
        self.assertEqual(
            set(payload["format"]["properties"]["files"]["properties"]),
            set(allowed_paths),
        )
        self.assertFalse(
            payload["format"]["properties"]["files"]["additionalProperties"]
        )
        self.assertNotEqual(payload["format"], "json")
        self.assertEqual(payload["prompt"], "bounded mission")
        self.assertFalse(payload["stream"])

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

    @patch("controller.generic_engineering_worker._provider_call")
    @patch("controller.generic_engineering_worker._prepare_branch")
    @patch("controller.generic_engineering_worker._configure_git_identity")
    @patch("controller.generic_engineering_worker.EngineeringMissionRunner")
    def test_validation_failure_rolls_back_workspace(
        self,
        runner_type,
        configure_identity,
        prepare_branch,
        provider_call,
    ):
        environment = Mock()
        environment.read_text.return_value = "baseline\n"

        runner = Mock()
        runner.environment = environment
        runner.preflight.return_value = ("repo_clean:true",)
        runner.evidence_after_change.return_value = ("diff_present:true",)
        runner.validate.side_effect = EngineeringValidationError(
            "validation failed [TEST]: broken"
        )
        runner.restore_baseline.return_value = (
            "rollback_baseline:abc123",
            "rollback:success",
            "repo_clean:true",
        )
        runner_type.return_value = runner

        initial_response = {
            "id": "local-test",
            "status": "completed",
            "model": "test-model",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"files":{"source.py":"changed\\n"},"commit_message":"test","engineering_summary":"test"}',
                        }
                    ],
                }
            ],
        }
        failed_repair_response = {
            "id": "repair-failed",
            "status": "failed",
            "model": "test-model",
            "output": [],
        }
        provider_call.side_effect = [
            (initial_response, "ollama"),
            (failed_repair_response, "ollama"),
        ]

        mission = {
            "agent_id": "DEV-001",
            "wp_id": "TEST-ROLLBACK",
            "project": "rvsc",
            "repository": "GitSly1/RAMTech-RVSC-Control-Center",
            "base_branch": "main",
            "work_branch": "rvsc/TEST-ROLLBACK",
            "objective": "prove rollback",
            "allowed_paths": ["source.py"],
            "acceptance_criteria": ["validation passes"],
            "validation_commands": [
                {"name": "TEST", "argv": ["python", "-c", "print('test')"]}
            ],
        }

        checkpoints = []

        with self.assertRaisesRegex(
            RuntimeError,
            "repair provider status was failed",
        ):
            execute_mission(
                agent_id="DEV-001",
                agent_name="Daniel",
                role="Engineering",
                mission=mission,
                checkpoint=lambda name, evidence: checkpoints.append((name, evidence)),
            )

        runner.restore_baseline.assert_called_once_with()
        self.assertEqual(provider_call.call_count, 2)
        self.assertTrue(
            any(name == "implementation_rolled_back" for name, _ in checkpoints)
        )
        self.assertTrue(
            any(name == "repair_started" for name, _ in checkpoints)
        )

    @patch("controller.generic_engineering_worker._provider_call")
    @patch("controller.generic_engineering_worker._prepare_branch")
    @patch("controller.generic_engineering_worker._configure_git_identity")
    @patch("controller.generic_engineering_worker.EngineeringMissionRunner")
    def test_validation_failure_gets_one_successful_repair(
        self,
        runner_type,
        configure_identity,
        prepare_branch,
        provider_call,
    ):
        environment = Mock()
        environment.read_text.return_value = "baseline\n"
        environment.run.side_effect = [
            Mock(returncode=0, stdout="a" * 40 + "\n", stderr=""),
            Mock(returncode=0, stdout="", stderr=""),
        ]
        environment.git_status.return_value = Mock(
            returncode=0, stdout="", stderr=""
        )

        runner = Mock()
        runner.environment = environment
        runner.preflight.return_value = ("repo_clean:true",)
        runner.evidence_after_change.return_value = ("diff_present:true",)
        runner.validate.side_effect = [
            EngineeringValidationError("validation failed [TEST]: broken"),
            ("validation:TEST:returncode:0",),
        ]
        runner.restore_baseline.return_value = (
            "rollback_baseline:abc123",
            "rollback:success",
            "repo_clean:true",
        )
        runner.commit.return_value = ("commit:created",)
        runner_type.return_value = runner

        first = {
            "id": "proposal-1",
            "status": "completed",
            "model": "test-model",
            "output": [{
                "type": "message",
                "content": [{
                    "type": "output_text",
                    "text": '{"files":{"source.py":"broken\\n"},"commit_message":"first","engineering_summary":"first"}',
                }],
            }],
        }
        repaired = {
            "id": "proposal-2",
            "status": "completed",
            "model": "test-model",
            "output": [{
                "type": "message",
                "content": [{
                    "type": "output_text",
                    "text": '{"files":{"source.py":"fixed\\n"},"commit_message":"repaired","engineering_summary":"repaired"}',
                }],
            }],
        }
        provider_call.side_effect = [
            (first, "ollama"),
            (repaired, "ollama"),
        ]

        mission = {
            "agent_id": "DEV-001",
            "wp_id": "TEST-REPAIR",
            "project": "rvsc",
            "repository": "GitSly1/RAMTech-RVSC-Control-Center",
            "base_branch": "main",
            "work_branch": "rvsc/TEST-REPAIR",
            "objective": "prove bounded repair",
            "allowed_paths": ["source.py"],
            "acceptance_criteria": ["validation passes"],
            "validation_commands": [
                {"name": "TEST", "argv": ["python", "-c", "print('test')"]}
            ],
        }

        checkpoints = []
        result = execute_mission(
            agent_id="DEV-001",
            agent_name="Daniel",
            role="Engineering",
            mission=mission,
            checkpoint=lambda name, evidence: checkpoints.append(
                (name, evidence)
            ),
        )

        self.assertTrue(result["success"])
        self.assertEqual(provider_call.call_count, 2)
        self.assertEqual(runner.validate.call_count, 2)
        runner.restore_baseline.assert_called_once_with()
        self.assertEqual(
            environment.write_text.call_args_list,
            [
                call("source.py", "broken\n"),
                call("source.py", "fixed\n"),
            ],
        )
        self.assertTrue(
            any(name == "repair_started" for name, _ in checkpoints)
        )
        self.assertTrue(
            any(name == "repair_proposal_received" for name, _ in checkpoints)
        )

    @patch("controller.generic_engineering_worker._provider_call")
    @patch("controller.generic_engineering_worker._prepare_branch")
    @patch("controller.generic_engineering_worker._configure_git_identity")
    @patch("controller.generic_engineering_worker.EngineeringMissionRunner")
    def test_second_validation_failure_rolls_back_and_stops(
        self,
        runner_type,
        configure_identity,
        prepare_branch,
        provider_call,
    ):
        environment = Mock()
        environment.read_text.return_value = "baseline\n"

        runner = Mock()
        runner.environment = environment
        runner.preflight.return_value = ("repo_clean:true",)
        runner.evidence_after_change.return_value = ("diff_present:true",)
        runner.validate.side_effect = [
            EngineeringValidationError("validation failed [TEST]: first"),
            EngineeringValidationError("validation failed [TEST]: second"),
        ]
        runner.restore_baseline.return_value = (
            "rollback_baseline:abc123",
            "rollback:success",
            "repo_clean:true",
        )
        runner_type.return_value = runner

        first = {
            "id": "proposal-1",
            "status": "completed",
            "model": "test-model",
            "output": [{
                "type": "message",
                "content": [{
                    "type": "output_text",
                    "text": '{"files":{"source.py":"broken-1\\n"},"commit_message":"first","engineering_summary":"first"}',
                }],
            }],
        }
        repaired = {
            "id": "proposal-2",
            "status": "completed",
            "model": "test-model",
            "output": [{
                "type": "message",
                "content": [{
                    "type": "output_text",
                    "text": '{"files":{"source.py":"broken-2\\n"},"commit_message":"repair","engineering_summary":"repair"}',
                }],
            }],
        }
        provider_call.side_effect = [
            (first, "ollama"),
            (repaired, "ollama"),
        ]

        mission = {
            "agent_id": "DEV-001",
            "wp_id": "TEST-REPAIR-FAIL",
            "project": "rvsc",
            "repository": "GitSly1/RAMTech-RVSC-Control-Center",
            "base_branch": "main",
            "work_branch": "rvsc/TEST-REPAIR-FAIL",
            "objective": "prove bounded repair termination",
            "allowed_paths": ["source.py"],
            "acceptance_criteria": ["validation passes"],
            "validation_commands": [
                {"name": "TEST", "argv": ["python", "-c", "print('test')"]}
            ],
        }

        with self.assertRaises(EngineeringValidationError):
            execute_mission(
                agent_id="DEV-001",
                agent_name="Daniel",
                role="Engineering",
                mission=mission,
            )

        self.assertEqual(provider_call.call_count, 2)
        self.assertEqual(runner.validate.call_count, 2)
        self.assertEqual(runner.restore_baseline.call_count, 2)
        runner.commit.assert_not_called()

    @patch("controller.generic_engineering_worker._provider_call")
    @patch("controller.generic_engineering_worker._prepare_branch")
    @patch("controller.generic_engineering_worker._configure_git_identity")
    @patch("controller.generic_engineering_worker.EngineeringMissionRunner")
    def test_non_validation_failure_rolls_back_without_repair(
        self,
        runner_type,
        configure_identity,
        prepare_branch,
        provider_call,
    ):
        environment = Mock()
        environment.read_text.return_value = "baseline\n"
        environment.write_text.side_effect = RuntimeError("write failed")

        runner = Mock()
        runner.environment = environment
        runner.preflight.return_value = ("repo_clean:true",)
        runner.restore_baseline.return_value = (
            "rollback_baseline:abc123",
            "rollback:success",
            "repo_clean:true",
        )
        runner_type.return_value = runner

        proposal = {
            "id": "proposal-1",
            "status": "completed",
            "model": "test-model",
            "output": [{
                "type": "message",
                "content": [{
                    "type": "output_text",
                    "text": '{"files":{"source.py":"changed\\n"},"commit_message":"test","engineering_summary":"test"}',
                }],
            }],
        }
        provider_call.return_value = (proposal, "ollama")

        mission = {
            "agent_id": "DEV-001",
            "wp_id": "TEST-NON-VALIDATION",
            "project": "rvsc",
            "repository": "GitSly1/RAMTech-RVSC-Control-Center",
            "base_branch": "main",
            "work_branch": "rvsc/TEST-NON-VALIDATION",
            "objective": "prove non-validation failure classification",
            "allowed_paths": ["source.py"],
            "acceptance_criteria": ["controlled failure"],
            "validation_commands": [
                {"name": "TEST", "argv": ["python", "-c", "print('test')"]}
            ],
        }

        checkpoints = []

        with self.assertRaisesRegex(RuntimeError, "write failed"):
            execute_mission(
                agent_id="DEV-001",
                agent_name="Daniel",
                role="Engineering",
                mission=mission,
                checkpoint=lambda name, evidence: checkpoints.append(
                    (name, evidence)
                ),
            )

        self.assertEqual(provider_call.call_count, 1)
        runner.restore_baseline.assert_called_once_with()
        runner.validate.assert_not_called()
        runner.commit.assert_not_called()

        names = [name for name, _ in checkpoints]
        self.assertIn("implementation_rolled_back", names)
        self.assertNotIn("repair_started", names)
        self.assertNotIn("repair_proposal_received", names)

    @patch("controller.generic_engineering_worker._provider_call")
    @patch("controller.generic_engineering_worker._prepare_branch")
    @patch("controller.generic_engineering_worker._configure_git_identity")
    @patch("controller.generic_engineering_worker.EngineeringMissionRunner")
    def test_missing_allowed_file_is_presented_to_provider_as_empty_baseline(
        self,
        runner_type,
        configure_identity,
        prepare_branch,
        provider_call,
    ):
        runner = runner_type.return_value
        environment = runner.environment
        environment.read_text.side_effect = FileNotFoundError(
            "authorized new file"
        )
        runner.preflight.return_value = ("preflight:ok",)
        runner.validate.return_value = ()
        runner.evidence_after_change.return_value = ()
        runner.commit.return_value = ("commit:created",)
        environment.run.side_effect = [
            Mock(returncode=0, stdout="a" * 40 + "\n", stderr=""),
            Mock(returncode=0, stdout="", stderr=""),
        ]
        environment.git_status.return_value = Mock(
            returncode=0,
            stdout="",
            stderr="",
        )

        provider_call.return_value = (
            {
                "id": "response-new-file",
                "status": "completed",
                "model": "test-model",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": (
                                    '{"files":{"new_file.py":"VALUE = 1\\n"},'
                                    '"commit_message":"create authorized file",'
                                    '"engineering_summary":"create authorized file"}'
                                ),
                            }
                        ],
                    }
                ],
            },
            "test-provider",
        )

        mission = {
            "agent_id": "DEV-001",
            "wp_id": "TEST-NEW-FILE",
            "project": "rvsc",
            "repository": "GitSly1/RAMTech-RVSC-Control-Center",
            "base_branch": "main",
            "work_branch": "rvsc/TEST-NEW-FILE",
            "objective": "create authorized new file",
            "allowed_paths": ["new_file.py"],
            "acceptance_criteria": ["file is created"],
            "validation_commands": [
                {
                    "name": "TEST",
                    "argv": ["python", "-c", "print('test')"],
                }
            ],
        }

        execute_mission(
            agent_id="DEV-001",
            agent_name="Daniel",
            role="Engineering",
            mission=mission,
        )

        environment.read_text.assert_called_once_with("new_file.py")
        provider_call.assert_called_once()
        prompt = provider_call.call_args.args[0]
        self.assertIn('"new_file.py": ""', prompt)
        environment.write_text.assert_called_once_with(
            "new_file.py",
            "VALUE = 1\n",
        )
    def test_bounded_context_text_preserves_head_tail_and_marker(self):
        text = "HEAD-" + ("x" * 200) + "-TAIL"

        bounded = _bounded_context_text(
            "controller/runtime_supervisor.py",
            text,
            120,
        )

        self.assertEqual(len(bounded), 120)
        self.assertTrue(bounded.startswith("HEAD-"))
        self.assertTrue(bounded.endswith("-TAIL"))
        self.assertIn("RVSC CONTEXT OMITTED", bounded)
        self.assertIn("controller/runtime_supervisor.py", bounded)

    def test_context_budget_is_deterministic_and_bounded(self):
        context = {
            "controller/runtime_supervisor.py": "A" * 10000,
            "tests/test_runtime_supervisor.py": "B" * 6000,
        }

        first, first_evidence = _budget_context_files(
            context,
            total_budget=4000,
        )
        second, second_evidence = _budget_context_files(
            context,
            total_budget=4000,
        )

        self.assertEqual(first, second)
        self.assertEqual(first_evidence, second_evidence)

        self.assertEqual(
            list(first),
            [
                "controller/runtime_supervisor.py",
                "tests/test_runtime_supervisor.py",
            ],
        )

        supplied = sum(len(value) for value in first.values())

        self.assertLessEqual(supplied, 4000)
        self.assertIn(
            "context_original_chars:16000",
            first_evidence,
        )
        self.assertIn(
            f"context_supplied_chars:{supplied}",
            first_evidence,
        )
        self.assertIn(
            "context_truncated:true",
            first_evidence,
        )

        for value in first.values():
            self.assertIn("RVSC CONTEXT OMITTED", value)

    def test_non_ollama_provider_preserves_full_context(self):
        source = Path(
            "controller/generic_engineering_worker.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'provider = os.environ.get("RVSC_AI_PROVIDER", "ollama").strip().lower()',
            source,
        )
        self.assertIn(
            'if provider == "ollama":',
            source,
        )
        self.assertIn(
            'bounded_context_files = dict(context_files)',
            source,
        )
        self.assertIn(
            '"context_budget_chars:unbounded"',
            source,
        )

    def test_context_budget_preserves_small_context_exactly(self):
        context = {
            "controller/runtime_supervisor.py":
                "STATUS = 'running'\n",
        }

        bounded, evidence = _budget_context_files(
            context,
            total_budget=4000,
        )

        self.assertEqual(bounded, context)
        self.assertIn(
            "context_truncated:false",
            evidence,
        )

    def test_read_context_files_reads_only_requested_repository_files(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "controller").mkdir()
            (repo / "controller" / "runtime_supervisor.py").write_text(
                "STATUS_SURFACE = 'runtime'\n",
                encoding="utf-8",
            )

            result = _read_context_files(
                repo,
                {"context_paths": ["controller/runtime_supervisor.py"]},
            )

            self.assertEqual(
                result,
                {
                    "controller/runtime_supervisor.py":
                    "STATUS_SURFACE = 'runtime'\n"
                },
            )

    def test_missing_required_context_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                RuntimeError,
                "required context file does not exist",
            ):
                _read_context_files(
                    Path(directory),
                    {"context_paths": ["controller/missing.py"]},
                )

    @patch("controller.generic_engineering_worker._provider_call")
    @patch("controller.generic_engineering_worker._prepare_branch")
    @patch("controller.generic_engineering_worker._configure_git_identity")
    @patch("controller.generic_engineering_worker.EngineeringMissionRunner")
    def test_read_only_context_is_supplied_but_not_added_to_write_scope(
        self,
        runner_type,
        configure_identity,
        prepare_branch,
        provider_call,
    ):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "controller").mkdir()
            (repo / "controller" / "runtime_supervisor.py").write_text(
                "def status_dicts():\n    return {'controller': 'running'}\n",
                encoding="utf-8",
            )

            runner = runner_type.return_value
            environment = runner.environment
            environment.read_text.side_effect = FileNotFoundError(
                "authorized new file"
            )
            runner.preflight.return_value = ("preflight:ok",)
            runner.validate.return_value = ()
            runner.evidence_after_change.return_value = ()
            runner.commit.return_value = ("commit:created",)
            environment.run.side_effect = [
                Mock(returncode=0, stdout="a" * 40 + "\n", stderr=""),
                Mock(returncode=0, stdout="", stderr=""),
            ]
            environment.git_status.return_value = Mock(
                returncode=0,
                stdout="",
                stderr="",
            )

            provider_call.return_value = (
                {
                    "id": "response-context",
                    "status": "completed",
                    "model": "test-model",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        '{"files":{"new_file.py":"VALUE = 1\\n"},'
                                        '"commit_message":"use runtime context",'
                                        '"engineering_summary":"bounded context"}'
                                    ),
                                }
                            ],
                        }
                    ],
                },
                "test-provider",
            )

            mission = {
                "agent_id": "DEV-001",
                "wp_id": "TEST-CONTEXT",
                "project": "rvsc",
                "repository": "GitSly1/RAMTech-RVSC-Control-Center",
                "base_branch": "main",
                "work_branch": "rvsc/TEST-CONTEXT",
                "objective": "use runtime status surface",
                "allowed_paths": ["new_file.py"],
                "context_paths": ["controller/runtime_supervisor.py"],
                "acceptance_criteria": ["context is read only"],
                "validation_commands": [
                    {
                        "name": "TEST",
                        "argv": ["python", "-c", "print('test')"],
                    }
                ],
            }

            with patch.dict(
                os.environ,
                {"RVSC_RVSC_REPO": str(repo)},
                clear=False,
            ):
                execute_mission(
                    agent_id="DEV-001",
                    agent_name="Daniel",
                    role="Engineering",
                    mission=mission,
                )

            prompt = provider_call.call_args.args[0]

            self.assertIn(
                '"controller/runtime_supervisor.py"',
                prompt,
            )
            self.assertIn(
                "def status_dicts()",
                prompt,
            )
            self.assertIn(
                "READ-ONLY CONTEXT FILES",
                prompt,
            )

            environment.write_text.assert_called_once_with(
                "new_file.py",
                "VALUE = 1\n",
            )

    @patch("controller.generic_engineering_worker._provider_call")
    @patch("controller.generic_engineering_worker._prepare_branch")
    @patch("controller.generic_engineering_worker._configure_git_identity")
    @patch("controller.generic_engineering_worker.EngineeringMissionRunner")
    def test_context_only_path_cannot_be_returned_as_output(
        self,
        runner_type,
        configure_identity,
        prepare_branch,
        provider_call,
    ):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "controller").mkdir()
            (repo / "controller" / "runtime_supervisor.py").write_text(
                "STATUS = 'running'\n",
                encoding="utf-8",
            )

            runner = runner_type.return_value
            environment = runner.environment
            environment.read_text.return_value = "baseline\n"
            runner.preflight.return_value = ("preflight:ok",)

            provider_call.return_value = (
                {
                    "id": "response-unauthorized-context",
                    "status": "completed",
                    "model": "test-model",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        '{"files":{'
                                        '"source.py":"updated\\n",'
                                        '"controller/runtime_supervisor.py":"forbidden\\n"'
                                        '},'
                                        '"commit_message":"bad scope",'
                                        '"engineering_summary":"bad scope"}'
                                    ),
                                }
                            ],
                        }
                    ],
                },
                "test-provider",
            )

            mission = {
                "agent_id": "DEV-001",
                "wp_id": "TEST-CONTEXT-SCOPE",
                "project": "rvsc",
                "repository": "GitSly1/RAMTech-RVSC-Control-Center",
                "base_branch": "main",
                "work_branch": "rvsc/TEST-CONTEXT-SCOPE",
                "objective": "prove context remains read only",
                "allowed_paths": ["source.py"],
                "context_paths": ["controller/runtime_supervisor.py"],
                "acceptance_criteria": ["context cannot become output"],
                "validation_commands": [
                    {
                        "name": "TEST",
                        "argv": ["python", "-c", "print('test')"],
                    }
                ],
            }

            with patch.dict(
                os.environ,
                {"RVSC_RVSC_REPO": str(repo)},
                clear=False,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "unauthorized or incomplete file set",
                ):
                    execute_mission(
                        agent_id="DEV-001",
                        agent_name="Daniel",
                        role="Engineering",
                        mission=mission,
                    )

            environment.write_text.assert_not_called()
            runner.commit.assert_not_called()


    @patch("controller.generic_engineering_worker._provider_call")
    @patch("controller.generic_engineering_worker._prepare_branch")
    @patch("controller.generic_engineering_worker._configure_git_identity")
    @patch("controller.generic_engineering_worker.EngineeringMissionRunner")
    def test_malformed_files_shape_is_reported_without_contents(
        self,
        runner_type,
        configure_identity,
        prepare_branch,
        provider_call,
    ):
        runner = runner_type.return_value
        environment = runner.environment
        environment.read_text.return_value = "baseline\n"
        runner.preflight.return_value = ("preflight:ok",)

        provider_call.return_value = (
            {
                "id": "response-malformed-files",
                "status": "completed",
                "model": "test-model",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": (
                                    '{"files":null,'
                                    '"commit_message":"none",'
                                    '"engineering_summary":"malformed"}'
                                ),
                            }
                        ],
                    }
                ],
            },
            "test-provider",
        )

        mission = {
            "agent_id": "DEV-001",
            "wp_id": "TEST-PROPOSAL-SHAPE",
            "project": "rvsc",
            "repository": "GitSly1/RAMTech-RVSC-Control-Center",
            "base_branch": "main",
            "work_branch": "rvsc/TEST-PROPOSAL-SHAPE",
            "objective": "observe malformed proposal shape",
            "allowed_paths": ["source.py"],
            "acceptance_criteria": ["fail closed"],
            "validation_commands": [
                {
                    "name": "TEST",
                    "argv": ["python", "-m", "unittest"],
                }
            ],
        }

        with self.assertRaisesRegex(
            RuntimeError,
            r"files_shape:null; files_count:0",
        ):
            execute_mission(
                agent_id="DEV-001",
                agent_name="Daniel",
                role="Engineering",
                mission=mission,
            )

        self.assertEqual(provider_call.call_count, 1)
        environment.write_text.assert_not_called()
        runner.validate.assert_not_called()
        runner.commit.assert_not_called()

    @patch("controller.generic_engineering_worker._provider_call")
    @patch("controller.generic_engineering_worker._prepare_branch")
    @patch("controller.generic_engineering_worker._configure_git_identity")
    @patch("controller.generic_engineering_worker.EngineeringMissionRunner")
    def test_missing_authorized_files_receive_one_bounded_proposal_repair(
        self,
        runner_type,
        configure_identity,
        prepare_branch,
        provider_call,
    ):
        runner = runner_type.return_value
        environment = runner.environment
        environment.read_text.return_value = "baseline\n"
        runner.preflight.return_value = ("preflight:ok",)
        runner.evidence_after_change.return_value = ("diff:ok",)
        runner.validate.return_value = ("validation:ok",)
        runner.commit.return_value = ("commit:created",)
        environment.run.side_effect = [
            Mock(returncode=0, stdout="a" * 40 + "\n", stderr=""),
            Mock(returncode=0, stdout="", stderr=""),
        ]
        environment.git_status.return_value = Mock(
            returncode=0,
            stdout="",
            stderr="",
        )

        provider_call.side_effect = [
            (
                {
                    "id": "response-incomplete",
                    "status": "completed",
                    "model": "test-model",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        '{"files":{},'
                                        '"commit_message":"first",'
                                        '"engineering_summary":"incomplete"}'
                                    ),
                                }
                            ],
                        }
                    ],
                },
                "test-provider",
            ),
            (
                {
                    "id": "response-repaired",
                    "status": "completed",
                    "model": "test-model",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        '{"files":{"source.py":"updated\\n"},'
                                        '"commit_message":"fixed",'
                                        '"engineering_summary":"complete"}'
                                    ),
                                }
                            ],
                        }
                    ],
                },
                "test-provider",
            ),
        ]

        mission = {
            "agent_id": "DEV-001",
            "wp_id": "TEST-INCOMPLETE-REPAIR",
            "project": "rvsc",
            "repository": "GitSly1/RAMTech-RVSC-Control-Center",
            "base_branch": "main",
            "work_branch": "rvsc/TEST-INCOMPLETE-REPAIR",
            "objective": "repair one incomplete authorized proposal",
            "allowed_paths": ["source.py"],
            "acceptance_criteria": ["return exact authorized file set"],
            "validation_commands": [
                {
                    "name": "TEST",
                    "argv": ["python", "-c", "print('test')"],
                }
            ],
        }

        checkpoints = []

        result = execute_mission(
            agent_id="DEV-001",
            agent_name="Daniel",
            role="Engineering",
            mission=mission,
            checkpoint=lambda name, evidence: checkpoints.append(
                (name, evidence)
            ),
        )

        self.assertTrue(result["success"])
        self.assertEqual(provider_call.call_count, 2)
        environment.write_text.assert_called_once_with(
            "source.py",
            "updated\n",
        )
        names = [name for name, _ in checkpoints]
        self.assertIn("proposal_repair_started", names)
        self.assertIn("proposal_repair_received", names)

    @patch("controller.generic_engineering_worker._provider_call")
    @patch("controller.generic_engineering_worker._prepare_branch")
    @patch("controller.generic_engineering_worker._configure_git_identity")
    @patch("controller.generic_engineering_worker.EngineeringMissionRunner")
    def test_second_incomplete_proposal_fails_closed_without_writes(
        self,
        runner_type,
        configure_identity,
        prepare_branch,
        provider_call,
    ):
        runner = runner_type.return_value
        environment = runner.environment
        environment.read_text.return_value = "baseline\n"
        runner.preflight.return_value = ("preflight:ok",)

        incomplete_response = (
            {
                "id": "response-incomplete",
                "status": "completed",
                "model": "test-model",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": (
                                    '{"files":{},'
                                    '"commit_message":"still incomplete",'
                                    '"engineering_summary":"incomplete"}'
                                ),
                            }
                        ],
                    }
                ],
            },
            "test-provider",
        )
        provider_call.side_effect = [
            incomplete_response,
            incomplete_response,
        ]

        mission = {
            "agent_id": "DEV-001",
            "wp_id": "TEST-INCOMPLETE-FAIL-CLOSED",
            "project": "rvsc",
            "repository": "GitSly1/RAMTech-RVSC-Control-Center",
            "base_branch": "main",
            "work_branch": "rvsc/TEST-INCOMPLETE-FAIL-CLOSED",
            "objective": "prove bounded incomplete repair",
            "allowed_paths": ["source.py"],
            "acceptance_criteria": ["return exact authorized file set"],
            "validation_commands": [
                {
                    "name": "TEST",
                    "argv": ["python", "-c", "print('test')"],
                }
            ],
        }

        with self.assertRaisesRegex(
            RuntimeError,
            "repair returned unauthorized or incomplete file set",
        ):
            execute_mission(
                agent_id="DEV-001",
                agent_name="Daniel",
                role="Engineering",
                mission=mission,
            )

        self.assertEqual(provider_call.call_count, 2)
        environment.write_text.assert_not_called()
        runner.validate.assert_not_called()
        runner.commit.assert_not_called()

    def test_git_identity_failure_stops_execution(self):
        environment = Mock()
        environment.run.return_value = Mock(returncode=1, stdout="", stderr="failed")
        with self.assertRaises(EngineeringEnvironmentError):
            _configure_git_identity(environment, "DEV-001", "Daniel")


if __name__ == "__main__":
    unittest.main()
