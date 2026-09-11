from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "golden-core" / "QA_001_QUINN_COGNITION_CONTRACT_V1.md"


class QuinnCognitionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = CONTRACT.read_text(encoding="utf-8-sig")

    def test_shared_operational_discipline_is_required(self) -> None:
        self.assertIn("MAX Platinum Engineering Core", self.text)
        self.assertIn("evidence before claims", self.text)
        self.assertIn("explicit uncertainty", self.text)

    def test_independent_qa_reasoning_is_required(self) -> None:
        for phrase in (
            "Original objective",
            "Acceptance sufficiency",
            "Implementation behavior",
            "Architecture and governance consistency",
            "Evidence integrity",
            "Regression and safety risk",
            "Historical and authoritative conflicts",
            "Assumption and uncertainty challenge",
        ):
            self.assertIn(phrase, self.text)

    def test_failure_classification_is_explicit(self) -> None:
        for classification in (
            "QA_REJECTED_IMPLEMENTATION",
            "QA_REJECTED_REQUIREMENT",
            "QA_BLOCKED_CONTRACT",
            "QA_BLOCKED_HARNESS",
            "QA_BLOCKED_ENVIRONMENT",
            "QA_BLOCKED_BOUNDARY",
            "QA_BLOCKED_EVIDENCE",
        ):
            self.assertIn(classification, self.text)

    def test_classification_taxonomy_defines_requirement_vs_contract_boundary(self) -> None:
        self.assertIn(
            "QA_REJECTED_REQUIREMENT",
            self.text,
        )
        self.assertIn(
            "requirement itself is invalid, unsafe, unauthorized",
            self.text,
        )
        self.assertIn(
            "QA_BLOCKED_CONTRACT",
            self.text,
        )
        self.assertIn(
            "internally contradictory, materially ambiguous, mutually exclusive",
            self.text,
        )
        self.assertIn(
            "Classification precedence must follow root cause rather than surface symptom.",
            self.text,
        )
        self.assertIn(
            "Two acceptance criteria that require mutually exclusive behavior are `QA_BLOCKED_CONTRACT`.",
            self.text,
        )
        self.assertIn(
            "A clear requirement that directly conflicts with authoritative RVSC policy is `QA_REJECTED_REQUIREMENT`.",
            self.text,
        )

    def test_root_cause_precedence_distinguishes_environment_from_contract(self) -> None:
        for phrase in (
            "Classify the condition that prevents a defensible QA disposition",
            "Use `QA_BLOCKED_CONTRACT` only when the contract itself is the blocker",
            "Do not use `QA_BLOCKED_CONTRACT` merely because a valid acceptance criterion could not be completed.",
            "external runtime, provider, dependency, infrastructure, service, or execution environment",
            "use `QA_BLOCKED_ENVIRONMENT`",
            "prescribed validation or qualification mechanism is defective",
            "use `QA_BLOCKED_HARNESS`",
            "most specific evidenced root cause",
        ):
            self.assertIn(phrase, self.text)

    def test_classification_taxonomy_covers_blocking_boundaries(self) -> None:
        for phrase in (
            "QA_BLOCKED_HARNESS",
            "defect is attributable to the harness",
            "QA_BLOCKED_ENVIRONMENT",
            "external runtime, provider, dependency, infrastructure",
            "QA_BLOCKED_BOUNDARY",
            "authorization, repository, role, project, promotion",
            "QA_BLOCKED_EVIDENCE",
            "insufficient, internally unreliable, misleading, unverifiable",
        ):
            self.assertIn(phrase, self.text)

    def test_cognition_cannot_override_deterministic_qa(self) -> None:
        self.assertIn(
            "A cognitive pass cannot override deterministic failure.",
            self.text,
        )
        self.assertIn(
            "A deterministic pass cannot override cognitive rejection or blocking findings.",
            self.text,
        )

    def test_behavioral_qualification_is_required(self) -> None:
        self.assertIn(
            "Quinn is not cognitively qualified merely because this contract exists",
            self.text,
        )
        self.assertIn(
            "representative adversarial cases",
            self.text,
        )


if __name__ == "__main__":
    unittest.main()
