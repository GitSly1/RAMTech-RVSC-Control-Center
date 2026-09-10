from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "golden-core" / "MAX_PLATINUM_ENGINEERING_CORE_V1.md"


class MaxPlatinumEngineeringCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = CORE.read_text(encoding="utf-8")

    def test_daniel_owns_complete_engineering_cognition_loop(self) -> None:
        required = (
            "Problem understanding",
            "Evidence",
            "Dependency model",
            "Competing hypotheses",
            "First proven divergence",
            "Root cause",
            "Corrective design",
            "Affected dependencies and predicted effects",
            "Invariants",
            "Implementation",
            "Verification expectations",
            "Learning candidate",
        )
        for capability in required:
            with self.subTest(capability=capability):
                self.assertIn(capability, self.text)

    def test_root_cause_first_method_is_explicit(self) -> None:
        self.assertIn(
            "INSPECT EVIDENCE → RECONSTRUCT EXACT FAILURE PATH → FIRST PROVEN DIVERGENCE → DEPENDENCY CONE → ROOT CAUSE",
            self.text,
        )
        self.assertIn(
            "Regression testing verifies the engineering solution. It is not the primary method for discovering one.",
            self.text,
        )

    def test_execution_authority_remains_outside_daniel(self) -> None:
        self.assertIn("Daniel owns:", self.text)
        self.assertIn("RVSC/controller owns:", self.text)
        self.assertIn("Quinn owns:", self.text)
        self.assertIn(
            "Daniel must reason like the engineer; the controller must not replace engineering judgment with pass/fail mechanics",
            self.text,
        )

    def test_learning_requires_qualification(self) -> None:
        self.assertIn("Qualified learning rule", self.text)
        self.assertIn(
            "Retain a generalized engineering lesson only after the implementation is verified and independent QA accepts the mission.",
            self.text,
        )
        self.assertIn(
            "Failed hypotheses, rejected designs, and unqualified model assertions remain historical evidence",
            self.text,
        )

    def test_hidden_reasoning_is_not_claimed_or_copied(self) -> None:
        self.assertIn(
            "without copying model weights, hidden reasoning, system internals, or private configuration",
            self.text,
        )
        self.assertIn(
            "It does not and cannot copy Max's underlying model weights, hidden chain-of-thought, system prompts, private runtime state, or proprietary internal configuration.",
            self.text,
        )


if __name__ == "__main__":
    unittest.main()
