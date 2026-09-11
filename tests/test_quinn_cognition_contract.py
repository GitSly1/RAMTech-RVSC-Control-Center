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
