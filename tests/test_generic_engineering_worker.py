from __future__ import annotations

import os
import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from controller.engineering_environment import EngineeringEnvironmentError
from controller.engineering_runner import EngineeringValidationError
from controller.generic_engineering_worker import _proposal_diagnostics, _apply_bounded_edits, _budget_context_files, _bounded_context_text, _configure_git_identity, _git_identity, _ollama_call, _ollama_proposal_schema, _engineering_repair_prompt, _read_context_files, _repo_root, _validations, _worker_request, execute_mission


class GenericEngineeringWorkerTests(unittest.TestCase):
    def test_bounded_edits_apply_exact_authorized_anchor(self):
        source = {
            "controller/a.py": "before = 1\nafter = 2\n",
            "tests/test_a.py": "assert before == 1\n",
        }

        result = _apply_bounded_edits(
            source,
            [
                {
                    "operation": "replace",
                    "path": "controller/a.py",
                    "old_text": "before = 1",
                    "new_text": "before = 3",
                }
            ],
            existing_paths={"controller/a.py", "tests/test_a.py"},
        )

        self.assertEqual(
            result["controller/a.py"],
            "before = 3\nafter = 2\n",
        )
        self.assertEqual(
            result["tests/test_a.py"],
            source["tests/test_a.py"],
        )

    def test_bounded_edits_support_sequential_edits(self):
        result = _apply_bounded_edits(
            {"source.py": "VALUE = 1\n"},
            [
                {
                    "operation": "replace",
                    "path": "source.py",
                    "old_text": "VALUE = 1",
                    "new_text": "VALUE = 2",
                },
                {
                    "operation": "replace",
                    "path": "source.py",
                    "old_text": "VALUE = 2",
                    "new_text": "VALUE = 3",
                },
            ],
            existing_paths={"source.py"},
        )

        self.assertEqual(result["source.py"], "VALUE = 3\n")

    def test_bounded_edits_reject_unauthorized_path(self):
        with self.assertRaisesRegex(RuntimeError, "unauthorized path"):
            _apply_bounded_edits(
                {"allowed.py": "VALUE = 1\n"},
                [
                    {
                        "operation": "replace",
                        "path": "outside.py",
                        "old_text": "VALUE = 1",
                        "new_text": "VALUE = 2",
                    }
                ],
                existing_paths={"allowed.py"},
            )

    def test_bounded_edits_reject_missing_anchor_atomically(self):
        source = {
            "a.py": "A = 1\n",
            "b.py": "B = 1\n",
        }

        with self.assertRaisesRegex(
            RuntimeError,
            "anchor occurrence count",
        ):
            _apply_bounded_edits(
                source,
                [
                    {
                        "operation": "replace",
                        "path": "a.py",
                        "old_text": "A = 1",
                        "new_text": "A = 2",
                    },
                    {
                        "operation": "replace",
                        "path": "b.py",
                        "old_text": "MISSING",
                        "new_text": "B = 2",
                    },
                ],
                existing_paths={"a.py", "b.py"},
            )

        self.assertEqual(source["a.py"], "A = 1\n")
        self.assertEqual(source["b.py"], "B = 1\n")

    def test_bounded_edits_reject_ambiguous_anchor(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "anchor occurrence count",
        ):
            _apply_bounded_edits(
                {"source.py": "VALUE\nVALUE\n"},
                [
                    {
                        "operation": "replace",
                        "path": "source.py",
                        "old_text": "VALUE",
                        "new_text": "OTHER",
                    }
                ],
                existing_paths={"source.py"},
            )

    def test_bounded_edits_reject_empty_anchor(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "non-empty old_text",
        ):
            _apply_bounded_edits(
                {"source.py": "VALUE = 1\n"},
                [
                    {
                        "operation": "replace",
                        "path": "source.py",
                        "old_text": "",
                        "new_text": "VALUE = 2",
                    }
                ],
                existing_paths={"source.py"},
            )

    def test_bounded_edits_reject_malformed_operation(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "exactly operation, path, old_text, new_text",
        ):
            _apply_bounded_edits(
                {"source.py": "VALUE = 1\n"},
                [
                    {
                        "operation": "replace",
                        "path": "source.py",
                        "old_text": "VALUE = 1",
                    }
                ],
                existing_paths={"source.py"},
            )

    def test_bounded_create_accepts_absent_authorized_file(self):
        result = _apply_bounded_edits(
            {"new.py": ""},
            [
                {
                    "operation": "create",
                    "path": "new.py",
                    "content": "VALUE = 1\n",
                }
            ],
            existing_paths=set(),
        )

        self.assertEqual(result["new.py"], "VALUE = 1\n")

    def test_bounded_create_accepts_empty_content_for_absent_file(self):
        result = _apply_bounded_edits(
            {"new.py": ""},
            [
                {
                    "operation": "create",
                    "path": "new.py",
                    "content": "",
                }
            ],
            existing_paths=set(),
        )

        self.assertEqual(result["new.py"], "")

    def test_bounded_create_rejects_existing_empty_file(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "targets existing path",
        ):
            _apply_bounded_edits(
                {"empty.py": ""},
                [
                    {
                        "operation": "create",
                        "path": "empty.py",
                        "content": "VALUE = 1\n",
                    }
                ],
                existing_paths={"empty.py"},
            )

    def test_bounded_create_rejects_existing_nonempty_file(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "targets existing path",
        ):
            _apply_bounded_edits(
                {"source.py": "VALUE = 1\n"},
                [
                    {
                        "operation": "create",
                        "path": "source.py",
                        "content": "VALUE = 2\n",
                    }
                ],
                existing_paths={"source.py"},
            )

    def test_bounded_create_rejects_unauthorized_path(self):
        with self.assertRaisesRegex(RuntimeError, "unauthorized path"):
            _apply_bounded_edits(
                {"allowed.py": ""},
                [
                    {
                        "operation": "create",
                        "path": "outside.py",
                        "content": "VALUE = 1\n",
                    }
                ],
                existing_paths=set(),
            )

    def test_bounded_create_rejects_duplicate_creation(self):
        with self.assertRaisesRegex(RuntimeError, "targets existing path"):
            _apply_bounded_edits(
                {"new.py": ""},
                [
                    {
                        "operation": "create",
                        "path": "new.py",
                        "content": "first\n",
                    },
                    {
                        "operation": "create",
                        "path": "new.py",
                        "content": "second\n",
                    },
                ],
                existing_paths=set(),
            )


    def test_bounded_create_then_replace_same_path(self):
        result = _apply_bounded_edits(
            {"new.py": ""},
            [
                {
                    "operation": "create",
                    "path": "new.py",
                    "content": "VALUE = 1\n",
                },
                {
                    "operation": "replace",
                    "path": "new.py",
                    "old_text": "VALUE = 1",
                    "new_text": "VALUE = 2",
                },
            ],
            existing_paths=set(),
        )

        self.assertEqual(
            result["new.py"],
            "VALUE = 2\n",
        )

    def test_bounded_replace_rejects_absent_authorized_file(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "targets absent path",
        ):
            _apply_bounded_edits(
                {"new.py": ""},
                [
                    {
                        "operation": "replace",
                        "path": "new.py",
                        "old_text": "VALUE",
                        "new_text": "OTHER",
                    }
                ],
                existing_paths=set(),
            )

    def test_bounded_replace_rejects_noop(self):
        with self.assertRaisesRegex(RuntimeError, "no-op"):
            _apply_bounded_edits(
                {"source.py": "VALUE = 1\n"},
                [
                    {
                        "operation": "replace",
                        "path": "source.py",
                        "old_text": "VALUE = 1",
                        "new_text": "VALUE = 1",
                    }
                ],
                existing_paths={"source.py"},
            )

    def test_bounded_existing_paths_must_be_authorized(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "existing_paths contains unauthorized path",
        ):
            _apply_bounded_edits(
                {"source.py": "VALUE = 1\n"},
                [
                    {
                        "operation": "replace",
                        "path": "source.py",
                        "old_text": "1",
                        "new_text": "2",
                    }
                ],
                existing_paths={"source.py", "outside.py"},
            )

    def test_ollama_proposal_schema_requires_exact_engineering_contract(self):
        allowed_paths = ("controller/a.py", "tests/test_a.py")
        schema = _ollama_proposal_schema(allowed_paths)

        self.assertEqual(schema["type"], "object")
        self.assertEqual(
            schema["required"],
            [
                "edits",
                "commit_message",
                "engineering_summary",
            ],
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            set(schema["properties"]),
            {
                "edits",
                "commit_message",
                "engineering_summary",
            },
        )

        edits_schema = schema["properties"]["edits"]
        self.assertEqual(edits_schema["type"], "array")
        self.assertEqual(edits_schema["minItems"], 1)

        variants = edits_schema["items"]["oneOf"]
        self.assertEqual(len(variants), 2)

        replace_schema, create_schema = variants

        self.assertEqual(
            replace_schema["properties"]["operation"]["enum"],
            ["replace"],
        )
        self.assertEqual(
            create_schema["properties"]["operation"]["enum"],
            ["create"],
        )

        for variant in variants:
            self.assertEqual(
                variant["properties"]["path"]["enum"],
                list(allowed_paths),
            )
            self.assertFalse(
                variant["additionalProperties"]
            )

        self.assertEqual(
            replace_schema["required"],
            [
                "operation",
                "path",
                "old_text",
                "new_text",
            ],
        )
        self.assertEqual(
            replace_schema["properties"]["old_text"]["minLength"],
            1,
        )

        self.assertEqual(
            create_schema["required"],
            [
                "operation",
                "path",
                "content",
            ],
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

        with self.assertRaises(ValueError):
            _ollama_proposal_schema(
                ("controller/a.py", "controller/a.py")
            )

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

        schema = payload["format"]

        self.assertEqual(
            schema["required"],
            [
                "edits",
                "commit_message",
                "engineering_summary",
            ],
        )

        variants = (
            schema["properties"]["edits"]["items"]["oneOf"]
        )

        self.assertEqual(len(variants), 2)

        for variant in variants:
            self.assertEqual(
                variant["properties"]["path"]["enum"],
                list(allowed_paths),
            )
            self.assertFalse(
                variant["additionalProperties"]
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

    def test_repair_prompt_requires_validation_diagnosis(self):
        mission = {
            "agent_id": "DEV-001",
            "wp_id": "TEST-REPAIR-DIAGNOSIS",
            "project": "rvsc",
            "repository": "GitSly1/RAMTech-RVSC-Control-Center",
            "base_branch": "main",
            "work_branch": "rvsc/TEST-REPAIR-DIAGNOSIS",
            "objective": "prove diagnostic repair contract",
            "allowed_paths": ["source.py"],
            "acceptance_criteria": ["validation passes"],
        }

        prompt = _engineering_repair_prompt(
            "DEV-001",
            "Daniel",
            "Engineering",
            mission,
            {"source.py": "baseline\n"},
            {"context.py": "reference\n"},
            {
                "edits": [{"operation": "replace", "path": "source.py", "old_text": "baseline\n", "new_text": "from source import broken\n"}],
                "commit_message": "failed",
                "engineering_summary": "failed",
            },
            "ImportError: circular import in source.py",
        )

        self.assertIn("diagnose the validation failure", prompt)
        self.assertIn("concrete defective generated code", prompt)
        self.assertIn("BASELINE FILES", prompt)
        self.assertIn("READ-ONLY CONTEXT FILES", prompt)
        self.assertIn("do not merely repeat", prompt)
        self.assertIn("ImportError: circular import in source.py", prompt)
        self.assertIn("from source import broken", prompt)
    def test_validations_reject_more_than_two_commands(self):
        mission = {
            "validation_commands": [
                {"name": "one", "argv": ["python", "-c", "print(1)"]},
                {"name": "two", "argv": ["python", "-c", "print(2)"]},
                {"name": "three", "argv": ["python", "-c", "print(3)"]},
            ]
        }

        with self.assertRaisesRegex(
            ValueError,
            "at most two validation commands",
        ):
            _validations(mission)

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
                            "text": '{"edits":[{"operation":"replace","path":"source.py","old_text":"baseline\\n","new_text":"changed\\n"}],"commit_message":"test","engineering_summary":"test"}',
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
                    "text": '{"edits":[{"operation":"replace","path":"source.py","old_text":"baseline\\n","new_text":"broken\\n"}],"commit_message":"first","engineering_summary":"first"}',
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
                    "text": '{"edits":[{"operation":"replace","path":"source.py","old_text":"baseline\\n","new_text":"fixed\\n"}],"commit_message":"repaired","engineering_summary":"repaired"}',
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
                    "text": '{"edits":[{"operation":"replace","path":"source.py","old_text":"baseline\\n","new_text":"broken-1\\n"}],"commit_message":"first","engineering_summary":"first"}',
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
                    "text": '{"edits":[{"operation":"replace","path":"source.py","old_text":"baseline\\n","new_text":"broken-2\\n"}],"commit_message":"repair","engineering_summary":"repair"}',
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
                    "text": '{"edits":[{"operation":"replace","path":"source.py","old_text":"baseline\\n","new_text":"changed\\n"}],"commit_message":"test","engineering_summary":"test"}',
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

    def test_source_loading_distinguishes_existing_empty_from_absent(self):
        allowed_paths = ("existing_empty.py", "absent.py")
        source_files = {}
        existing_paths = set()

        class Environment:
            def read_text(self, path):
                if path == "existing_empty.py":
                    return ""
                raise FileNotFoundError(path)

        environment = Environment()

        for path in allowed_paths:
            try:
                source_files[path] = environment.read_text(path)
                existing_paths.add(path)
            except FileNotFoundError:
                source_files[path] = ""

        self.assertEqual(
            source_files,
            {
                "existing_empty.py": "",
                "absent.py": "",
            },
        )
        self.assertEqual(
            existing_paths,
            {"existing_empty.py"},
        )

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
                                    '{"edits":[{"operation":"create","path":"new_file.py","content":"VALUE = 1\\n"}],"commit_message":"create authorized file","engineering_summary":"create authorized file"}'
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
                                        '{"edits":[{"operation":"create","path":"new_file.py","content":"VALUE = 1\\n"}],"commit_message":"use runtime context","engineering_summary":"bounded context"}'
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
                                        '{"edits":[{"operation":"replace","path":"controller/runtime_supervisor.py","old_text":"STATUS = \'running\'\\n","new_text":"forbidden\\n"}],"commit_message":"bad scope","engineering_summary":"bad scope"}'
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
                    "targets unauthorized path",
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
    def test_malformed_edits_shape_is_reported_without_contents(
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
                                    '{"edits":null,"commit_message":"none","engineering_summary":"malformed"}'
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
            r"edits_shape:null; edits_count:0",
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
    def test_compact_subset_proposal_writes_only_touched_authorized_path(
        self,
        runner_type,
        configure_identity,
        prepare_branch,
        provider_call,
    ):
        runner = runner_type.return_value
        environment = runner.environment

        def read_text(path):
            values = {
                "source.py": "baseline\n",
                "untouched.py": "untouched\n",
            }
            return values[path]

        environment.read_text.side_effect = read_text

        runner.preflight.return_value = ("repo_clean:true",)
        runner.evidence_after_change.return_value = (
            "diff_present:true",
        )
        runner.validate.return_value = ()
        runner.commit.return_value = ("commit:created",)
        environment.run.side_effect = [
            Mock(returncode=0, stdout="a" * 40 + "\n", stderr=""),
            Mock(returncode=0, stdout="", stderr=""),
        ]
        environment.git_status.return_value = Mock(returncode=0, stdout="", stderr="")

        provider_call.return_value = ({'id': 'response-compact-test', 'status': 'completed', 'model': 'test-model', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"edits":[{"operation":"replace","path":"source.py","old_text":"baseline\\n","new_text":"changed\\n"}],"commit_message":"subset edit","engineering_summary":"touch one authorized path"}'}]}]}, "test-provider")

        mission = {
            "agent_id": 'DEV-001',
            "wp_id": 'UX-SUBSET',
            "project": 'rvsc',
            "repository": 'GitSly1/RAMTech-RVSC-Control-Center',
            "base_branch": 'main',
            "work_branch": 'rvsc/UX-SUBSET',
            "objective": 'prove compact subset writes only touched authorized path',
            "allowed_paths": ['source.py', 'untouched.py'],
            "acceptance_criteria": ['only touched authorized path is written'],
            "validation_commands": [{'name': 'TEST', 'argv': ['python', '-c', "print('test')"]}],
        }

        execute_mission(
            agent_id="DEV-001",
            agent_name="Daniel",
            role="Engineering",
            mission=mission,
        )

        self.assertEqual(provider_call.call_count, 1)

        environment.write_text.assert_called_once_with(
            "source.py",
            "changed\n",
        )

        written_paths = [
            call.args[0]
            for call in environment.write_text.call_args_list
        ]

        self.assertNotIn("untouched.py", written_paths)

        runner.restore_baseline.assert_not_called()
        runner.validate.assert_called_once()
        runner.commit.assert_called_once()

    @patch("controller.generic_engineering_worker._provider_call")
    @patch("controller.generic_engineering_worker._prepare_branch")
    @patch("controller.generic_engineering_worker._configure_git_identity")
    @patch("controller.generic_engineering_worker.EngineeringMissionRunner")
    def test_empty_compact_proposal_fails_closed_without_writes_or_retry(
        self,
        runner_type,
        configure_identity,
        prepare_branch,
        provider_call,
    ):
        runner = runner_type.return_value
        environment = runner.environment
        environment.read_text.return_value = "baseline\n"

        runner.preflight.return_value = ("repo_clean:true",)

        provider_call.return_value = ({'id': 'response-compact-test', 'status': 'completed', 'model': 'test-model', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"edits":[],"commit_message":"none","engineering_summary":"empty proposal"}'}]}]}, "test-provider")

        mission = {
            "agent_id": 'DEV-001',
            "wp_id": 'UX-EMPTY',
            "project": 'rvsc',
            "repository": 'GitSly1/RAMTech-RVSC-Control-Center',
            "base_branch": 'main',
            "work_branch": 'rvsc/UX-EMPTY',
            "objective": 'prove empty compact proposal fails closed',
            "allowed_paths": ['source.py'],
            "acceptance_criteria": ['empty proposal fails without write or retry'],
            "validation_commands": [{'name': 'TEST', 'argv': ['python', '-c', "print('test')"]}],
        }

        with self.assertRaisesRegex(
            RuntimeError,
            "invalid bounded edit proposal",
        ):
            execute_mission(
                agent_id="DEV-001",
                agent_name="Daniel",
                role="Engineering",
                mission=mission,
            )

        self.assertEqual(provider_call.call_count, 1)
        environment.write_text.assert_not_called()
        runner.evidence_after_change.assert_not_called()
        runner.validate.assert_not_called()
        runner.restore_baseline.assert_not_called()
        runner.commit.assert_not_called()

    def test_git_identity_failure_stops_execution(self):
        environment = Mock()
        environment.run.return_value = Mock(returncode=1, stdout="", stderr="failed")
        with self.assertRaises(EngineeringEnvironmentError):
            _configure_git_identity(environment, "DEV-001", "Daniel")

    def test_proposal_diagnostics_are_source_free_and_fingerprint_noop(self):
        diagnostic = _proposal_diagnostics(
            run_id="RUN-DIAGNOSTIC",
            provider_response_id="RESP-DIAGNOSTIC",
            proposal_phase="initial",
            edits=[
                {
                    "operation": "replace",
                    "path": "controller/example.py",
                    "old_text": "same",
                    "new_text": "same",
                },
                {
                    "operation": "create",
                    "path": "tests/example.py",
                    "content": "print('ok')\n",
                },
            ],
        )

        self.assertEqual(diagnostic["run_id"], "RUN-DIAGNOSTIC")
        self.assertEqual(
            diagnostic["provider_response_id"],
            "RESP-DIAGNOSTIC",
        )
        self.assertEqual(diagnostic["proposal_phase"], "initial")
        self.assertEqual(diagnostic["edit_count"], 2)

        replace = diagnostic["edits"][0]
        create = diagnostic["edits"][1]

        self.assertEqual(replace["operation"], "replace")
        self.assertEqual(create["operation"], "create")
        self.assertEqual(
            replace["old_text_sha256"],
            replace["new_text_sha256"],
        )
        self.assertEqual(
            replace["old_text_length"],
            replace["new_text_length"],
        )

        serialized = repr(diagnostic)
        self.assertNotIn("'old_text'", serialized)
        self.assertNotIn("'new_text'", serialized)
        self.assertNotIn("'content'", serialized)



if __name__ == "__main__":
    unittest.main()
