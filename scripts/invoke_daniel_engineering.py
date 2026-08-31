from __future__ import annotations

import json
import os
import sys

from controller.adapters import WorkerRequest
from controller.provider_loader import build_broker_from_environment


def main() -> int:
    os.environ.setdefault("RVSC_PROVIDER_NAME", "openai-daniel-local")
    os.environ.setdefault("RVSC_PROVIDER_ENDPOINT", "http://127.0.0.1:8767/execute")
    os.environ.setdefault("RVSC_PROVIDER_TIMEOUT_SECONDS", "300")

    request = WorkerRequest(
        agent_id="DEV-001",
        wp_id="SEM-DANIEL-001",
        project="SEMANTIQ",
        repository="GitSly1/RAMTech-SEMANTIQ",
        base_branch="rvsc/SEM-003-rtudes-baseline-import",
        work_branch="rvsc/SEM-DANIEL-001-reproduction",
        objective=(
            "Implement strict X.Y.Z semantic-version parsing for SEMANTIQ identity, add an immutable orderable "
            "SemanticVersion value object, export the new API, and add regression tests while preserving existing identity behavior."
        ),
        allowed_paths=(
            "src/semantiq/identity.py",
            "src/semantiq/__init__.py",
            "tests/test_identity.py",
        ),
        acceptance_criteria=(
            "only authorized files are modified",
            "canonical X.Y.Z versions parse into integer components",
            "non-canonical versions are rejected",
            "SemanticVersion ordering works",
            "new API is publicly exported",
            "compile validation passes",
            "SEMANTIQ unit tests pass",
            "DEV-001 commit is created and pushed",
            "attributable execution evidence is returned",
        ),
    )

    result = build_broker_from_environment().execute(os.environ["RVSC_PROVIDER_NAME"], request)
    print(json.dumps({
        "success": result.success,
        "summary": result.summary,
        "evidence": list(result.evidence),
        "retryable": result.retryable,
    }, indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
