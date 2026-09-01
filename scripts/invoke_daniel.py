from __future__ import annotations

import json
import os
import sys

from controller.adapters import WorkerRequest
from controller.provider_loader import build_broker_from_environment


def main() -> int:
    os.environ.setdefault("RVSC_PROVIDER_NAME", "openai-daniel-local")
    os.environ.setdefault("RVSC_PROVIDER_ENDPOINT", "http://127.0.0.1:8767/execute")
    os.environ.setdefault("RVSC_PROVIDER_TIMEOUT_SECONDS", "120")

    request = WorkerRequest(
        agent_id="DEV-001",
        wp_id=os.environ.get("RVSC_DANIEL_WP", "SEM-DANIEL-HANDSHAKE-001"),
        project="SEMANTIQ",
        repository="GitSly1/RAMTech-SEMANTIQ",
        base_branch="main",
        work_branch=os.environ.get("RVSC_DANIEL_BRANCH", "semantiq/daniel-handshake"),
        objective="Prove DEV-001 Daniel can receive a bounded SEMANTIQ mission through the RVSC execution bridge and return attributable live-provider evidence.",
        allowed_paths=("docs/",),
        acceptance_criteria=(
            "provider returns completed response",
            "evidence includes DEV-001 identity",
            "evidence includes run id and timestamps",
            "evidence includes provider response id and model",
        ),
    )

    broker = build_broker_from_environment()
    result = broker.execute(os.environ["RVSC_PROVIDER_NAME"], request)
    print(json.dumps({
        "success": result.success,
        "summary": result.summary,
        "evidence": list(result.evidence),
        "retryable": result.retryable,
    }, indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
