from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

ALLOWED_TRANSITIONS = {
    "draft": {"ready"},
    "ready": {"in_progress", "blocked"},
    "in_progress": {"blocked", "review"},
    "blocked": {"in_progress", "rejected"},
    "review": {"in_progress", "accepted", "rejected"},
    "accepted": {"closed"},
    "rejected": {"in_progress", "closed"},
    "closed": set(),
}

REQUIRED_HANDOFF_KEYS = {
    "files_changed",
    "validation_results",
    "risks",
    "commit_or_pr",
}


@dataclass(frozen=True)
class GateResult:
    eligible: bool
    reasons: tuple[str, ...]


def transition_allowed(current: str, requested: str) -> bool:
    return requested in ALLOWED_TRANSITIONS.get(current, set())


def _normalize(path: str) -> str:
    return str(PurePosixPath(path))


def path_allowed(path: str, allowed_paths: Iterable[str], forbidden_paths: Iterable[str]) -> bool:
    candidate = _normalize(path)

    for forbidden in forbidden_paths:
        forbidden = _normalize(forbidden.rstrip("*"))
        if candidate == forbidden or candidate.startswith(forbidden.rstrip("/") + "/"):
            return False

    for allowed in allowed_paths:
        prefix = _normalize(allowed.rstrip("*"))
        if candidate == prefix or candidate.startswith(prefix.rstrip("/") + "/"):
            return True
    return False


def validate_scope(changed_files: Iterable[str], allowed_paths: Iterable[str], forbidden_paths: Iterable[str]) -> list[str]:
    violations = []
    for path in changed_files:
        if not path_allowed(path, allowed_paths, forbidden_paths):
            violations.append(path)
    return violations


def validate_handoff(handoff_report: dict) -> list[str]:
    missing = sorted(REQUIRED_HANDOFF_KEYS - set(handoff_report))
    errors = [f"missing handoff field: {item}" for item in missing]
    if not handoff_report.get("files_changed"):
        errors.append("handoff must report files_changed")
    if not handoff_report.get("validation_results"):
        errors.append("handoff must report validation_results")
    if not handoff_report.get("commit_or_pr"):
        errors.append("handoff must report commit_or_pr")
    return errors


def evaluate_merge_eligibility(
    *,
    status: str,
    target_repository: str,
    actual_repository: str,
    base_branch: str,
    work_branch: str,
    changed_files: Iterable[str],
    allowed_paths: Iterable[str],
    forbidden_paths: Iterable[str],
    acceptance_results: dict[str, bool],
    validation_results: dict[str, bool],
    handoff_report: dict,
    pr_exists: bool,
    pr_mergeable: bool,
    review_approved: bool,
    qa_accepted: bool,
) -> GateResult:
    reasons: list[str] = []

    if status != "review":
        reasons.append("work package must be in review state")
    if target_repository != actual_repository:
        reasons.append("repository does not match work package target")
    if base_branch != "main":
        reasons.append("base branch must remain main")
    if not work_branch.startswith("rvsc/"):
        reasons.append("work branch must use rvsc/ prefix")
    if work_branch == base_branch:
        reasons.append("direct development on base branch is prohibited")

    violations = validate_scope(changed_files, allowed_paths, forbidden_paths)
    if violations:
        reasons.append("out-of-scope paths: " + ", ".join(sorted(violations)))

    failed_acceptance = sorted(k for k, passed in acceptance_results.items() if not passed)
    if failed_acceptance:
        reasons.append("acceptance criteria failed: " + ", ".join(failed_acceptance))

    failed_validation = sorted(k for k, passed in validation_results.items() if not passed)
    if failed_validation:
        reasons.append("validation failed: " + ", ".join(failed_validation))

    reasons.extend(validate_handoff(handoff_report))

    if not pr_exists:
        reasons.append("pull request evidence missing")
    if not pr_mergeable:
        reasons.append("pull request is not mergeable")
    if not review_approved:
        reasons.append("required review approval missing")
    if not qa_accepted:
        reasons.append("QA acceptance missing")

    return GateResult(eligible=not reasons, reasons=tuple(reasons))
