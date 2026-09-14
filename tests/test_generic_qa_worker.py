from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from controller.generic_qa_worker import _acceptance_authority_gate, _authoritative_epistemic_facts, _authoritative_knowledge_context, _classification_consistency_guard, _epistemic_consistency_guard, _quinn_cognitive_prompt, _repo_root, _validated_cognitive_assurance, execute_mission




class QuinnAuthoritativeEpistemicPreservationTests(unittest.TestCase):
    """Controller-established material facts must survive into cognition."""

    def _base_cognition(self, observed=None):
        fact = "ordinary positive implementation evidence"

        return {
            "observed_facts": (
                list(observed)
                if observed is not None
                else [fact]
            ),
            "missing_facts": [],
            "supported_inferences": [],
            "unsupported_inferences": [],
            "causal_owner": "NONE",
            "causal_justification": fact,
            "causal_evidence_refs": [fact],
            "causal_state": "SATISFIED",
            "classification": "QA_ACCEPTED",
            "summary": fact,
            "findings": [fact],
        }

    def test_boundary_fact_is_canonical_and_material(self):
        mission = {
            "allowed_paths": [
                "controller/generic_qa_worker.py"
            ],
            "changed_files": [
                "controller/generic_qa_worker.py",
                (
                    "golden-core/"
                    "QA_001_QUINN_COGNITION_CONTRACT_V1.md"
                ),
            ],
        }

        facts = _authoritative_epistemic_facts(
            mission
        )

        self.assertEqual(
            facts,
            (
                'authority_boundary:scope_compliant=false;'
                'unauthorized_changed_files=["golden-core/'
                'QA_001_QUINN_COGNITION_CONTRACT_V1.md"]',
            ),
        )

    def test_environment_fact_is_canonical_and_material(self):
        mission = {
            "validation_results": {
                "environment_ready": False,
                "harness_integrity": True,
            }
        }

        self.assertEqual(
            _authoritative_epistemic_facts(
                mission
            ),
            (
                "validation_results:"
                "environment_ready=false;"
                "harness_integrity=true",
            ),
        )

    def test_harness_fact_is_canonical_and_material(self):
        mission = {
            "validation_results": {
                "environment_ready": True,
                "harness_integrity": False,
            }
        }

        self.assertEqual(
            _authoritative_epistemic_facts(
                mission
            ),
            (
                "validation_results:"
                "harness_integrity=false;"
                "environment_ready!=false",
            ),
        )

    def test_missing_a4_contract_fact_is_not_retyped_as_observed(self):
        mission = {
            "contract_assessment": {
                "complete": False,
                "blockers": [
                    {
                        "type": "MISSING_REQUIRED_CONTRACT_INPUT",
                        "authority_class": "A4",
                        "name": "approved_value",
                    }
                ],
            }
        }

        self.assertEqual(
            _authoritative_epistemic_facts(
                mission
            ),
            (),
        )

        cognition = {
            "observed_facts": [
                "implementation evidence is present"
            ],
            "missing_facts": [
                "approved value is absent"
            ],
            "supported_inferences": [
                "required A4 value is unavailable"
            ],
            "unsupported_inferences": [],
            "causal_owner": "CONTRACT",
            "causal_justification": (
                "required A4 value is unavailable"
            ),
            "causal_evidence_refs": [
                "required A4 value is unavailable"
            ],
            "causal_state": "CONTRACT_BLOCKER",
            "classification": "QA_BLOCKED_CONTRACT",
            "summary": "required contract value is absent",
            "findings": [
                "required contract input is missing"
            ],
        }

        self.assertIs(
            _epistemic_consistency_guard(
                mission,
                cognition,
            ),
            cognition,
        )

    def test_no_material_controller_blocker_requires_no_fact(self):
        mission = {
            "allowed_paths": ["source.py"],
            "changed_files": ["source.py"],
            "validation_results": {
                "environment_ready": True,
                "harness_integrity": True,
            },
            "contract_assessment": {
                "complete": True,
                "blockers": [],
            },
        }

        self.assertEqual(
            _authoritative_epistemic_facts(
                mission
            ),
            (),
        )

    def test_guard_rejects_omitted_boundary_fact(self):
        mission = {
            "allowed_paths": ["source.py"],
            "changed_files": [
                "source.py",
                "outside.py",
            ],
        }

        cognition = self._base_cognition()

        with self.assertRaisesRegex(
            ValueError,
            "omitted authoritative structured fact",
        ):
            _epistemic_consistency_guard(
                mission,
                cognition,
            )

    def test_guard_accepts_exact_boundary_preservation(self):
        mission = {
            "allowed_paths": ["source.py"],
            "changed_files": [
                "source.py",
                "outside.py",
            ],
        }

        required = (
            _authoritative_epistemic_facts(
                mission
            )
        )

        cognition = self._base_cognition(
            observed=[
                "ordinary positive implementation evidence",
                *required,
            ]
        )

        self.assertIs(
            _epistemic_consistency_guard(
                mission,
                cognition,
            ),
            cognition,
        )

    def test_guard_matrix_rejects_each_omitted_controller_fact(self):
        missions = (
            {
                "allowed_paths": ["source.py"],
                "changed_files": [
                    "source.py",
                    "outside.py",
                ],
            },
            {
                "validation_results": {
                    "environment_ready": False,
                    "harness_integrity": True,
                },
            },
            {
                "validation_results": {
                    "environment_ready": True,
                    "harness_integrity": False,
                },
            },
        )

        for mission in missions:
            with self.subTest(mission=mission):
                self.assertTrue(
                    _authoritative_epistemic_facts(
                        mission
                    )
                )

                with self.assertRaisesRegex(
                    ValueError,
                    "omitted authoritative structured fact",
                ):
                    _epistemic_consistency_guard(
                        mission,
                        self._base_cognition(),
                    )

    def test_prompt_exposes_exact_authoritative_fact_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            core = root / "golden-core"
            governance = root / "governance"

            core.mkdir(parents=True)
            governance.mkdir(parents=True)

            (
                core
                / "QA_001_QUINN_COGNITION_CONTRACT_V1.md"
            ).write_text(
                "QUINN CONTRACT",
                encoding="utf-8",
            )

            (
                core
                / "MAX_PLATINUM_ENGINEERING_CORE_V1.md"
            ).write_text(
                "MAX DISCIPLINE",
                encoding="utf-8",
            )

            (
                governance
                / "AUTHORITATIVE_KNOWLEDGE_HIERARCHY.md"
            ).write_text(
                "Governance authority.",
                encoding="utf-8",
            )

            (
                governance
                / "SOURCE_ISOLATION.md"
            ).write_text(
                "Source isolation.",
                encoding="utf-8",
            )

            (
                governance
                / "WORK_PACKAGE_LIFECYCLE.md"
            ).write_text(
                "Lifecycle.",
                encoding="utf-8",
            )

            subprocess.run(
                ["git", "init", "-b", "main"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "config",
                    "user.name",
                    "Fixture",
                ],
                cwd=root,
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "config",
                    "user.email",
                    "fixture@example.invalid",
                ],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "add", "."],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "fixture"],
                cwd=root,
                check=True,
                capture_output=True,
            )

            mission = {
                "project": "rvsc",
                "repository": (
                    "GitSly1/"
                    "RAMTech-RVSC-Control-Center"
                ),
                "objective": "Qualify work.",
                "acceptance_criteria": [
                    "Behavior is correct."
                ],
                "allowed_paths": ["source.py"],
                "changed_files": [
                    "source.py",
                    "outside.py",
                ],
            }

            prompt = _quinn_cognitive_prompt(
                mission=mission,
                review_root=root,
                branch="rvsc/test",
                commit_sha="a" * 40,
                authority_root=root,
            )

            required = (
                _authoritative_epistemic_facts(
                    mission
                )[0]
            )

            import json

            bounded_prefix = (
                "BOUNDED REVIEW CONTEXT:\n"
            )
            bounded_suffix = (
                "\n\nCURRENT DECISION AUTHORITY CONTEXT:"
            )

            bounded_text = prompt.split(
                bounded_prefix,
                1,
            )[1].split(
                bounded_suffix,
                1,
            )[0]

            bounded = json.loads(
                bounded_text
            )

            self.assertEqual(
                bounded[
                    "evidence_context"
                ][
                    "authoritative_epistemic_facts"
                ],
                [required],
            )

            self.assertIn(
                "AUTHORITATIVE EPISTEMIC PRESERVATION RULE:",
                prompt,
            )
            self.assertIn(
                "MUST be copied verbatim into observed_facts",
                prompt,
            )

    def test_contract_contains_authoritative_preservation_invariant(self):
        content = Path(
            "golden-core/"
            "QA_001_QUINN_COGNITION_CONTRACT_V1.md"
        ).read_text(
            encoding="utf-8-sig",
        )

        self.assertIn(
            "### Authoritative epistemic preservation invariant",
            content,
        )
        self.assertIn(
            "AUTHORITATIVE_EPISTEMIC_FACTS SUBSET_OF OBSERVED_FACTS",
            content,
        )
        self.assertIn(
            "Membership uses exact string identity.",
            content,
        )


class QuinnCausalEvidenceBindingRegressionTests(unittest.TestCase):
    """Exact epistemic-to-causal evidence binding."""

    def _boundary_cognition(self):
        fact = (
            "boundary_assessment: unauthorized_changed_files contains "
            "golden-core/QA_001_QUINN_COGNITION_CONTRACT_V1.md"
        )
        return {
            "observed_facts": [fact],
            "missing_facts": [],
            "supported_inferences": [],
            "unsupported_inferences": [],
            "causal_owner": "AUTHORITY_BOUNDARY",
            "causal_justification": (
                "The finalized boundary fact proves the causal conclusion."
            ),
            "causal_evidence_refs": [fact],
            "causal_state": "BOUNDARY_BLOCKER",
            "summary": "Delegated repository scope was exceeded.",
            "findings": [
                "A changed file is outside delegated scope."
            ],
        }

    def test_exact_observed_fact_reference_is_accepted(self):
        cognition = self._boundary_cognition()

        validated = _validated_cognitive_assurance(
            cognition
        )

        self.assertEqual(
            validated["causal_evidence_refs"],
            cognition["observed_facts"],
        )
        self.assertEqual(
            validated["classification"],
            "QA_BLOCKED_BOUNDARY",
        )

    def test_semantic_paraphrase_is_rejected(self):
        cognition = self._boundary_cognition()

        cognition["causal_evidence_refs"] = [
            "The Quinn cognition contract is outside authorized scope."
        ]

        with self.assertRaisesRegex(
            ValueError,
            "claims outside observed_facts/supported_inferences",
        ):
            _validated_cognitive_assurance(
                cognition
            )

    def test_exact_supported_inference_reference_is_accepted(self):
        cognition = self._boundary_cognition()

        inference = (
            "The unauthorized changed file establishes "
            "an authority-boundary blocker."
        )

        cognition["supported_inferences"] = [
            inference
        ]
        cognition["causal_evidence_refs"] = [
            inference
        ]

        validated = _validated_cognitive_assurance(
            cognition
        )

        self.assertEqual(
            validated["causal_evidence_refs"],
            [inference],
        )

    def test_runtime_prompt_contains_select_copy_procedure(self):
        source = Path(
            "controller/generic_qa_worker.py"
        ).read_text(
            encoding="utf-8-sig",
        )

        self.assertIn(
            "CAUSAL EVIDENCE BINDING RULE:",
            source,
        )
        self.assertIn(
            "Use SELECT -> COPY.",
            source,
        )
        self.assertIn(
            "verbatim into causal_evidence_refs",
            source,
        )
        self.assertIn(
            "Membership is exact string",
            source,
        )

    def test_contract_contains_binding_invariant(self):
        content = Path(
            "golden-core/"
            "QA_001_QUINN_COGNITION_CONTRACT_V1.md"
        ).read_text(
            encoding="utf-8-sig",
        )

        self.assertIn(
            "### Causal evidence binding invariant",
            content,
        )
        self.assertIn(
            "Membership uses exact string identity.",
            content,
        )
        self.assertIn(
            "`SELECT -> COPY`",
            content,
        )
        self.assertIn(
            "must not regenerate, summarize, rewrite, "
            "normalize, or paraphrase",
            content,
        )

    def test_complete_causal_domain_matrix_preserves_binding(self):
        cases = (
            (
                "NONE",
                "SATISFIED",
                "QA_ACCEPTED",
            ),
            (
                "IMPLEMENTATION",
                "IMPLEMENTATION_DEFECT",
                "QA_REJECTED_IMPLEMENTATION",
            ),
            (
                "REQUIREMENT",
                "REQUIREMENT_DEFECT",
                "QA_REJECTED_REQUIREMENT",
            ),
            (
                "CONTRACT",
                "CONTRACT_BLOCKER",
                "QA_BLOCKED_CONTRACT",
            ),
            (
                "VALIDATION_HARNESS",
                "HARNESS_BLOCKER",
                "QA_BLOCKED_HARNESS",
            ),
            (
                "ENVIRONMENT",
                "ENVIRONMENT_BLOCKER",
                "QA_BLOCKED_ENVIRONMENT",
            ),
            (
                "AUTHORITY_BOUNDARY",
                "BOUNDARY_BLOCKER",
                "QA_BLOCKED_BOUNDARY",
            ),
            (
                "EVIDENCE",
                "EVIDENCE_BLOCKER",
                "QA_BLOCKED_EVIDENCE",
            ),
        )

        for owner, state, classification in cases:
            with self.subTest(
                owner=owner,
                state=state,
            ):
                fact = (
                    "finalized epistemic evidence for "
                    + state
                )

                cognition = {
                    "observed_facts": [fact],
                    "missing_facts": [],
                    "supported_inferences": [],
                    "unsupported_inferences": [],
                    "causal_owner": owner,
                    "causal_justification": fact,
                    "causal_evidence_refs": [fact],
                    "causal_state": state,
                    "summary": fact,
                    "findings": [fact],
                }

                validated = (
                    _validated_cognitive_assurance(
                        cognition
                    )
                )

                self.assertEqual(
                    validated[
                        "causal_evidence_refs"
                    ],
                    [fact],
                )

                self.assertEqual(
                    validated["classification"],
                    classification,
                )

    def test_failed_binding_does_not_create_classification(self):
        cognition = self._boundary_cognition()

        cognition["causal_evidence_refs"] = [
            "paraphrased evidence"
        ]

        self.assertNotIn(
            "classification",
            cognition,
        )

        with self.assertRaises(ValueError):
            _validated_cognitive_assurance(
                cognition
            )

        self.assertNotIn(
            "classification",
            cognition,
        )

class GenericQAWorkerTests(unittest.TestCase):
    def test_contract_blocker_requires_contract_classification(self):
        mission = {
            "validation_results": {
                "environment_ready": True,
                "harness_integrity": True,
            },
            "contract_assessment": {
                "complete": False,
                "declared": True,
                "blockers": [
                    {
                        "type": "MISSING_REQUIRED_CONTRACT_INPUT",
                        "authority_class": "A4",
                        "name": "approved_value",
                    }
                ],
            },
        }

        cognitive = {
            "causal_state": "REQUIREMENT_DEFECT",
            "classification": "QA_REJECTED_REQUIREMENT",
            "summary": "missing contract value",
            "findings": [
                "approved value absent"
            ],
        }

        with self.assertRaisesRegex(
            ValueError,
            "explicit contract blocker",
        ):
            _classification_consistency_guard(
                mission,
                cognitive,
            )

    def test_contract_blocker_accepts_contract_classification(self):
        mission = {
            "validation_results": {
                "environment_ready": True,
                "harness_integrity": True,
            },
            "contract_assessment": {
                "complete": False,
                "declared": True,
                "blockers": [
                    {
                        "type": "MISSING_REQUIRED_CONTRACT_INPUT",
                        "authority_class": "A4",
                        "name": "approved_value",
                    }
                ],
            },
        }

        cognitive = {
            "causal_state": "CONTRACT_BLOCKER",
            "classification": "QA_BLOCKED_CONTRACT",
            "summary": "active contract is incomplete",
            "findings": [
                "approved value absent"
            ],
        }

        self.assertEqual(
            _classification_consistency_guard(
                mission,
                cognitive,
            ),
            cognitive,
        )

    def test_no_declared_contract_blocker_does_not_override_cognition(self):
        mission = {
            "validation_results": {
                "environment_ready": True,
                "harness_integrity": True,
            },
            "contract_assessment": {
                "complete": True,
                "declared": False,
                "blockers": [],
            },
        }

        cognitive = {
            "causal_state": "IMPLEMENTATION_DEFECT",
            "classification": "QA_REJECTED_IMPLEMENTATION",
            "summary": "implementation violated requirement",
            "findings": [
                "observable implementation defect"
            ],
        }

        self.assertEqual(
            _classification_consistency_guard(
                mission,
                cognitive,
            ),
            cognitive,
        )

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
            "objective": "Verify the reviewed source update.",
            "acceptance_criteria": [
                "The required source behavior is verified."
            ],
            "requires_semantic_acceptance": True,
            "acceptance_checks": [
                {
                    "criterion_index": 1,
                    "type": "validation_passed",
                    "name": "content",
                }
            ],
            "allowed_paths": ["source.py"],
            "validation_commands": [{"name": "content", "argv": [sys.executable, "-c", "from pathlib import Path; assert Path('source.py').read_text() == 'VALUE = 2\\n'"]}],
            "engineering_evidence": [
                "semantic_acceptance:criterion:1:validation:content",
                "semantic_acceptance:criteria_verified:1",
                "semantic_acceptance:passed",
            ],
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
            "observed_facts": ["objective and acceptance evidence are sufficient"],
            "missing_facts": [],
            "supported_inferences": [],
            "unsupported_inferences": [],
            "causal_owner": "NONE",
            "causal_justification": "objective and acceptance evidence are sufficient",
            "causal_evidence_refs": ["objective and acceptance evidence are sufficient"],
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
            "observed_facts": ["required implementation behavior is absent"],
            "missing_facts": [],
            "supported_inferences": [],
            "unsupported_inferences": [],
            "causal_owner": "IMPLEMENTATION",
            "causal_justification": "required implementation behavior is absent",
            "causal_evidence_refs": ["required implementation behavior is absent"],
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

    def test_harness_blocker_requires_harness_classification(self):
        mission = {
            "validation_results": {
                "environment_ready": True,
                "harness_integrity": False,
            }
        }
        cognitive = {
            "causal_state": "REQUIREMENT_DEFECT",
            "classification": "QA_REJECTED_REQUIREMENT",
            "summary": "validation mechanism failed",
            "findings": [
                "prescribed validation mechanism is defective"
            ],
        }

        with self.assertRaisesRegex(
            ValueError,
            "explicit harness blocker",
        ):
            _classification_consistency_guard(
                mission,
                cognitive,
            )

    def test_harness_blocker_accepts_harness_classification(self):
        mission = {
            "validation_results": {
                "environment_ready": True,
                "harness_integrity": False,
            }
        }
        cognitive = {
            "causal_state": "HARNESS_BLOCKER",
            "classification": "QA_BLOCKED_HARNESS",
            "summary": "validation mechanism is defective",
            "findings": [
                "prescribed validation mechanism cannot establish correctness"
            ],
        }

        self.assertIs(
            _classification_consistency_guard(
                mission,
                cognitive,
            ),
            cognitive,
        )

    def test_consistency_guard_does_not_infer_harness_without_explicit_state(self):
        mission = {
            "validation_results": {
                "environment_ready": True,
            }
        }
        cognitive = {
            "causal_state": "REQUIREMENT_DEFECT",
            "classification": "QA_REJECTED_REQUIREMENT",
            "summary": "judgment remains cognitive",
            "findings": [
                "no explicit harness blocker"
            ],
        }

        self.assertIs(
            _classification_consistency_guard(
                mission,
                cognitive,
            ),
            cognitive,
        )

    def test_environment_unavailable_takes_precedence_over_harness_integrity_false(self):
        mission = {
            "validation_results": {
                "environment_ready": False,
                "harness_integrity": False,
            }
        }
        cognitive = {
            "causal_state": "ENVIRONMENT_BLOCKER",
            "classification": "QA_BLOCKED_ENVIRONMENT",
            "summary": "environment is unavailable",
            "findings": [
                "external runtime prevents defensible validation"
            ],
        }

        self.assertIs(
            _classification_consistency_guard(
                mission,
                cognitive,
            ),
            cognitive,
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

    def test_acceptance_authority_blocks_satisfied_without_semantic_evidence(self):
        mission = {
            "objective": "review work",
            "acceptance_criteria": ["behavior is correct"],
            "allowed_paths": ["source.py"],
        }

        authority = _acceptance_authority_gate(mission)

        self.assertFalse(authority["eligible"])
        self.assertEqual(
            authority["classification"],
            "QA_BLOCKED_EVIDENCE",
        )

    def test_acceptance_authority_requires_every_criterion_evidence(self):
        mission = {
            "requires_semantic_acceptance": True,
            "acceptance_criteria": [
                "criterion one",
                "criterion two",
            ],
            "acceptance_checks": [
                {
                    "criterion_index": 1,
                    "type": "validation_passed",
                    "name": "tests",
                },
                {
                    "criterion_index": 2,
                    "type": "path_changed",
                    "path": "source.py",
                },
            ],
            "engineering_evidence": [
                "semantic_acceptance:criterion:1:validation:tests",
                "semantic_acceptance:criteria_verified:2",
                "semantic_acceptance:passed",
            ],
        }

        authority = _acceptance_authority_gate(mission)

        self.assertFalse(authority["eligible"])
        self.assertTrue(
            any(
                "criterion 2" in reason
                for reason in authority["reasons"]
            )
        )

    def test_acceptance_authority_accepts_controller_semantic_evidence(self):
        mission = {
            "requires_semantic_acceptance": True,
            "acceptance_criteria": [
                "criterion one",
                "criterion two",
            ],
            "acceptance_checks": [
                {
                    "criterion_index": 1,
                    "type": "validation_passed",
                    "name": "tests",
                },
                {
                    "criterion_index": 2,
                    "type": "path_changed",
                    "path": "source.py",
                },
            ],
            "engineering_evidence": [
                "semantic_acceptance:criterion:1:validation:tests",
                "semantic_acceptance:criterion:2:path_changed:source.py",
                "semantic_acceptance:criteria_verified:2",
                "semantic_acceptance:passed",
            ],
        }

        authority = _acceptance_authority_gate(mission)

        self.assertTrue(authority["eligible"])
        self.assertEqual(
            authority["classification"],
            "QA_ACCEPTED",
        )
        self.assertEqual(authority["reasons"], [])

    @patch("controller.generic_qa_worker._cognitive_assurance")
    def test_satisfied_cognition_cannot_bypass_acceptance_authority(
        self,
        cognitive,
    ):
        cognitive.return_value = {
            "observed_facts": ["supplied tests passed"],
            "missing_facts": [],
            "supported_inferences": [],
            "unsupported_inferences": [],
            "causal_owner": "NONE",
            "causal_justification": "supplied tests passed",
            "causal_evidence_refs": ["supplied tests passed"],
            "causal_state": "SATISFIED",
            "summary": "tests passed",
            "findings": ["supplied tests passed"],
        }

        mission = self.mission()
        mission["requires_semantic_acceptance"] = False
        mission.pop("acceptance_checks")
        mission.pop("engineering_evidence")

        result = self.execute(mission)

        self.assertFalse(result["success"])
        self.assertEqual(result["verdict"], "QA_REJECTED")
        self.assertEqual(
            result["cognitive_classification"],
            "QA_ACCEPTED",
        )
        self.assertEqual(
            result["acceptance_classification"],
            "QA_BLOCKED_EVIDENCE",
        )
        self.assertFalse(
            result["acceptance_authority"]["eligible"]
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
                        "observed_facts": ["evidence-backed finding"],
                        "missing_facts": [],
                        "supported_inferences": [],
                        "unsupported_inferences": [],
                        "causal_owner": {
                            "SATISFIED": "NONE",
                            "IMPLEMENTATION_DEFECT": "IMPLEMENTATION",
                            "REQUIREMENT_DEFECT": "REQUIREMENT",
                            "CONTRACT_BLOCKER": "CONTRACT",
                            "HARNESS_BLOCKER": "VALIDATION_HARNESS",
                            "ENVIRONMENT_BLOCKER": "ENVIRONMENT",
                            "BOUNDARY_BLOCKER": "AUTHORITY_BOUNDARY",
                            "EVIDENCE_BLOCKER": "EVIDENCE",
                        }[causal_state],
                        "causal_justification": "evidence-backed finding",
                        "causal_evidence_refs": ["evidence-backed finding"],
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
                    "observed_facts": ["invalid causal state was returned"],
                    "missing_facts": [],
                    "supported_inferences": [],
                    "unsupported_inferences": [],
                    "causal_owner": "NONE",
                    "causal_justification": "invalid causal state was returned",
                    "causal_evidence_refs": ["invalid causal state was returned"],
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
                    "observed_facts": ["objective appears correct"],
                    "missing_facts": [],
                    "supported_inferences": [],
                    "unsupported_inferences": [],
                    "causal_owner": "NONE",
                    "causal_justification": "objective appears correct",
                    "causal_evidence_refs": ["objective appears correct"],
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
                authority_root=root,
            )

        self.assertIn("QUINN CONTRACT", prompt)
        self.assertIn("MAX DISCIPLINE", prompt)
        self.assertIn("verify semantic correctness", prompt)
        self.assertIn("[TRUNCATED]", prompt)
        self.assertLess(len(prompt), 35000)

    @patch("controller.generic_qa_worker.urllib.request.urlopen")
    def test_quinn_ollama_call_owns_shared_context_capacity(self, urlopen):
        import json
        from unittest.mock import Mock

        from controller.generic_qa_worker import _quinn_ollama_call

        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(
            {
                "model": "qwen2.5-coder:7b-instruct",
                "response": "{}",
            }
        ).encode("utf-8")

        urlopen.return_value = response

        _quinn_ollama_call("bounded QA prompt")

        request = urlopen.call_args.args[0]
        payload = json.loads(
            request.data.decode("utf-8")
        )

        self.assertEqual(
            payload["options"],
            {"num_ctx": 32768},
        )
        self.assertEqual(
            payload["model"],
            "qwen2.5-coder:7b-instruct",
        )
        self.assertFalse(
            payload["stream"]
        )
        self.assertIn(
            "format",
            payload,
        )

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
                                    '{"observed_facts":["evidence supplied"],'
                                    '"missing_facts":[],'
                                    '"supported_inferences":["objective supported"],'
                                    '"unsupported_inferences":[],'
                                    '"causal_state":"SATISFIED",'
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
                authority_root=root,
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

        context = _authoritative_knowledge_context(mission, root, authority_root=root)

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

    def test_authoritative_knowledge_reserves_cross_authority_coverage(self):
        from controller.generic_qa_worker import _authoritative_knowledge_context

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            core = root / "golden-core"
            governance = root / "governance"
            docs = root / "docs"
            config = root / "config"

            core.mkdir()
            governance.mkdir()
            docs.mkdir()
            config.mkdir()

            (governance / "AUTHORITATIVE_KNOWLEDGE_HIERARCHY.md").write_text(
                "governance " * 2000,
                encoding="utf-8",
            )
            (governance / "SOURCE_ISOLATION.md").write_text(
                "isolation " * 1000,
                encoding="utf-8",
            )
            (governance / "WORK_PACKAGE_LIFECYCLE.md").write_text(
                "lifecycle " * 1000,
                encoding="utf-8",
            )
            (docs / "ORCHESTRATION_ARCHITECTURE.md").write_text(
                "RVSC controller mission architecture",
                encoding="utf-8",
            )
            (config / "agents.yaml").write_text(
                "RVSC Quinn agent mission",
                encoding="utf-8",
            )
            (root / "PROJECT_REGISTRY.md").write_text(
                "RVSC mission operational projection",
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
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "fixture"],
                cwd=root,
                check=True,
                capture_output=True,
            )

            result = _authoritative_knowledge_context(
                {
                    "project": "RVSC",
                    "objective": (
                        "Review controller orchestration architecture and "
                        "current qualification readiness status"
                    ),
                    "acceptance_criteria": [
                        "Verify control-plane architecture evidence",
                        "Resolve roadmap and readiness projections",
                    ],
                },
                root,
                authority_root=root,
            )

            classes = {
                source["authority_class"]
                for source in result["sources"]
            }

            self.assertIn("A1", classes)
            self.assertIn("A2", classes)
            self.assertIn("A5", classes)
            self.assertLessEqual(result["used_chars"], 6000)

    def test_quinn_prompt_identifies_a3_a4_decision_context(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixture_sha = self._prepare_quinn_cognition_fixture(root)

            prompt = _quinn_cognitive_prompt(
                mission={
                    "project": "RVSC",
                    "repository": "RAMTech-RVSC-Control-Center",
                    "objective": "evaluate evidence",
                    "acceptance_criteria": ["evidence-backed disposition"],
                    "engineering_evidence": ["tests:pass"],
                    "validation_results": {"tests": True},
                },
                review_root=root,
                branch="rvsc/review",
                commit_sha=fixture_sha,
                authority_root=root,
            )

            self.assertIn(
                "CURRENT DECISION AUTHORITY CONTEXT",
                prompt,
            )
            self.assertIn(
                "A3 = engineering/validation evidence already supplied",
                prompt,
            )
            self.assertIn(
                "A4 = active mission contract already supplied",
                prompt,
            )

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
                authority_root=root,
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
            authority_root=root,
        )

        self.assertIn("AUTHORITATIVE INSTITUTIONAL KNOWLEDGE", prompt)
        self.assertIn('"authority_class": "A1"', prompt)
        self.assertIn('"path": "governance/SOURCE_ISOLATION.md"', prompt)
        self.assertIn('"revision":', prompt)
        self.assertIn('"sha256":', prompt)
        self.assertIn('"truncated":', prompt)
        self.assertIn("COMMAND_DASHBOARD.md", prompt)


    def test_target_repository_cannot_override_trusted_quinn_authority(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            authority = base / "authority"
            target = base / "target"
            authority.mkdir()
            target.mkdir()

            for repo in (authority, target):
                subprocess.run(
                    ["git", "init", "-b", "main"],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "config", "user.name", "Fixture"],
                    cwd=repo,
                    check=True,
                )
                subprocess.run(
                    ["git", "config", "user.email", "fixture@example.invalid"],
                    cwd=repo,
                    check=True,
                )

            trusted = {
                "golden-core/QA_001_QUINN_COGNITION_CONTRACT_V1.md":
                    "TRUSTED_QUINN_AUTHORITY",
                "golden-core/MAX_PLATINUM_ENGINEERING_CORE_V1.md":
                    "TRUSTED_MAX_AUTHORITY",
                "governance/AUTHORITATIVE_KNOWLEDGE_HIERARCHY.md":
                    "TRUSTED_A1_HIERARCHY",
                "governance/SOURCE_ISOLATION.md":
                    "TRUSTED_SOURCE_ISOLATION",
                "governance/WORK_PACKAGE_LIFECYCLE.md":
                    "TRUSTED_WORK_PACKAGE_LIFECYCLE",
            }

            hostile = {
                "golden-core/QA_001_QUINN_COGNITION_CONTRACT_V1.md":
                    "HOSTILE_TARGET_QUINN",
                "golden-core/MAX_PLATINUM_ENGINEERING_CORE_V1.md":
                    "HOSTILE_TARGET_MAX",
                "governance/AUTHORITATIVE_KNOWLEDGE_HIERARCHY.md":
                    "HOSTILE_TARGET_GOVERNANCE",
                "source.py":
                    "VALUE = 1\n",
            }

            for relative, content in trusted.items():
                path = authority / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            for relative, content in hostile.items():
                path = target / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            for repo in (authority, target):
                subprocess.run(["git", "add", "."], cwd=repo, check=True)
                subprocess.run(
                    ["git", "commit", "-m", "fixture"],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                )

            target_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=target,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            prompt = _quinn_cognitive_prompt(
                mission={
                    "project": "SemantiQ",
                    "repository": "RAMTech-SEMANTIQ",
                    "objective": "verify target evidence",
                    "acceptance_criteria": ["authority remains independent"],
                    "allowed_paths": ["source.py"],
                    "engineering_evidence": ["TARGET_EVIDENCE_PRESENT"],
                },
                review_root=target,
                branch="rvsc/review",
                commit_sha=target_sha,
                authority_root=authority,
            )

            self.assertIn("TRUSTED_QUINN_AUTHORITY", prompt)
            self.assertIn("TRUSTED_MAX_AUTHORITY", prompt)
            self.assertIn("TARGET_EVIDENCE_PRESENT", prompt)

            self.assertNotIn("HOSTILE_TARGET_QUINN", prompt)
            self.assertNotIn("HOSTILE_TARGET_MAX", prompt)
            self.assertNotIn("HOSTILE_TARGET_GOVERNANCE", prompt)

    def test_authority_provenance_uses_authority_repository_revision(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            authority = base / "authority"
            target = base / "target"
            authority.mkdir()
            target.mkdir()

            for repo in (authority, target):
                subprocess.run(
                    ["git", "init", "-b", "main"],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "config", "user.name", "Fixture"],
                    cwd=repo,
                    check=True,
                )
                subprocess.run(
                    ["git", "config", "user.email", "fixture@example.invalid"],
                    cwd=repo,
                    check=True,
                )

            required = {
                "governance/AUTHORITATIVE_KNOWLEDGE_HIERARCHY.md":
                    "authority hierarchy",
                "governance/SOURCE_ISOLATION.md":
                    "source isolation",
                "governance/WORK_PACKAGE_LIFECYCLE.md":
                    "independent QA lifecycle",
            }

            for relative, content in required.items():
                path = authority / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            (target / "source.py").write_text(
                "TARGET = True\n",
                encoding="utf-8",
            )

            for repo in (authority, target):
                subprocess.run(["git", "add", "."], cwd=repo, check=True)
                subprocess.run(
                    ["git", "commit", "-m", "fixture"],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                )

            authority_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=authority,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            target_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=target,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            self.assertNotEqual(authority_sha, target_sha)

            context = _authoritative_knowledge_context(
                {
                    "project": "SemantiQ",
                    "objective": "verify QA authority provenance",
                },
                target,
                authority_root=authority,
            )

            self.assertTrue(context["sources"])

            for source in context["sources"]:
                self.assertEqual(source["revision"], authority_sha)
                self.assertEqual(
                    Path(source["authority_root"]).resolve(),
                    authority.resolve(),
                )
                self.assertNotEqual(source["revision"], target_sha)


    def test_quinn_schema_is_qa_owned_not_engineering_proposal(self):
        from controller.generic_qa_worker import _quinn_assurance_schema

        schema = _quinn_assurance_schema()

        self.assertEqual(
            set(schema["required"]),
            {
                "observed_facts",
                "missing_facts",
                "supported_inferences",
                "unsupported_inferences",
                "causal_owner",
                "causal_justification",
                "causal_evidence_refs",
                "causal_state",
                "summary",
                "findings",
            },
        )

        properties = schema["properties"]

        self.assertIn("observed_facts", properties)
        self.assertIn("missing_facts", properties)
        self.assertIn("supported_inferences", properties)
        self.assertIn("unsupported_inferences", properties)
        self.assertIn("causal_owner", properties)
        self.assertIn("causal_justification", properties)
        self.assertIn("causal_evidence_refs", properties)
        self.assertIn("causal_state", properties)
        self.assertNotIn("classification", properties)
        self.assertIn("summary", properties)
        self.assertIn("findings", properties)

        self.assertNotIn("edits", properties)
        self.assertNotIn("commit_message", properties)
        self.assertNotIn("engineering_summary", properties)


class QuinnEpistemicBoundaryRegressionTests(unittest.TestCase):
    def test_old_three_field_provider_result_is_rejected(self):
        from controller.generic_qa_worker import (
            _validated_cognitive_assurance,
        )

        with self.assertRaises(ValueError):
            _validated_cognitive_assurance(
                {
                    "causal_state": "CONTRACT_BLOCKER",
                    "summary": "blocked",
                    "findings": ["missing contract input"],
                }
            )

    def test_epistemic_schema_requires_grounding_fields(self):
        from controller.generic_qa_worker import (
            _quinn_assurance_schema,
        )

        schema = _quinn_assurance_schema()

        self.assertEqual(
            set(schema["required"]),
            {
                "observed_facts",
                "missing_facts",
                "supported_inferences",
                "unsupported_inferences",
                "causal_owner",
                "causal_justification",
                "causal_evidence_refs",
                "causal_state",
                "summary",
                "findings",
            },
        )

    def test_missing_a4_fact_cannot_be_supported_comparison(self):
        from controller.generic_qa_worker import (
            _epistemic_consistency_guard,
        )

        mission = {
            "contract_assessment": {
                "complete": False,
                "blockers": [
                    {
                        "type":
                            "MISSING_REQUIRED_CONTRACT_INPUT",
                        "authority_class": "A4",
                        "name": "approved_production_timeout",
                    }
                ],
            }
        }

        cognitive = {
            "observed_facts": [
                "implementation timeout is 30"
            ],
            "missing_facts": [
                "approved production timeout is absent"
            ],
            "supported_inferences": [
                "implementation timeout differs from approved timeout"
            ],
            "unsupported_inferences": [],
            "causal_owner": "REQUIREMENT",
            "causal_justification": "implementation timeout differs from approved timeout",
            "causal_evidence_refs": ["implementation timeout differs from approved timeout"],
            "causal_state": "REQUIREMENT_DEFECT",
            "classification": "QA_REJECTED_REQUIREMENT",
            "summary": "mismatch",
            "findings": ["timeout mismatch"],
        }

        with self.assertRaises(ValueError):
            _epistemic_consistency_guard(
                mission,
                cognitive,
            )

    def test_missing_a4_fact_allows_grounded_contract_blocker(self):
        from controller.generic_qa_worker import (
            _epistemic_consistency_guard,
        )

        mission = {
            "contract_assessment": {
                "complete": False,
                "blockers": [
                    {
                        "type":
                            "MISSING_REQUIRED_CONTRACT_INPUT",
                        "authority_class": "A4",
                        "name": "approved_production_timeout",
                    }
                ],
            }
        }

        cognitive = {
            "observed_facts": [
                "implementation timeout is 30"
            ],
            "missing_facts": [
                "approved production timeout is absent"
            ],
            "supported_inferences": [
                "required A4 value is unavailable"
            ],
            "unsupported_inferences": [
                "timeout comparison cannot be established"
            ],
            "causal_owner": "CONTRACT",
            "causal_justification": "required A4 value is unavailable",
            "causal_evidence_refs": ["required A4 value is unavailable"],
            "causal_state": "CONTRACT_BLOCKER",
            "classification": "QA_BLOCKED_CONTRACT",
            "summary": "required contract value is absent",
            "findings": [
                "comparison cannot be established"
            ],
        }

        result = _epistemic_consistency_guard(
            mission,
            cognitive,
        )

        self.assertEqual(
            result["causal_state"],
            "CONTRACT_BLOCKER",
        )

    def test_prompt_requires_causal_actor_ownership_preservation(self):
        mission = {
            "objective": "review implementation",
            "acceptance_criteria": [
                "validation must evaluate the reviewed target"
            ],
            "allowed_paths": [
                "controller/generic_qa_worker.py"
            ],
            "changed_files": [
                "controller/generic_qa_worker.py"
            ],
            "engineering_evidence": [],
            "acceptance_results": {},
            "validation_results": {
                "environment_ready": True,
                "harness_integrity": False,
            },
            "contract_assessment": {
                "declared": True,
                "complete": True,
                "blockers": [],
            },
        }

        prompt = _quinn_cognitive_prompt(
            mission=mission,
            review_root=Path.cwd(),
            branch="qualification",
            commit_sha="a" * 40,
            authority_root=Path.cwd(),
        )

        self.assertIn(
            "Preserve causal actor ownership from evidence through inference.",
            prompt,
        )
        self.assertIn(
            "must not be transferred",
            prompt,
        )
        self.assertIn(
            "failure of a validation or test mechanism does not by itself "
            "establish a defect in the reviewed implementation",
            prompt,
        )

    def test_prompt_preserves_active_evidence_and_epistemic_order(self):
        import tempfile
        from controller.generic_qa_worker import (
            _quinn_cognitive_prompt,
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixture_sha = (
                GenericQAWorkerTests()
                ._prepare_quinn_cognition_fixture(root)
            )

            prompt = _quinn_cognitive_prompt(
                mission={
                    "objective": "evaluate timeout",
                    "acceptance_criteria": [
                        "timeout must equal approved value"
                    ],
                    "allowed_paths": ["app.py"],
                    "engineering_evidence": [
                        "TARGET_EVIDENCE_PRESENT"
                    ],
                    "contract_assessment": {
                        "complete": False,
                        "blockers": [
                            {
                                "type":
                                    "MISSING_REQUIRED_CONTRACT_INPUT",
                                "authority_class": "A4",
                                "name":
                                    "approved_production_timeout",
                            }
                        ],
                    },
                },
                review_root=root,
                branch="rvsc/review",
                commit_sha=fixture_sha,
                authority_root=root,
            )

        self.assertIn(
            "TARGET_EVIDENCE_PRESENT",
            prompt,
        )
        self.assertIn(
            "CURRENT DECISION AUTHORITY CONTEXT",
            prompt,
        )
        self.assertIn(
            "EPISTEMIC REASONING REQUIREMENT",
            prompt,
        )

        self.assertLess(
            prompt.index("BOUNDED REVIEW CONTEXT"),
            prompt.index(
                "EPISTEMIC REASONING REQUIREMENT"
            ),
        )



class QuinnStructuredCausalDecisionRegressionTests(unittest.TestCase):
    def test_structured_causal_schema_requires_decision_binding(self):
        from controller.generic_qa_worker import (
            _quinn_assurance_schema,
        )

        schema = _quinn_assurance_schema()
        required = set(schema["required"])

        self.assertTrue(
            {
                "causal_owner",
                "causal_justification",
                "causal_evidence_refs",
            }.issubset(required)
        )

        owner_schema = schema[
            "properties"
        ]["causal_owner"]

        self.assertEqual(
            set(owner_schema["enum"]),
            {
                "NONE",
                "IMPLEMENTATION",
                "REQUIREMENT",
                "CONTRACT",
                "VALIDATION_HARNESS",
                "ENVIRONMENT",
                "AUTHORITY_BOUNDARY",
                "EVIDENCE",
            },
        )

    def test_validator_rejects_causal_reference_outside_supported_evidence(self):
        raw = {
            "observed_facts": [
                "Harness opened fixture_B.json."
            ],
            "missing_facts": [],
            "supported_inferences": [
                "The failed validation cannot establish "
                "implementation correctness."
            ],
            "unsupported_inferences": [],
            "causal_owner": "VALIDATION_HARNESS",
            "causal_justification": (
                "The harness inspected the wrong artifact."
            ),
            "causal_evidence_refs": [
                "This claim was never observed or supported."
            ],
            "causal_state": "HARNESS_BLOCKER",
            "summary": "Harness blocks judgment.",
            "findings": [
                "Wrong validation artifact."
            ],
        }

        with self.assertRaisesRegex(
            ValueError,
            "claims outside observed_facts/supported_inferences",
        ):
            _validated_cognitive_assurance(
                raw
            )

    def test_causal_guard_rejects_owner_state_contradiction(self):
        from controller.generic_qa_worker import (
            _causal_decision_consistency_guard,
        )

        cognitive = {
            "observed_facts": [
                "The validation mechanism evaluated "
                "the wrong artifact."
            ],
            "missing_facts": [],
            "supported_inferences": [
                "The validation failure does not establish "
                "a defect in the reviewed implementation."
            ],
            "unsupported_inferences": [],
            "causal_owner": "VALIDATION_HARNESS",
            "causal_justification": (
                "The validator inspected the wrong artifact."
            ),
            "causal_evidence_refs": [
                "The validation failure does not establish "
                "a defect in the reviewed implementation."
            ],
            "causal_state": "IMPLEMENTATION_DEFECT",
            "classification": "QA_REJECTED_IMPLEMENTATION",
            "summary": "Contradictory causal decision.",
            "findings": [
                "Harness evidence was transferred "
                "to implementation."
            ],
        }

        with self.assertRaisesRegex(
            ValueError,
            "causal owner conflicts with selected causal state",
        ):
            _causal_decision_consistency_guard(
                cognitive
            )

    def test_causal_guard_preserves_valid_harness_decision(self):
        from controller.generic_qa_worker import (
            _causal_decision_consistency_guard,
        )

        evidence = (
            "The prescribed validation mechanism "
            "evaluated the wrong artifact."
        )

        cognitive = {
            "observed_facts": [
                evidence
            ],
            "missing_facts": [],
            "supported_inferences": [
                "The failed validation cannot establish "
                "implementation correctness."
            ],
            "unsupported_inferences": [],
            "causal_owner": "VALIDATION_HARNESS",
            "causal_justification": (
                "The harness itself evaluated "
                "the wrong artifact."
            ),
            "causal_evidence_refs": [
                evidence
            ],
            "causal_state": "HARNESS_BLOCKER",
            "classification": "QA_BLOCKED_HARNESS",
            "summary": (
                "Harness blocks defensible judgment."
            ),
            "findings": [
                "Wrong artifact was evaluated."
            ],
        }

        result = (
            _causal_decision_consistency_guard(
                cognitive
            )
        )

        self.assertEqual(
            result["causal_owner"],
            "VALIDATION_HARNESS",
        )

        self.assertEqual(
            result["causal_state"],
            "HARNESS_BLOCKER",
        )

    def test_prompt_requires_structured_causal_decision_binding(self):
        prompt = _quinn_cognitive_prompt(
            mission={
                "objective": "review implementation",
                "acceptance_criteria": [
                    "judge evidenced root cause"
                ],
                "allowed_paths": [
                    "controller/generic_qa_worker.py"
                ],
                "changed_files": [
                    "controller/generic_qa_worker.py"
                ],
                "engineering_evidence": [],
                "acceptance_results": {},
                "validation_results": {},
                "contract_assessment": {
                    "declared": True,
                    "complete": True,
                    "blockers": [],
                },
            },
            review_root=Path.cwd(),
            branch="qualification",
            commit_sha="a" * 40,
            authority_root=Path.cwd(),
        )

        self.assertIn(
            "select exactly one causal_owner",
            prompt,
        )

        self.assertIn(
            "causal_evidence_refs",
            prompt,
        )

        self.assertIn(
            "causal_owner and causal_state must "
            "describe the same evidenced root cause",
            prompt,
        )


class QuinnCausalDomainOntologyRegressionTests(
    unittest.TestCase
):
    def _validated(
        self,
        *,
        owner,
        state,
        fact,
    ):
        from controller.generic_qa_worker import (
            _validated_cognitive_assurance,
        )

        return _validated_cognitive_assurance(
            {
                "observed_facts": [
                    fact,
                ],
                "missing_facts": [],
                "supported_inferences": [],
                "unsupported_inferences": [],
                "causal_owner": owner,
                "causal_justification": fact,
                "causal_evidence_refs": [
                    fact,
                ],
                "causal_state": state,
                "summary": fact,
                "findings": [
                    fact,
                ],
            }
        )

    def test_runtime_prompt_projects_evidence_provenance_owner_distinction(
        self,
    ):
        prompt = _quinn_cognitive_prompt(
            mission={
                "objective": "review governed engineering work",
                "acceptance_criteria": [
                    "implementation works",
                    "submission remains inside delegated scope",
                ],
                "allowed_paths": [
                    "controller/generic_qa_worker.py",
                ],
                "changed_files": [
                    "controller/generic_qa_worker.py",
                    (
                        "golden-core/"
                        "QA_001_QUINN_COGNITION_CONTRACT_V1.md"
                    ),
                ],
                "engineering_evidence": [
                    "authorized implementation behavior passed",
                ],
                "acceptance_results": {
                    "functional_behavior": "passed",
                },
                "validation_results": {
                    "environment_ready": True,
                    "harness_integrity": True,
                },
                "contract_assessment": {
                    "declared": True,
                    "complete": True,
                    "blockers": [],
                },
            },
            review_root=Path.cwd(),
            branch="qualification",
            commit_sha="a" * 40,
            authority_root=Path.cwd(),
        )

        self.assertIn(
            "CAUSAL DOMAIN OWNERSHIP RULE",
            prompt,
        )
        self.assertIn(
            "Evidence provenance and causal ownership are not "
            "the same concept.",
            prompt,
        )
        self.assertIn(
            "Evidence showing a changed file outside delegated scope "
            "proves an AUTHORITY_BOUNDARY cause",
            prompt,
        )
        self.assertIn(
            "EVIDENCE only when evidence itself is absent, "
            "insufficient, unreliable, misleading, unverifiable, or "
            "materially incomplete",
            prompt,
        )

    def test_evidence_reference_does_not_make_evidence_the_owner(
        self,
    ):
        from controller.generic_qa_worker import (
            _causal_decision_consistency_guard,
        )

        fact = (
            "boundary_assessment:unauthorized_changed_files "
            "contains golden-core/"
            "QA_001_QUINN_COGNITION_CONTRACT_V1.md"
        )

        cognitive = self._validated(
            owner="AUTHORITY_BOUNDARY",
            state="BOUNDARY_BLOCKER",
            fact=fact,
        )

        result = _causal_decision_consistency_guard(
            cognitive
        )

        self.assertEqual(
            result["causal_evidence_refs"],
            [
                fact,
            ],
        )
        self.assertEqual(
            result["causal_owner"],
            "AUTHORITY_BOUNDARY",
        )
        self.assertEqual(
            result["causal_state"],
            "BOUNDARY_BLOCKER",
        )
        self.assertEqual(
            result["classification"],
            "QA_BLOCKED_BOUNDARY",
        )

    def test_causal_domain_matrix_keeps_evidence_provenance_separate_from_owner(
        self,
    ):
        from controller.generic_qa_worker import (
            _causal_decision_consistency_guard,
        )

        matrix = (
            (
                "IMPLEMENTATION",
                "IMPLEMENTATION_DEFECT",
                "QA_REJECTED_IMPLEMENTATION",
                (
                    "Engineering evidence establishes that the "
                    "submitted implementation violates a coherent "
                    "required behavior."
                ),
            ),
            (
                "REQUIREMENT",
                "REQUIREMENT_DEFECT",
                "QA_REJECTED_REQUIREMENT",
                (
                    "Governance evidence establishes that the "
                    "authoritative requirement itself is invalid."
                ),
            ),
            (
                "CONTRACT",
                "CONTRACT_BLOCKER",
                "QA_BLOCKED_CONTRACT",
                (
                    "Contract evidence establishes that a required "
                    "A4 governing value is absent."
                ),
            ),
            (
                "VALIDATION_HARNESS",
                "HARNESS_BLOCKER",
                "QA_BLOCKED_HARNESS",
                (
                    "Validation evidence establishes that the "
                    "prescribed harness evaluated the wrong artifact."
                ),
            ),
            (
                "ENVIRONMENT",
                "ENVIRONMENT_BLOCKER",
                "QA_BLOCKED_ENVIRONMENT",
                (
                    "Execution evidence establishes that an external "
                    "runtime dependency is unavailable."
                ),
            ),
            (
                "AUTHORITY_BOUNDARY",
                "BOUNDARY_BLOCKER",
                "QA_BLOCKED_BOUNDARY",
                (
                    "Boundary evidence establishes that the submitted "
                    "change exceeds delegated authorization."
                ),
            ),
        )

        for (
            owner,
            state,
            classification,
            evidence_fact,
        ) in matrix:
            with self.subTest(
                owner=owner,
                state=state,
            ):
                cognitive = self._validated(
                    owner=owner,
                    state=state,
                    fact=evidence_fact,
                )

                result = (
                    _causal_decision_consistency_guard(
                        cognitive
                    )
                )

                self.assertEqual(
                    result["causal_owner"],
                    owner,
                )
                self.assertNotEqual(
                    result["causal_owner"],
                    "EVIDENCE",
                )
                self.assertEqual(
                    result["causal_state"],
                    state,
                )
                self.assertEqual(
                    result["classification"],
                    classification,
                )

    def test_evidence_remains_owner_when_evidence_itself_is_material_blocker(
        self,
    ):
        from controller.generic_qa_worker import (
            _causal_decision_consistency_guard,
        )

        fact = (
            "Required acceptance evidence is absent and no more "
            "specific causal defect can be established."
        )

        cognitive = self._validated(
            owner="EVIDENCE",
            state="EVIDENCE_BLOCKER",
            fact=fact,
        )

        result = _causal_decision_consistency_guard(
            cognitive
        )

        self.assertEqual(
            result["causal_owner"],
            "EVIDENCE",
        )
        self.assertEqual(
            result["causal_state"],
            "EVIDENCE_BLOCKER",
        )
        self.assertEqual(
            result["classification"],
            "QA_BLOCKED_EVIDENCE",
        )

    def test_none_owner_remains_reserved_for_satisfied(
        self,
    ):
        from controller.generic_qa_worker import (
            _causal_decision_consistency_guard,
        )

        fact = (
            "No unresolved material governed blocker remains."
        )

        cognitive = self._validated(
            owner="NONE",
            state="SATISFIED",
            fact=fact,
        )

        result = _causal_decision_consistency_guard(
            cognitive
        )

        self.assertEqual(
            result["causal_owner"],
            "NONE",
        )
        self.assertEqual(
            result["causal_state"],
            "SATISFIED",
        )
        self.assertEqual(
            result["classification"],
            "QA_ACCEPTED",
        )


class QuinnCausalSatisfactionEligibilityRegressionTests(
    unittest.TestCase
):
    def _validated(
        self,
        *,
        owner,
        state,
        blocker_fact,
    ):
        from controller.generic_qa_worker import (
            _validated_cognitive_assurance,
        )

        positive_fact = (
            "The reviewed implementation satisfies "
            "its functional behavior."
        )

        return _validated_cognitive_assurance(
            {
                "observed_facts": [
                    positive_fact,
                    blocker_fact,
                ],
                "missing_facts": [],
                "supported_inferences": [],
                "unsupported_inferences": [],
                "causal_owner": owner,
                "causal_justification": blocker_fact,
                "causal_evidence_refs": [
                    blocker_fact,
                ],
                "causal_state": state,
                "summary": blocker_fact,
                "findings": [
                    blocker_fact,
                ],
            }
        )

    def test_complete_blocker_matrix_survives_positive_implementation_evidence(
        self,
    ):
        from controller.generic_qa_worker import (
            _causal_decision_consistency_guard,
        )

        matrix = (
            (
                "IMPLEMENTATION",
                "IMPLEMENTATION_DEFECT",
                "QA_REJECTED_IMPLEMENTATION",
                "A material implementation defect remains.",
            ),
            (
                "REQUIREMENT",
                "REQUIREMENT_DEFECT",
                "QA_REJECTED_REQUIREMENT",
                "A material requirement defect remains.",
            ),
            (
                "CONTRACT",
                "CONTRACT_BLOCKER",
                "QA_BLOCKED_CONTRACT",
                "The active contract blocks a defensible decision.",
            ),
            (
                "VALIDATION_HARNESS",
                "HARNESS_BLOCKER",
                "QA_BLOCKED_HARNESS",
                "The prescribed validation harness is defective.",
            ),
            (
                "ENVIRONMENT",
                "ENVIRONMENT_BLOCKER",
                "QA_BLOCKED_ENVIRONMENT",
                "The execution environment blocks a defensible decision.",
            ),
            (
                "AUTHORITY_BOUNDARY",
                "BOUNDARY_BLOCKER",
                "QA_BLOCKED_BOUNDARY",
                "The submission crosses a governed authority boundary.",
            ),
            (
                "EVIDENCE",
                "EVIDENCE_BLOCKER",
                "QA_BLOCKED_EVIDENCE",
                "The evidence cannot support a defensible decision.",
            ),
        )

        for (
            owner,
            state,
            classification,
            blocker_fact,
        ) in matrix:
            with self.subTest(
                owner=owner,
                state=state,
            ):
                cognitive = self._validated(
                    owner=owner,
                    state=state,
                    blocker_fact=blocker_fact,
                )

                result = (
                    _causal_decision_consistency_guard(
                        cognitive
                    )
                )

                self.assertEqual(
                    result["causal_owner"],
                    owner,
                )
                self.assertEqual(
                    result["causal_state"],
                    state,
                )
                self.assertEqual(
                    result["classification"],
                    classification,
                )

    def test_satisfied_remains_none_owner_without_material_blocker(
        self,
    ):
        from controller.generic_qa_worker import (
            _causal_decision_consistency_guard,
            _validated_cognitive_assurance,
        )

        fact = (
            "No material governed blocker remains "
            "and acceptance evidence is sufficient."
        )

        cognitive = _validated_cognitive_assurance(
            {
                "observed_facts": [
                    fact,
                ],
                "missing_facts": [],
                "supported_inferences": [],
                "unsupported_inferences": [],
                "causal_owner": "NONE",
                "causal_justification": fact,
                "causal_evidence_refs": [
                    fact,
                ],
                "causal_state": "SATISFIED",
                "summary": fact,
                "findings": [
                    fact,
                ],
            }
        )

        result = (
            _causal_decision_consistency_guard(
                cognitive
            )
        )

        self.assertEqual(
            result["causal_owner"],
            "NONE",
        )
        self.assertEqual(
            result["causal_state"],
            "SATISFIED",
        )
        self.assertEqual(
            result["classification"],
            "QA_ACCEPTED",
        )

    def test_authoritative_contract_defines_satisfied_eligibility(
        self,
    ):
        contract_path = (
            Path.cwd()
            / "golden-core"
            / "QA_001_QUINN_COGNITION_CONTRACT_V1.md"
        )

        text = contract_path.read_text(
            encoding="utf-8-sig"
        )

        self.assertIn(
            "### Causal-state satisfaction eligibility",
            text,
        )
        self.assertIn(
            "`SATISFIED` is a whole-disposition causal state",
            text,
        )
        self.assertIn(
            "no governed authority or scope boundary "
            "has been crossed",
            text,
        )
        self.assertIn(
            "Positive evidence in one causal domain does not "
            "cancel a material blocker in another causal domain.",
            text,
        )
        self.assertIn(
            "must not manufacture a replacement semantic "
            "causal state from prose findings",
            text,
        )

    def test_rendered_prompt_projects_satisfied_eligibility_semantics(
        self,
    ):
        prompt = _quinn_cognitive_prompt(
            mission={
                "project": "RVSC",
                "repository": (
                    "GitSly1/RAMTech-RVSC-Control-Center"
                ),
                "objective": (
                    "Review governed engineering work."
                ),
                "acceptance_criteria": [
                    "Implementation is correct.",
                    "Submission remains inside authorized scope.",
                ],
                "allowed_paths": [
                    "controller/generic_qa_worker.py",
                ],
                "changed_files": [
                    "controller/generic_qa_worker.py",
                ],
                "engineering_evidence": [
                    "Functional behavior passed.",
                ],
                "validation_results": {
                    "environment_ready": True,
                    "harness_integrity": True,
                },
                "contract_assessment": {
                    "declared": True,
                    "complete": True,
                    "blockers": [],
                },
            },
            review_root=Path.cwd(),
            branch="qualification",
            commit_sha="a" * 40,
            authority_root=Path.cwd(),
        )

        self.assertIn(
            "SATISFIED ELIGIBILITY RULE:",
            prompt,
        )

        self.assertIn(
            "Positive evidence in one causal domain "
            "must not cancel a material blocker in another.",
            prompt,
        )

        self.assertIn(
            "Do not preserve the blocker only as a secondary "
            "finding while selecting SATISFIED",
            prompt,
        )

        for owner in (
            "IMPLEMENTATION",
            "REQUIREMENT",
            "CONTRACT",
            "VALIDATION_HARNESS",
            "ENVIRONMENT",
            "AUTHORITY_BOUNDARY",
            "EVIDENCE",
        ):
            with self.subTest(
                owner=owner,
            ):
                self.assertIn(
                    owner,
                    prompt,
                )



class QuinnAuthorityBoundaryFactRegressionTests(
    unittest.TestCase
):
    def test_boundary_assessment_identifies_exact_unauthorized_file(
        self,
    ):
        from controller.generic_qa_worker import (
            _authority_boundary_assessment,
        )

        result = _authority_boundary_assessment(
            {
                "allowed_paths": [
                    "controller/generic_qa_worker.py",
                ],
                "changed_files": [
                    "controller/generic_qa_worker.py",
                    (
                        "golden-core/"
                        "QA_001_QUINN_COGNITION_CONTRACT_V1.md"
                    ),
                ],
            }
        )

        self.assertTrue(
            result["complete"]
        )

        self.assertFalse(
            result["scope_compliant"]
        )

        self.assertEqual(
            result["unauthorized_changed_files"],
            [
                (
                    "golden-core/"
                    "QA_001_QUINN_COGNITION_CONTRACT_V1.md"
                )
            ],
        )

        self.assertEqual(
            result["authority_class"],
            "A4",
        )

    def test_boundary_assessment_accepts_child_of_authorized_module(
        self,
    ):
        from controller.generic_qa_worker import (
            _authority_boundary_assessment,
        )

        result = _authority_boundary_assessment(
            {
                "allowed_paths": [
                    "controller",
                ],
                "changed_files": [
                    "controller/generic_qa_worker.py",
                    "controller/submodule/checks.py",
                ],
            }
        )

        self.assertTrue(
            result["complete"]
        )

        self.assertTrue(
            result["scope_compliant"]
        )

        self.assertEqual(
            result["unauthorized_changed_files"],
            [],
        )

    def test_boundary_assessment_normalizes_repository_relative_separators(
        self,
    ):
        from controller.generic_qa_worker import (
            _authority_boundary_assessment,
        )

        result = _authority_boundary_assessment(
            {
                "allowed_paths": [
                    "./controller",
                ],
                "changed_files": [
                    r"controller\generic_qa_worker.py",
                ],
            }
        )

        self.assertTrue(
            result["scope_compliant"]
        )

        self.assertEqual(
            result["changed_files"],
            [
                "controller/generic_qa_worker.py",
            ],
        )

    def test_boundary_assessment_does_not_infer_when_contract_fact_is_incomplete(
        self,
    ):
        from controller.generic_qa_worker import (
            _authority_boundary_assessment,
        )

        result = _authority_boundary_assessment(
            {
                "allowed_paths": [
                    "controller",
                ],
            }
        )

        self.assertFalse(
            result["complete"]
        )

        self.assertIsNone(
            result["scope_compliant"]
        )

        self.assertEqual(
            result["unauthorized_changed_files"],
            [],
        )

    def test_rendered_prompt_projects_authoritative_boundary_fact(
        self,
    ):
        unauthorized = (
            "golden-core/"
            "QA_001_QUINN_COGNITION_CONTRACT_V1.md"
        )

        prompt = _quinn_cognitive_prompt(
            mission={
                "project": "RVSC",
                "repository": (
                    "GitSly1/RAMTech-RVSC-Control-Center"
                ),
                "objective": (
                    "Review governed engineering work."
                ),
                "acceptance_criteria": [
                    "Implementation works.",
                    "Submission remains in authorized scope.",
                ],
                "allowed_paths": [
                    "controller/generic_qa_worker.py",
                ],
                "changed_files": [
                    "controller/generic_qa_worker.py",
                    unauthorized,
                ],
                "engineering_evidence": [
                    "Functional behavior passed.",
                ],
                "validation_results": {
                    "environment_ready": True,
                    "harness_integrity": True,
                },
                "contract_assessment": {
                    "declared": True,
                    "complete": True,
                    "blockers": [],
                },
            },
            review_root=Path.cwd(),
            branch="qualification",
            commit_sha="a" * 40,
            authority_root=Path.cwd(),
        )

        self.assertIn(
            '"boundary_assessment"',
            prompt,
        )

        self.assertIn(
            '"scope_compliant": false',
            prompt,
        )

        self.assertIn(
            '"unauthorized_changed_files": '
            '["'
            + unauthorized
            + '"]',
            prompt,
        )

        self.assertIn(
            "controller boundary_assessment is an authoritative A4 "
            "structured fact",
            prompt,
        )

        self.assertIn(
            "This deterministic fact does not choose causal_state",
            prompt,
        )

        self.assertIn(
            "Quinn retains semantic causal ownership",
            prompt,
        )




if __name__ == "__main__":
    unittest.main()
