from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CEA = ROOT / "governance" / "CONTRACT_TO_EXECUTION_ASSURANCE.md"


class ContractToExecutionAssuranceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = CEA.read_text(encoding="utf-8-sig")

    def test_governing_chain_is_explicit(self) -> None:
        self.assertIn(
            "DESIGN -> CONTRACT -> IMPLEMENTATION -> INTEGRATION -> BEHAVIOR -> EVIDENCE -> INDEPENDENT ASSURANCE",
            self.text,
        )

    def test_maturity_states_are_preserved(self) -> None:
        for state in (
            "DESIGNED",
            "IMPLEMENTED",
            "INTEGRATED",
            "TESTED",
            "BEHAVIORALLY PROVEN",
            "INDEPENDENTLY QUALIFIED",
            "PRODUCTION ELIGIBLE",
        ):
            self.assertIn(state, self.text)

    def test_qa_is_not_reduced_to_prescribed_validation(self) -> None:
        self.assertIn(
            "Independent QA must not be reduced to executing prescribed validation commands.",
            self.text,
        )
        self.assertIn(
            "A deficient contract must be distinguished from an implementation defect.",
            self.text,
        )

    def test_interface_assurance_and_failure_learning_are_required(self) -> None:
        self.assertIn("Interface and arrow assurance", self.text)
        self.assertIn(
            "INCIDENT -> ROOT CAUSE -> GENERALIZED FAILURE CLASS -> INVARIANT -> REGRESSION TEST -> CAPABILITY ASSURANCE UPDATE -> REQUALIFICATION",
            self.text,
        )


if __name__ == "__main__":
    unittest.main()
