from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .adapters import WorkerRequest, WorkerResult
from .engineering_environment import ControlledEngineeringEnvironment, EngineeringEnvironmentError


@dataclass(frozen=True)
class ValidationCommand:
    name: str
    argv: tuple[str, ...]


class EngineeringMissionRunner:
    """Binds an RVSC WorkerRequest to controlled repository operations and evidence."""

    def __init__(self, request: WorkerRequest, repo_root: str | Path, *, validations: Sequence[ValidationCommand]) -> None:
        self.request = request
        self.environment = ControlledEngineeringEnvironment(
            repo_root,
            allowed_paths=request.allowed_paths,
            allowed_executables=("python", "git"),
        )
        self.validations = tuple(validations)

    def preflight(self) -> tuple[str, ...]:
        evidence = [
            f"agent_id:{self.request.agent_id}",
            f"wp_id:{self.request.wp_id}",
            f"project:{self.request.project}",
            f"repository:{self.request.repository}",
        ]
        branch = self.environment.git_current_branch()
        if branch.returncode != 0:
            raise EngineeringEnvironmentError(branch.stderr.strip() or "unable to determine git branch")
        actual = branch.stdout.strip()
        if actual != self.request.work_branch:
            raise EngineeringEnvironmentError(f"branch mismatch: expected {self.request.work_branch}, got {actual or '<detached>'}")
        status = self.environment.git_material_status()
        if status.returncode != 0:
            raise EngineeringEnvironmentError(status.stderr.strip() or "git status failed")
        if status.stdout.strip():
            raise EngineeringEnvironmentError("repository must be clean before mission execution")
        evidence.extend((f"branch:{actual}", "repo_clean:true"))
        return tuple(evidence)

    def validate(self) -> tuple[str, ...]:
        evidence: list[str] = []
        for check in self.validations:
            result = self.environment.run(check.argv)
            evidence.append(f"validation:{check.name}:returncode:{result.returncode}")
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or "no output"
                raise EngineeringEnvironmentError(f"validation failed [{check.name}]: {detail}")
        return tuple(evidence)

    def evidence_after_change(self, changed_paths: Sequence[str]) -> tuple[str, ...]:
        evidence: list[str] = []
        for path in changed_paths:
            item = self.environment.file_evidence(path)
            evidence.extend((f"file:{item.relative_path}", f"sha256:{item.relative_path}:{item.sha256}", f"bytes:{item.relative_path}:{item.size}"))
        diff = self.environment.git_diff()
        if diff.returncode != 0:
            raise EngineeringEnvironmentError(diff.stderr.strip() or "git diff failed")
        evidence.append(f"diff_present:{str(bool(diff.stdout.strip())).lower()}")
        return tuple(evidence)

    def commit(
        self,
        changed_paths: Sequence[str],
        message: str,
        *,
        author_name: str,
        author_email: str,
    ) -> tuple[str, ...]:
        staged = self.environment.stage(changed_paths)
        if staged.returncode != 0:
            raise EngineeringEnvironmentError(staged.stderr.strip() or "git staging failed")
        committed = self.environment.commit(message, author_name=author_name, author_email=author_email)
        if committed.returncode != 0:
            raise EngineeringEnvironmentError(committed.stderr.strip() or committed.stdout.strip() or "git commit failed")
        head = self.environment.run(("git", "rev-parse", "HEAD"))
        if head.returncode != 0:
            raise EngineeringEnvironmentError(head.stderr.strip() or "unable to capture commit SHA")
        return (f"commit:{head.stdout.strip()}",)

    @staticmethod
    def blocked(exc: Exception) -> WorkerResult:
        return WorkerResult(success=False, summary=str(exc), evidence=("engineering_runner:blocked",), retryable=False)
