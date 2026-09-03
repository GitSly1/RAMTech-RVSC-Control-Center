from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable

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
REQUIRED_HANDOFF_KEYS = {"files_changed", "validation_results", "risks", "commit_or_pr"}
QA_ACCEPTED = "QA_ACCEPTED"
QA_REJECTED = "QA_REJECTED"


@dataclass(frozen=True)
class GateResult:
    eligible: bool
    reasons: tuple[str, ...]


class QAHandoffError(ValueError):
    pass


def transition_allowed(current: str, requested: str) -> bool:
    return requested in ALLOWED_TRANSITIONS.get(current, set())


def _normalize(path: str) -> str:
    return str(PurePosixPath(path))


def path_allowed(path: str, allowed_paths: Iterable[str], forbidden_paths: Iterable[str]) -> bool:
    candidate = _normalize(path)
    for forbidden in forbidden_paths:
        prefix = _normalize(forbidden.rstrip("*"))
        if candidate == prefix or candidate.startswith(prefix.rstrip("/") + "/"):
            return False
    for allowed in allowed_paths:
        prefix = _normalize(allowed.rstrip("*"))
        if candidate == prefix or candidate.startswith(prefix.rstrip("/") + "/"):
            return True
    return False


def validate_scope(changed_files: Iterable[str], allowed_paths: Iterable[str], forbidden_paths: Iterable[str]) -> list[str]:
    return [path for path in changed_files if not path_allowed(path, allowed_paths, forbidden_paths)]


def validate_handoff(handoff_report: dict) -> list[str]:
    errors = [f"missing handoff field: {item}" for item in sorted(REQUIRED_HANDOFF_KEYS - set(handoff_report))]
    if not handoff_report.get("files_changed"):
        errors.append("handoff must report files_changed")
    if not handoff_report.get("validation_results"):
        errors.append("handoff must report validation_results")
    if not handoff_report.get("commit_or_pr"):
        errors.append("handoff must report commit_or_pr")
    return errors


def _first_text(values: Iterable[Any]) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def engineering_commit_sha(result: dict[str, Any]) -> str:
    git = result.get("git") if isinstance(result.get("git"), dict) else {}
    handoff = result.get("handoff") if isinstance(result.get("handoff"), dict) else {}
    commit = _first_text((result.get("commit_sha"), result.get("commit"), git.get("commit_sha"), git.get("commit"), handoff.get("commit_sha")))
    if commit:
        return commit
    evidence = result.get("evidence")
    if isinstance(evidence, (list, tuple)):
        for item in evidence:
            text = str(item).strip()
            for prefix in ("commit_sha:", "commit:"):
                if text.lower().startswith(prefix):
                    return text.split(":", 1)[1].strip()
    return ""


def engineering_push_succeeded(result: dict[str, Any]) -> bool:
    if result.get("pushed") is True or result.get("push_success") is True or result.get("push") is True:
        return True
    push = result.get("push")
    if isinstance(push, str) and push.strip().lower() in {"success", "succeeded", "pushed", "true"}:
        return True
    if isinstance(push, dict) and (push.get("success") is True or str(push.get("status", "")).lower() in {"success", "succeeded", "pushed"}):
        return True
    evidence = result.get("evidence")
    return isinstance(evidence, (list, tuple)) and any(str(item).strip().lower() in {"push:success", "push:succeeded", "pushed:true"} for item in evidence)


def build_qa_mission(*, engineering_mission: dict[str, Any], engineering_result: dict[str, Any], qa_agent_id: str) -> dict[str, Any]:
    implementer_id = str(engineering_mission.get("agent_id", "")).strip()
    if not qa_agent_id.strip():
        raise QAHandoffError("missing QA candidate")
    if qa_agent_id.strip().upper() == implementer_id.upper():
        raise QAHandoffError("implementer cannot be selected as QA")
    if not engineering_result.get("success"):
        raise QAHandoffError("engineering execution was not successful")

    branch = _first_text((engineering_result.get("work_branch"), engineering_result.get("branch"), engineering_mission.get("work_branch"), engineering_mission.get("branch")))
    commit_sha = engineering_commit_sha(engineering_result)
    if not branch:
        raise QAHandoffError("missing engineering branch evidence")
    if not commit_sha:
        raise QAHandoffError("missing engineering commit evidence")
    expected_branch = _first_text((engineering_mission.get("work_branch"), engineering_mission.get("branch")))
    result_branch = _first_text((engineering_result.get("work_branch"), engineering_result.get("branch")))
    if expected_branch and result_branch and expected_branch != result_branch:
        raise QAHandoffError("engineering branch evidence does not match mission")
    if not engineering_push_succeeded(engineering_result):
        raise QAHandoffError("missing successful push evidence")

    project = _first_text((engineering_mission.get("project"), engineering_result.get("project")))
    repository = _first_text((engineering_mission.get("repository"), engineering_result.get("repository")))
    if not project:
        raise QAHandoffError("missing engineering project")
    if not repository:
        raise QAHandoffError("missing engineering repository")

    qa_mission = dict(engineering_mission)
    qa_mission.update({
        "agent_id": qa_agent_id.strip(),
        "mission_type": "qa",
        "qa_mode": "independent_review",
        "implementer_id": implementer_id,
        "engineering_run_id": engineering_result.get("run_id"),
        "engineering_project": project,
        "engineering_repository": repository,
        "engineering_branch": branch,
        "engineering_commit_sha": commit_sha,
        "reviewed_commit_sha": commit_sha,
        "project": project,
        "repository": repository,
        "work_branch": branch,
        "authorized_paths": list(engineering_mission.get("authorized_paths") or engineering_mission.get("allowed_paths") or ()),
        "allowed_paths": list(engineering_mission.get("allowed_paths") or engineering_mission.get("authorized_paths") or ()),
        "validation_commands": list(engineering_mission.get("validation_commands") or ()),
    })
    return qa_mission


def validate_qa_result(result: Any) -> tuple[str, tuple[str, ...]]:
    if not isinstance(result, dict):
        raise QAHandoffError("malformed QA dispatch result")
    verdict = result.get("verdict")
    if verdict not in {QA_ACCEPTED, QA_REJECTED}:
        raise QAHandoffError("malformed QA evidence: valid verdict missing")
    evidence_value = result.get("evidence")
    if not isinstance(evidence_value, (list, tuple)):
        raise QAHandoffError("malformed QA evidence: evidence bundle missing")
    evidence = tuple(str(item).strip() for item in evidence_value if str(item).strip())
    if not evidence:
        raise QAHandoffError("malformed QA evidence: evidence bundle empty")
    if result.get("success") is not True:
        raise QAHandoffError("QA dispatch failed")
    return verdict, evidence


def evaluate_merge_eligibility(*, status: str, target_repository: str, actual_repository: str, base_branch: str, work_branch: str, changed_files: Iterable[str], allowed_paths: Iterable[str], forbidden_paths: Iterable[str], acceptance_results: dict[str, bool], validation_results: dict[str, bool], handoff_report: dict, pr_exists: bool, pr_mergeable: bool, review_approved: bool, qa_accepted: bool) -> GateResult:
    reasons: list[str] = []
    if status != "review": reasons.append("work package must be in review state")
    if target_repository != actual_repository: reasons.append("repository does not match work package target")
    if base_branch != "main": reasons.append("base branch must remain main")
    if not work_branch.startswith("rvsc/"): reasons.append("work branch must use rvsc/ prefix")
    if work_branch == base_branch: reasons.append("direct development on base branch is prohibited")
    violations = validate_scope(changed_files, allowed_paths, forbidden_paths)
    if violations: reasons.append("out-of-scope paths: " + ", ".join(sorted(violations)))
    failed_acceptance = sorted(key for key, passed in acceptance_results.items() if not passed)
    if failed_acceptance: reasons.append("acceptance criteria failed: " + ", ".join(failed_acceptance))
    failed_validation = sorted(key for key, passed in validation_results.items() if not passed)
    if failed_validation: reasons.append("validation failed: " + ", ".join(failed_validation))
    reasons.extend(validate_handoff(handoff_report))
    if not pr_exists: reasons.append("pull request evidence missing")
    if not pr_mergeable: reasons.append("pull request is not mergeable")
    if not review_approved: reasons.append("required review approval missing")
    if not qa_accepted: reasons.append("QA acceptance missing")
    return GateResult(eligible=not reasons, reasons=tuple(reasons))
