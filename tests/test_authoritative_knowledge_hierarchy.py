from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "governance" / "AUTHORITATIVE_KNOWLEDGE_HIERARCHY.md"


class AuthoritativeKnowledgeHierarchyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = CONTRACT.read_text(encoding="utf-8-sig")

    def test_authority_classes_are_explicit(self):
        for authority in ("A0", "A1", "A2", "A3", "A4", "A5", "A6"):
            self.assertIn(authority, self.text)

    def test_human_authority_remains_supreme(self):
        self.assertIn("Human/company authority remains supreme", self.text)

    def test_operational_projections_are_not_self_authenticating_truth(self):
        self.assertIn(
            "They are not self-authenticating truth",
            self.text,
        )
        self.assertIn("COMMAND_DASHBOARD.md", self.text)
        self.assertIn("ROADMAP.md", self.text)

    def test_accepted_execution_evidence_controls_execution_claims(self):
        self.assertIn(
            "accepted evidence controls the factual execution claim",
            self.text,
        )

    def test_mission_cannot_expand_authority(self):
        self.assertIn(
            "A mission cannot expand itself beyond A0-A3 authority",
            self.text,
        )

    def test_conflicts_must_not_be_silently_reconciled(self):
        self.assertIn(
            "Material conflicts between authoritative sources must be surfaced",
            self.text,
        )
        self.assertIn(
            "Quinn must never resolve a material conflict merely by choosing",
            self.text,
        )

    def test_retrieval_requires_provenance_and_bounding(self):
        for requirement in (
            "repository-relative path",
            "Git revision",
            "authority class",
            "content digest",
            "complete or truncated",
        ):
            self.assertIn(requirement, self.text)

    def test_retrieval_is_read_only(self):
        self.assertIn(
            "Retrieval is read-only evidence. Selection does not authorize mutation.",
            self.text,
        )

    def test_fail_closed_conditions_are_explicit(self):
        for condition in (
            "a required source cannot be read",
            "provenance cannot be established",
            "material truncation obscures the governing fact",
            "contradictory sources cannot be defensibly resolved",
        ):
            self.assertIn(condition, self.text)

    def test_behavioral_qualification_is_required(self):
        self.assertIn(
            "Implementation of this contract is not qualification",
            self.text,
        )
        self.assertIn(
            "retrieve a relevant governing constraint that was not manually embedded",
            self.text,
        )


if __name__ == "__main__":
    unittest.main()
