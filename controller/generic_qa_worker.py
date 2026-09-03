from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

RVSC_ROOT = Path(__file__).resolve().parents[1]
_ALLOWED_EXECUTABLES = {"python", "python3", "py", "pytest", "git"}
_READ_ONLY_GIT_COMMANDS = {
    "branch",
    "diff",
    "log",
    "rev-parse",
    "show",
    "status",
}
Checkpoint = Callable[[str, tuple[str, ...]], None]


def _record(checkpoint: Checkpoint | None, name: str, evidence: tuple[str, ...]) -> None:
    if checkpoint is not None:
        checkpoint(name, evidence)


def _reject(
    *,
    run_id: str,
    agent_id: str,
    branch: str | None,
    commit_sha: str | None,
    summary: str,
    evidence: list[str],
    validations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "run_id": run_id or None,
        "agent_id": agent_id,
        "verdict": "QA_REJECTED",
        "reviewed_branch": branch,
        "reviewed_commit_sha": commit_sha,
        "summary": summary,
        "evidence": evidence,
        "validations": validations if validations is not None else [],
        "retryable": False,
    }


def _git_value(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown Git error"
        raise RuntimeError(detail)
    return completed.stdout.strip()


def _authorized_path(repo_root: Path, raw_path: str) -> Path:
    if not raw_path.strip():
        raise ValueError("authorized path cannot be empty")
    relative = Path(raw_path)
    if relative.is_absolute():
        raise ValueError(f"authorized path must be repository-relative: {raw_path}")
    root = repo_root.resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"authorized path escapes repository: {raw_path}") from exc
    if not resolved.is_file():
        raise ValueError(f"required review evidence is missing: {raw_path}")
    return resolved


def _file_evidence(repo_root: Path, raw_path: str) -> str:
    path = _authorized_path(repo_root, raw_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    normalized = path.relative_to(repo_root.resolve()).as_posix()
    return f"inspected:{normalized}:sha256:{digest}"


def _is_allowed_executable(raw_executable: str) -> bool:
    if Path(raw_executable).name.lower() in _ALLOWED_EXECUTABLES:
        return True
    try:
        return Path(raw_executable).resolve() == Path(sys.executable).resolve()
    except OSError:
        return False


def _validated_commands(mission: dict[str, Any]) -> list[tuple[str, list[str]]]:
    raw_commands = mission.get("validation_commands")
    if not isinstance(raw_commands, list) or not raw_commands:
        raise ValueError("at least one validation command is required")
    commands: list[tuple[str, list[str]]] = []
    for index, raw in enumerate(raw_commands, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"validation command {index} must be an object")
        name = str(raw.get("name", "")).strip() or f"validation-{index}"
        argv = raw.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError(f"validation command {name} requires a non-empty string argv")
        executable = Path(argv[0]).name.lower()
        if not _is_allowed_executable(argv[0]):
            raise ValueError(f"validation command {name} uses uncontrolled executable {argv[0]}")
        if executable == "git":
            if len(argv) < 2 or argv[1].lower() not in _READ_ONLY_GIT_COMMANDS:
                raise ValueError(f"validation command {name} uses a non-read-only Git operation")
        commands.append((name, list(argv)))
    return commands


def _copy_repository(repo_root: Path, destination: Path) -> None:
    ignored_names = {".git", ".rvsc", "__pycache__", ".pytest_cache", ".mypy_cache"}

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in ignored_names or name.endswith(".pyc")}

    shutil.copytree(repo_root, destination, ignore=ignore)


def execute_mission(
    *,
    agent_id: str,
    agent_name: str,
    role: str,
    qa_eligible: bool,
    mission: dict[str, Any],
    checkpoint: Checkpoint | None = None,
    repo_root: Path = RVSC_ROOT,
) -> dict[str, Any]:
    run_id = str(mission.get("run_id", "")).strip()
    evidence = [f"qa_agent:{agent_id}", f"qa_name:{agent_name}", f"qa_role:{role}"]
    if not qa_eligible:
        evidence.append("authorization:denied")
        return _reject(
            run_id=run_id,
            agent_id=agent_id,
            branch=None,
            commit_sha=None,
            summary=f"{agent_id} is not authorized for independent QA execution",
            evidence=evidence,
        )
    evidence.append("authorization:qa_eligible")
    if not run_id:
        evidence.append("required_evidence:run_id:missing")
        return _reject(
            run_id="",
            agent_id=agent_id,
            branch=None,
            commit_sha=None,
            summary="required QA run_id is missing",
            evidence=evidence,
        )

    root = repo_root.resolve()
    branch: str | None = None
    commit_sha: str | None = None
    validations: list[dict[str, Any]] = []
    try:
        branch = _git_value(root, "branch", "--show-current")
        commit_sha = _git_value(root, "rev-parse", "HEAD")
        if not branch or not commit_sha:
            raise ValueError("reviewed Git branch and commit SHA are required")
        evidence.extend((f"reviewed_branch:{branch}", f"reviewed_commit_sha:{commit_sha}"))
        expected_branch = str(mission.get("work_branch", "")).strip()
        if expected_branch and expected_branch != branch:
            raise ValueError(f"reviewed branch mismatch: expected {expected_branch}, observed {branch}")
        expected_commit = str(mission.get("reviewed_commit_sha", mission.get("commit_sha", ""))).strip()
        if expected_commit and expected_commit != commit_sha:
            raise ValueError(f"reviewed commit mismatch: expected {expected_commit}, observed {commit_sha}")

        raw_paths = mission.get("allowed_paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise ValueError("authorized review paths are required")
        evidence_paths = mission.get("evidence_paths", [])
        if not isinstance(evidence_paths, list):
            raise ValueError("evidence_paths must be a list")
        review_paths = list(raw_paths) + list(evidence_paths)
        if not all(isinstance(item, str) for item in review_paths):
            raise ValueError("authorized review paths must be strings")
        for raw_path in dict.fromkeys(review_paths):
            evidence.append(_file_evidence(root, raw_path))

        commands = _validated_commands(mission)
        _record(checkpoint, "qa_inspection_complete", (f"run_id:{run_id}", f"branch:{branch}", f"commit:{commit_sha}"))
        timeout = int(mission.get("validation_timeout_seconds", 900))
        if timeout < 1 or timeout > 3600:
            raise ValueError("validation timeout must be between 1 and 3600 seconds")

        with tempfile.TemporaryDirectory(prefix="rvsc-qa-") as temporary:
            validation_root = Path(temporary) / "repository"
            _copy_repository(root, validation_root)
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            for name, argv in commands:
                completed = subprocess.run(
                    argv,
                    cwd=validation_root,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=timeout,
                    env=environment,
                )
                result = {
                    "name": name,
                    "argv": argv,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-12000:],
                    "stderr": completed.stderr[-12000:],
                }
                validations.append(result)
                evidence.append(f"validation:{name}:exit:{completed.returncode}")
                _record(checkpoint, "qa_validation_observed", (f"run_id:{run_id}", f"validation:{name}", f"exit:{completed.returncode}"))
                if completed.returncode != 0:
                    return _reject(
                        run_id=run_id,
                        agent_id=agent_id,
                        branch=branch,
                        commit_sha=commit_sha,
                        summary=f"validation failed: {name}",
                        evidence=evidence,
                        validations=validations,
                    )

        evidence.extend(("source_execution:isolated_copy", "verdict:QA_ACCEPTED"))
        _record(checkpoint, "qa_accepted", (f"run_id:{run_id}", f"branch:{branch}", f"commit:{commit_sha}"))
        return {
            "success": True,
            "run_id": run_id,
            "agent_id": agent_id,
            "verdict": "QA_ACCEPTED",
            "reviewed_branch": branch,
            "reviewed_commit_sha": commit_sha,
            "summary": f"independent QA accepted {branch} at {commit_sha}",
            "evidence": evidence,
            "validations": validations,
            "retryable": False,
        }
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        evidence.extend((f"qa_failure:{exc}", "verdict:QA_REJECTED"))
        _record(checkpoint, "qa_rejected", (f"run_id:{run_id}", f"failure:{exc}"))
        return _reject(
            run_id=run_id,
            agent_id=agent_id,
            branch=branch,
            commit_sha=commit_sha,
            summary=str(exc),
            evidence=evidence,
            validations=validations,
        )
