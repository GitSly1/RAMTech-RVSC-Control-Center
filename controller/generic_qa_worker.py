from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

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
_FULL_COMMIT_SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")
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


def _git_value(repo_root: Path, *args: str, timeout: int = 30) -> str:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        env=environment,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown Git error"
        raise RuntimeError(detail)
    return completed.stdout.strip()


def _normalized_origin_url(workspace_root: Path, origin_url: str) -> str:
    if "://" in origin_url or re.match(r"^[^/\\]+@[^:]+:", origin_url):
        return origin_url
    path = Path(origin_url).expanduser()
    if not path.is_absolute():
        path = workspace_root / path
    return str(path.resolve())


def _mission_text(mission: dict[str, Any], key: str) -> str:
    value = mission.get(key)
    return value.strip() if isinstance(value, str) else ""


def _requested_commit(mission: dict[str, Any]) -> str:
    reviewed_commit = _mission_text(mission, "reviewed_commit_sha")
    engineering_commit = _mission_text(mission, "engineering_commit_sha")
    legacy_commit = _mission_text(mission, "commit_sha")

    if reviewed_commit:
        for field, value in (("engineering_commit_sha", engineering_commit), ("commit_sha", legacy_commit)):
            if value and value.lower() != reviewed_commit.lower():
                raise ValueError(f"{field} does not match reviewed_commit_sha")
        return reviewed_commit
    if engineering_commit:
        raise ValueError("reviewed_commit_sha is required when engineering_commit_sha is supplied")
    return legacy_commit


@contextmanager
def _review_repository(
    workspace_root: Path,
    expected_branch: str,
    expected_commit: str,
) -> Iterator[tuple[Path, str, str, tuple[str, ...]]]:
    if not expected_commit:
        branch = _git_value(workspace_root, "branch", "--show-current")
        commit_sha = _git_value(workspace_root, "rev-parse", "HEAD")
        if not branch or not commit_sha:
            raise ValueError("reviewed Git branch and commit SHA are required")
        if expected_branch and expected_branch != branch:
            raise ValueError(f"reviewed branch mismatch: expected {expected_branch}, observed {branch}")
        yield workspace_root, branch, commit_sha, ("target_acquisition:existing_workspace",)
        return

    if not expected_branch:
        raise ValueError("requested review branch is required when a commit SHA is specified")
    if not _FULL_COMMIT_SHA.fullmatch(expected_commit):
        raise ValueError("requested commit SHA is invalid or unavailable")
    try:
        _git_value(workspace_root, "check-ref-format", f"refs/heads/{expected_branch}")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise ValueError("requested branch is invalid or unavailable") from exc
    try:
        origin_url = _normalized_origin_url(
            workspace_root,
            _git_value(workspace_root, "remote", "get-url", "origin"),
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise ValueError("origin is unavailable for requested target acquisition") from exc

    with tempfile.TemporaryDirectory(prefix="rvsc-qa-target-") as temporary:
        acquired_root = Path(temporary) / "repository"
        acquired_root.mkdir()
        try:
            _git_value(acquired_root, "init")
            _git_value(acquired_root, "remote", "add", "origin", origin_url)
            remote_ref = f"refs/remotes/origin/{expected_branch}"
            refspec = f"+refs/heads/{expected_branch}:{remote_ref}"
            _git_value(acquired_root, "fetch", "--no-tags", "origin", refspec, timeout=120)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            raise ValueError("requested branch is unavailable from origin") from exc

        normalized_commit = expected_commit.lower()
        try:
            fetched_tip = _git_value(acquired_root, "rev-parse", "--verify", f"{remote_ref}^{{commit}}").lower()
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            raise ValueError("requested branch could not be verified after fetch") from exc
        try:
            verified_commit = _git_value(
                acquired_root,
                "rev-parse",
                "--verify",
                f"{normalized_commit}^{{commit}}",
            ).lower()
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            raise ValueError("requested commit SHA is invalid or unavailable") from exc
        if verified_commit != normalized_commit:
            raise ValueError("requested commit SHA could not be verified exactly")
        if fetched_tip != normalized_commit:
            raise ValueError(
                f"requested branch/commit mismatch: origin branch tip is {fetched_tip}, not {normalized_commit}"
            )
        try:
            _git_value(acquired_root, "checkout", "--detach", normalized_commit)
            checked_out_commit = _git_value(acquired_root, "rev-parse", "HEAD").lower()
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            raise ValueError("requested commit could not be prepared for isolated QA review") from exc
        if checked_out_commit != normalized_commit:
            raise ValueError("acquired review target does not match the requested commit")

        yield acquired_root, expected_branch, normalized_commit, (
            "target_acquisition:origin_fetch",
            f"target_ref:{remote_ref}",
            "target_verification:branch_tip_matches_commit",
            "target_checkout:detached",
        )


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
        expected_branch = str(mission.get("work_branch", "")).strip()
        expected_commit = _requested_commit(mission)
        with _review_repository(root, expected_branch, expected_commit) as target:
            review_root, branch, commit_sha, acquisition_evidence = target
            evidence.extend(acquisition_evidence)
            evidence.extend((f"reviewed_branch:{branch}", f"reviewed_commit_sha:{commit_sha}"))

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
                evidence.append(_file_evidence(review_root, raw_path))

            commands = _validated_commands(mission)
            _record(
                checkpoint,
                "qa_inspection_complete",
                (f"run_id:{run_id}", f"branch:{branch}", f"commit:{commit_sha}"),
            )
            timeout = int(mission.get("validation_timeout_seconds", 900))
            if timeout < 1 or timeout > 3600:
                raise ValueError("validation timeout must be between 1 and 3600 seconds")

            with tempfile.TemporaryDirectory(prefix="rvsc-qa-") as temporary:
                validation_root = Path(temporary) / "repository"
                _copy_repository(review_root, validation_root)
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
                    _record(
                        checkpoint,
                        "qa_validation_observed",
                        (f"run_id:{run_id}", f"validation:{name}", f"exit:{completed.returncode}"),
                    )
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
            _record(
                checkpoint,
                "qa_accepted",
                (f"run_id:{run_id}", f"branch:{branch}", f"commit:{commit_sha}"),
            )
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
