from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from controller.generic_qa_worker import _authoritative_knowledge_context, _classification_consistency_guard, _quinn_cognitive_prompt, _repo_root, _validated_cognitive_assurance, execute_mission


class GenericQAWorkerTests(unittest.TestCase):
    def _prepare_quinn_cognition_fixture(self, root: Path) -> str:
        """Create the minimum complete repository contract Quinn cognition requires."""
        core = root / "golden-core"
        governance = root / "governance"
        core.mkdir(parents=True, exist_ok=True)
        governance.mkdir(parents=True, exist_ok=True)

        (core / "QA_001_QUINN_COGNITION_CONTRACT_V1.md").write_text(
            "QUINN CONTRACT",
            encoding="utf-8",
        )
        (core / "MAX_PLATINUM_ENGINEERING_CORE_V1.md").write_text(
            "MAX DISCIPLINE",
            encoding="utf-8",
        )

        (
            governance / "AUTHORITATIVE_KNOWLEDGE_HIERARCHY.md"
        ).write_text(
            "Human/company authority remains supreme. "
            "Operational projections cannot override stronger evidence.\n",
            encoding="utf-8",
        )
        (governance / "SOURCE_ISOLATION.md").write_text(
            "Repository and allowed-path boundaries remain authoritative.\n",
            encoding="utf-8",
        )
        (governance / "WORK_PACKAGE_LIFECYCLE.md").write_text(
            "Independent QA is required before acceptance and promotion.\n",
            encoding="utf-8",
        )

        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Fixture"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "fixture@example.invalid"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "add", "."],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "quinn cognition fixture"],
            cwd=root,
            check=True,
            capture_output=True,
        )

        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

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

    def test_local_logical_repository_context_cannot_redirect_controlled_workspace(self):
        mission = self.mission()
        mission["repository"] = r"D:\RAMTech\RAMTech_RVSC_Daniel_Work"
        mission["engineering_repository"] = r"D:\RAMTech\RAMTech_RVSC_Daniel_Work"

        with patch.dict(os.environ, {"RVSC_SEMANTIQ_REPO": str(self.semantiq)}, clear=False):
            self.assertEqual(_repo_root(mission), self.semantiq.resolve())

    def test_arbitrary_repository_context_cannot_redirect_controlled_workspace(self):
        mission = self.mission()
        mission["repository"] = r"X:\untrusted\redirect"
        mission["engineering_repository"] = r"X:\untrusted\redirect"

        with patch.dict(os.environ, {"RVSC_SEMANTIQ_REPO": str(self.semantiq)}, clear=False):
            self.assertEqual(_repo_root(mission), self.semantiq.resolve())

    def test_unsupported_project_still_fails_closed(self):
        mission = self.mission()
        mission["project"] = "unsupported"
        mission["engineering_project"] = "unsupported"

        with self.assertRaisesRegex(ValueError, "no controlled repository mapping"):
            _repo_root(mission)

    @patch("controller.generic_qa_worker._cognitive_assurance")
    def test_acquires_exact_pushed_semantiq_branch_and_commit(
        self, cognitive
    ):
        cognitive.return_value = {
            "causal_state": "SATISFIED",
            "summary": "objective and acceptance evidence are sufficient",
            "findings": [
                "reviewed implementation satisfies supplied objective"
            ],
        }
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


    @patch("controller.generic_qa_worker._cognitive_assurance")
    def test_cognitive_rejection_prevents_deterministic_acceptance(
        self, cognitive
    ):
        cognitive.return_value = {
            "causal_state": "IMPLEMENTATION_DEFECT",
            "summary": "implementation does not satisfy objective",
            "findings": ["tests pass but required behavior is absent"],
        }

        with patch(
            "controller.generic_qa_worker._review_repository"
        ) as review, patch(
            "controller.generic_qa_worker._file_evidence",
            return_value="inspected:test.py:sha256:abc",
        ), patch(
            "controller.generic_qa_worker._validated_commands",
            return_value=[("TEST", ["python", "-c", "print('ok')"])],
        ):
            review.return_value.__enter__.return_value = (
                Path("."),
                "rvsc/test",
                "a" * 40,
                ("target_acquisition:test",),
            )

            result = execute_mission(
                agent_id="QA-001",
                agent_name="Quinn",
                role="QA",
                qa_eligible=True,
                mission={
                    "run_id": "QCOG-TEST-REJECT",
                    "project": "rvsc",
                    "work_branch": "rvsc/test",
                    "allowed_paths": ["test.py"],
                    "validation_commands": [
                        {
                            "name": "TEST",
                            "argv": ["python", "-c", "print('ok')"],
                        }
                    ],
                },
                repo_root=Path("."),
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertEqual(
            result["cognitive_classification"],
            "QA_REJECTED_IMPLEMENTATION",
        )

    def test_environment_blocker_requires_environment_classification(self):
        mission = {
            "validation_results": {
                "environment_ready": False,
                "harness_integrity": True,
            }
        }
        cognitive = {
            "causal_state": "CONTRACT_BLOCKER",
            "classification": "QA_BLOCKED_CONTRACT",
            "summary": "dependency unavailable",
            "findings": ["external endpoint unreachable"],
        }

        with self.assertRaisesRegex(
            ValueError,
            "explicit environment blocker",
        ):
            _classification_consistency_guard(mission, cognitive)

    def test_environment_blocker_accepts_environment_classification(self):
        mission = {
            "validation_results": {
                "environment_ready": False,
                "harness_integrity": True,
            }
        }
        cognitive = {
            "causal_state": "ENVIRONMENT_BLOCKER",
            "classification": "QA_BLOCKED_ENVIRONMENT",
            "summary": "dependency unavailable",
            "findings": ["external endpoint unreachable"],
        }

        self.assertIs(
            _classification_consistency_guard(mission, cognitive),
            cognitive,
        )

    def test_consistency_guard_does_not_infer_environment_without_explicit_state(self):
        mission = {
            "validation_results": {
                "harness_integrity": True,
            }
        }
        cognitive = {
            "causal_state": "CONTRACT_BLOCKER",
            "classification": "QA_BLOCKED_CONTRACT",
            "summary": "judgment remains cognitive",
            "findings": ["no explicit environment blocker"],
        }

        self.assertIs(
            _classification_consistency_guard(mission, cognitive),
            cognitive,
        )

    def test_causal_state_maps_to_rvsc_disposition_deterministically(self):
        cases = {
            "SATISFIED": "QA_ACCEPTED",
            "IMPLEMENTATION_DEFECT": "QA_REJECTED_IMPLEMENTATION",
            "REQUIREMENT_DEFECT": "QA_REJECTED_REQUIREMENT",
            "CONTRACT_BLOCKER": "QA_BLOCKED_CONTRACT",
            "HARNESS_BLOCKER": "QA_BLOCKED_HARNESS",
            "ENVIRONMENT_BLOCKER": "QA_BLOCKED_ENVIRONMENT",
            "BOUNDARY_BLOCKER": "QA_BLOCKED_BOUNDARY",
            "EVIDENCE_BLOCKER": "QA_BLOCKED_EVIDENCE",
        }

        for causal_state, expected in cases.items():
            with self.subTest(causal_state=causal_state):
                result = _validated_cognitive_assurance(
                    {
                        "causal_state": causal_state,
                        "summary": "root cause assessed",
                        "findings": ["evidence-backed finding"],
                    }
                )
                self.assertEqual(result["causal_state"], causal_state)
                self.assertEqual(result["classification"], expected)

    def test_cognitive_result_rejects_unknown_causal_state(self):
        with self.assertRaisesRegex(
            ValueError,
            "unsupported causal state",
        ):
            _validated_cognitive_assurance(
                {
                    "causal_state": "MAGIC",
                    "summary": "invalid",
                    "findings": ["invalid"],
                }
            )

    def test_cognitive_result_requires_evidence_findings(self):
        with self.assertRaisesRegex(
            ValueError,
            "findings",
        ):
            _validated_cognitive_assurance(
                {
                    "causal_state": "SATISFIED",
                    "summary": "looks correct",
                    "findings": None,
                }
            )

    def test_quinn_prompt_uses_bounded_contract_context(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture_sha = self._prepare_quinn_cognition_fixture(root)

            prompt = _quinn_cognitive_prompt(
                mission={
                    "objective": "verify semantic correctness",
                    "acceptance_criteria": ["behavior matches objective"],
                    "allowed_paths": ["app.py"],
                    "engineering_evidence": ["X" * 50000],
                },
                review_root=root,
                branch="rvsc/review",
                commit_sha=fixture_sha,
            )

        self.assertIn("QUINN CONTRACT", prompt)
        self.assertIn("MAX DISCIPLINE", prompt)
        self.assertIn("verify semantic correctness", prompt)
        self.assertIn("[TRUNCATED]", prompt)
        self.assertLess(len(prompt), 35000)

    @patch("controller.generic_qa_worker._quinn_provider_call")
    def test_provider_backed_cognition_returns_structured_assurance(
        self, provider
    ):
        provider.return_value = (
            {
                "id": "qa-provider-1",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": (
                                    '{"causal_state":"SATISFIED",'
                                    '"summary":"objective satisfied",'
                                    '"findings":["evidence is sufficient"]}'
                                ),
                            }
                        ],
                    }
                ],
            },
            "ollama",
        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture_sha = self._prepare_quinn_cognition_fixture(root)

            from controller.generic_qa_worker import _cognitive_assurance

            result = _cognitive_assurance(
                mission={
                    "objective": "review work",
                    "allowed_paths": ["app.py"],
                },
                review_root=root,
                branch="rvsc/review",
                commit_sha=fixture_sha,
            )

        self.assertEqual(result["causal_state"], "SATISFIED")
        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(result["provider_response_id"], "qa-provider-1")
        self.assertGreater(result["prompt_chars"], 0)


    def test_authoritative_knowledge_is_bounded_and_provenanced(self):
        root = self.base / "knowledge-repo"
        root.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Fixture"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "fixture@example.invalid"],
            cwd=root,
            check=True,
        )

        files = {
            "governance/AUTHORITATIVE_KNOWLEDGE_HIERARCHY.md":
                "Human/company authority remains supreme.\n",
            "governance/SOURCE_ISOLATION.md":
                "Allowed paths are bounded and agents cannot expand scope.\n",
            "governance/WORK_PACKAGE_LIFECYCLE.md":
                "Independent QA is required before acceptance.\n",
            "docs/ORCHESTRATION_ARCHITECTURE.md":
                "RVSC orchestration routes QA-001 Quinn independently.\n",
            "config/agents.yaml":
                "agents:\n  QA-001:\n    name: Quinn\n",
            "config/orchestration.yaml":
                "qa: QA-001\n",
            "config/repositories.yaml":
                "rvsc: GitSly1/RAMTech-RVSC-Control-Center\n",
            "PROJECT_REGISTRY.md":
                "RVSC current project registry.\n",
            "ROADMAP.md":
                "RVSC roadmap projection.\n",
            "COMMAND_DASHBOARD.md":
                "RVSC command dashboard projection.\n",
            "SPRINT_DASHBOARD.md":
                "RVSC sprint dashboard projection.\n",
        }

        for relative_path, content in files.items():
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "knowledge fixture"],
            cwd=root,
            check=True,
            capture_output=True,
        )

        mission = {
            "project": "RVSC",
            "repository": "GitSly1/RAMTech-RVSC-Control-Center",
            "objective": "Verify Quinn independent QA orchestration",
            "acceptance_criteria": ["QA remains independent"],
            "allowed_paths": ["controller/generic_qa_worker.py"],
            "changed_files": ["controller/generic_qa_worker.py"],
        }

        context = _authoritative_knowledge_context(mission, root)

        self.assertLessEqual(
            context["used_chars"],
            context["budget_chars"],
        )
        self.assertTrue(context["sources"])

        for source in context["sources"]:
            self.assertIn(source["authority_class"], {"A1", "A2", "A5"})
            self.assertTrue(source["path"])
            self.assertRegex(source["revision"], r"^[0-9a-f]{40}$")
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
            self.assertIsInstance(source["truncated"], bool)
            self.assertLessEqual(source["excerpt_chars"], 3000)

    def test_authoritative_knowledge_requires_governance_sources(self):
        root = self.base / "missing-governance"
        root.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Fixture"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "fixture@example.invalid"],
            cwd=root,
            check=True,
        )
        (root / "placeholder.txt").write_text("rvsc\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "fixture"],
            cwd=root,
            check=True,
            capture_output=True,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "required authoritative knowledge source missing",
        ):
            _authoritative_knowledge_context(
                {"project": "RVSC", "objective": "QA"},
                root,
            )

    def test_quinn_prompt_contains_authority_provenance_not_repository_dump(self):
        root = self.base / "prompt-knowledge"
        root.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Fixture"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "fixture@example.invalid"],
            cwd=root,
            check=True,
        )

        fixture_files = {
            "golden-core/QA_001_QUINN_COGNITION_CONTRACT_V1.md":
                "Quinn independent assurance.",
            "golden-core/MAX_PLATINUM_ENGINEERING_CORE_V1.md":
                "Human authority is supreme.",
            "governance/AUTHORITATIVE_KNOWLEDGE_HIERARCHY.md":
                "A1 governance controls projections.",
            "governance/SOURCE_ISOLATION.md":
                "Scope is bounded.",
            "governance/WORK_PACKAGE_LIFECYCLE.md":
                "QA is independent.",
            "COMMAND_DASHBOARD.md":
                "RVSC dashboard says executing.",
        }
        for relative_path, content in fixture_files.items():
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "prompt fixture"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

        prompt = _quinn_cognitive_prompt(
            mission={
                "project": "RVSC",
                "repository": "RAMTech-RVSC-Control-Center",
                "objective": "Review RVSC dashboard execution claim",
                "acceptance_criteria": ["Use authoritative evidence"],
                "allowed_paths": ["COMMAND_DASHBOARD.md"],
                "changed_files": ["COMMAND_DASHBOARD.md"],
            },
            review_root=root,
            branch="rvsc/test",
            commit_sha=sha,
        )

        self.assertIn("AUTHORITATIVE INSTITUTIONAL KNOWLEDGE", prompt)
        self.assertIn('"authority_class": "A1"', prompt)
        self.assertIn('"path": "governance/SOURCE_ISOLATION.md"', prompt)
        self.assertIn('"revision":', prompt)
        self.assertIn('"sha256":', prompt)
        self.assertIn('"truncated":', prompt)
        self.assertIn("COMMAND_DASHBOARD.md", prompt)

    def test_quinn_schema_is_qa_owned_not_engineering_proposal(self):
        from controller.generic_qa_worker import _quinn_assurance_schema

        schema = _quinn_assurance_schema()

        self.assertEqual(
            set(schema["required"]),
            {
                "causal_state",
                "summary",
                "findings",
            },
        )

        properties = schema["properties"]

        self.assertIn("causal_state", properties)
        self.assertNotIn("classification", properties)
        self.assertIn("summary", properties)
        self.assertIn("findings", properties)

        self.assertNotIn("edits", properties)
        self.assertNotIn("commit_message", properties)
        self.assertNotIn("engineering_summary", properties)


if __name__ == "__main__":
    unittest.main()
