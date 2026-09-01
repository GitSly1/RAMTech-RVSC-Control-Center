from __future__ import annotations

import json
import os
import sys

from controller.adapters import WorkerRequest
from controller.provider_loader import build_broker_from_environment


def main() -> int:
    os.environ.setdefault("RVSC_PROVIDER_NAME", "openai-daniel-local")
    os.environ.setdefault("RVSC_PROVIDER_ENDPOINT", "http://127.0.0.1:8768/execute")
    os.environ.setdefault("RVSC_PROVIDER_TIMEOUT_SECONDS", "300")
    os.environ.setdefault("RVSC_PROVIDER_REQUIRE_HEALTHCHECK", "true")

    request = WorkerRequest(
        agent_id="DEV-001",
        wp_id="SEM-DANIEL-003",
        project="SEMANTIQ",
        repository="GitSly1/RAMTech-SEMANTIQ",
        base_branch="rvsc/SEM-DANIEL-003-final-baseline",
        work_branch="rvsc/SEM-DANIEL-003-runtime-proof",
        objective=(
            "Improve SEMANTIQ website interpretation relationship classification as a general, evidence-driven capability. "
            "Inspect the existing interpretation_layer.py behavior and strengthen URL-role classification for realistic "
            "same-site absolute/relative links, pagination/continuation signals, downloadable resources, media, navigation, "
            "external links, and detail relationships. Preserve observation-only semantics. Replace the seeded test surface "
            "with meaningful regression tests that demonstrate the improved behavior without site-specific rules."
        ),
        allowed_paths=("interpretation_layer.py", "tests/test_interpretation_layer.py"),
        acceptance_criteria=(
            "starts from the prepared SEM-DANIEL-003 baseline derived from SEM-003",
            "only interpretation_layer.py and tests/test_interpretation_layer.py are modified",
            "classification remains generic and not website-specific",
            "relative same-site links are handled correctly",
            "pagination and continuation signals are recognized",
            "resource and media links remain distinguishable",
            "navigation and external links remain distinguishable from detail relationships",
            "observation-only interpretation behavior is preserved",
            "meaningful regression tests replace the seed test",
            "compile validation passes",
            "SEMANTIQ unit tests pass",
            "DEV-001 commit is created and pushed to the runtime-proof branch",
            "attributable execution evidence is returned",
        ),
    )
    result = build_broker_from_environment().execute(os.environ["RVSC_PROVIDER_NAME"], request)
    print(json.dumps({"success": result.success, "summary": result.summary, "evidence": list(result.evidence), "retryable": result.retryable}, indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
