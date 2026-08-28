import unittest

from controller.work_package_controller import (
    evaluate_merge_eligibility,
    transition_allowed,
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
