import unittest

from controller.work_package_controller import (
    QA_ACCEPTED,
    QA_REJECTED,
    QAHandoffError,
    build_qa_mission,
    evaluate_merge_eligibility,
    transition_allowed,
    validate_qa_result,
    validate_scope,
)


class WorkPackageControllerTests(unittest.TestCase):
    def test_lifecycle_allows_review_rework(self):
        self.assertTrue(transition_allowed("review", "in_progress"))
        self.assertFalse(transition_allowed("draft", "accepted"))

    def test_scope_rejects_outside_allowed_paths(self):
        violations = validate_scope(
            ["src/semantiq/core.py", "secrets/token.txt"],
            ["src/**", "tests/**"],
            ["secrets/**"],
        )
        self.assertEqual(violations, ["secrets/token.txt"])

    def test_build_qa_mission_propagates_exact_engineering_context(self):
        mission = {
            "agent_id": "OPS-001",
            "project": "rvsc",
            "wp_id": "RVSC-026B",
            "work_branch": "rvsc/RVSC-026B-auto-qa-handoff",
            "allowed_paths": ["controller/generic_worker_host.py"],
            "validation_commands": [{"name": "tests", "argv": ["python", "-m", "unittest"]}],
        }
        result = {
            "success": True,
            "run_id": "ENG-RUN",
            "commit_sha": "0123456789abcdef",
            "work_branch": mission["work_branch"],
            "pushed": True,
        }

        qa = build_qa_mission(engineering_mission=mission, engineering_result=result, qa_agent_id="QA-001")

        self.assertEqual(qa["agent_id"], "QA-001")
        self.assertEqual(qa["implementer_id"], "OPS-001")
        self.assertEqual(qa["engineering_branch"], mission["work_branch"])
        self.assertEqual(qa["engineering_commit_sha"], "0123456789abcdef")
        self.assertEqual(qa["allowed_paths"], mission["allowed_paths"])
        self.assertEqual(qa["validation_commands"], mission["validation_commands"])

    def test_build_qa_mission_rejects_implementer(self):
        with self.assertRaisesRegex(QAHandoffError, "implementer"):
            build_qa_mission(
                engineering_mission={"agent_id": "QA-001", "project": "rvsc"},
                engineering_result={"success": True},
                qa_agent_id="QA-001",
            )

    def test_build_qa_mission_requires_commit_evidence(self):
        with self.assertRaisesRegex(QAHandoffError, "commit"):
            build_qa_mission(
                engineering_mission={"agent_id": "OPS-001", "project": "rvsc", "work_branch": "rvsc/test"},
                engineering_result={"success": True, "pushed": True},
                qa_agent_id="QA-001",
            )

    def test_build_qa_mission_requires_push_evidence(self):
        with self.assertRaisesRegex(QAHandoffError, "push"):
            build_qa_mission(
                engineering_mission={"agent_id": "OPS-001", "project": "rvsc", "work_branch": "rvsc/test"},
                engineering_result={"success": True, "commit_sha": "abcdef123456"},
                qa_agent_id="QA-001",
            )

    def test_validate_qa_result_preserves_accepted_evidence(self):
        verdict, evidence = validate_qa_result({"success": True, "verdict": QA_ACCEPTED, "evidence": ["tests:pass"]})
        self.assertEqual(verdict, QA_ACCEPTED)
        self.assertEqual(evidence, ("tests:pass",))

    def test_validate_qa_result_preserves_rejected_evidence(self):
        verdict, evidence = validate_qa_result({"success": True, "verdict": QA_REJECTED, "evidence": ["scope:failed"]})
        self.assertEqual(verdict, QA_REJECTED)
        self.assertEqual(evidence, ("scope:failed",))

    def test_validate_qa_result_fails_closed_on_malformed_evidence(self):
        with self.assertRaisesRegex(QAHandoffError, "malformed"):
            validate_qa_result({"success": True, "verdict": QA_ACCEPTED, "evidence": []})

    def test_merge_gate_rejects_missing_handoff(self):
        result = evaluate_merge_eligibility(
            status="review",
            target_repository="GitSly1/RAMTech-SEMANTIQ",
            actual_repository="GitSly1/RAMTech-SEMANTIQ",
            base_branch="main",
            work_branch="rvsc/SEM-001-bootstrap",
            changed_files=["src/semantiq/identity.py"],
            allowed_paths=["src/**"],
            forbidden_paths=[],
            acceptance_results={"AC-1": True},
            validation_results={"TEST": True},
            handoff_report={},
            pr_exists=True,
            pr_mergeable=True,
            review_approved=True,
            qa_accepted=True,
        )
        self.assertFalse(result.eligible)
        self.assertTrue(any("handoff" in reason for reason in result.reasons))

    def test_merge_gate_passes_complete_evidence(self):
        result = evaluate_merge_eligibility(
            status="review",
            target_repository="GitSly1/RAMTech-SEMANTIQ",
            actual_repository="GitSly1/RAMTech-SEMANTIQ",
            base_branch="main",
            work_branch="rvsc/SEM-001-bootstrap",
            changed_files=["src/semantiq/identity.py", "tests/test_identity.py"],
            allowed_paths=["src/**", "tests/**"],
            forbidden_paths=[".rvsc/**"],
            acceptance_results={"AC-1": True, "AC-2": True},
            validation_results={"UNIT": True, "SCOPE": True},
            handoff_report={
                "files_changed": ["src/semantiq/identity.py", "tests/test_identity.py"],
                "validation_results": {"UNIT": "PASS", "SCOPE": "PASS"},
                "risks": [],
                "commit_or_pr": "PR#2",
            },
            pr_exists=True,
            pr_mergeable=True,
            review_approved=True,
            qa_accepted=True,
        )
        self.assertTrue(result.eligible, result.reasons)


if __name__ == "__main__":
    unittest.main()
