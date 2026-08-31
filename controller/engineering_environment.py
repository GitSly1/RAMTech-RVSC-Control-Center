from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


class EngineeringEnvironmentError(RuntimeError):
    """Raised when a controlled engineering operation violates policy or fails."""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class FileEvidence:
    relative_path: str
    sha256: str
    size: int


class ControlledEngineeringEnvironment:
    """Policy-bounded repository tools for Golden Agent qualification.

    The environment intentionally does not expose arbitrary shell execution. Commands
    are argv sequences whose executable must be explicitly allowed by the work package
    runtime. File operations are constrained to configured repository-relative roots.
    """

    def __init__(
        self,
        repo_root: str | Path,
        *,
        allowed_paths: Sequence[str],
        allowed_executables: Iterable[str] = ("python", "git"),
        timeout_seconds: int = 120,
    ) -> None:
        root = Path(repo_root).expanduser().resolve()
        if not root.is_dir():
            raise EngineeringEnvironmentError(f"repository root does not exist: {root}")
        if not (root / ".git").exists():
            raise EngineeringEnvironmentError(f"not a git repository: {root}")
        self.repo_root = root
        self.allowed_paths = tuple(self._normalize_allowed_path(item) for item in allowed_paths)
        if not self.allowed_paths:
            raise EngineeringEnvironmentError("at least one allowed path is required")
        self.allowed_executables = frozenset(Path(item).name.lower() for item in allowed_executables)
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _normalize_allowed_path(value: str) -> str:
        normalized = value.replace("\\", "/").strip().strip("/")
        if not normalized or normalized == "." or ".." in Path(normalized).parts:
            raise EngineeringEnvironmentError(f"invalid allowed path: {value!r}")
        return normalized

    def _resolve(self, relative_path: str) -> Path:
        candidate_rel = relative_path.replace("\\", "/").strip().lstrip("/")
        if not candidate_rel or ".." in Path(candidate_rel).parts:
            raise EngineeringEnvironmentError(f"unsafe path: {relative_path!r}")
        candidate = (self.repo_root / candidate_rel).resolve()
        try:
            candidate.relative_to(self.repo_root)
        except ValueError as exc:
            raise EngineeringEnvironmentError("path escapes repository") from exc
        permitted = any(
            candidate_rel == prefix or candidate_rel.startswith(prefix + "/")
            for prefix in self.allowed_paths
        )
        if not permitted:
            raise EngineeringEnvironmentError(f"path outside work-package scope: {candidate_rel}")
        return candidate

    def read_text(self, relative_path: str) -> str:
        return self._resolve(relative_path).read_text(encoding="utf-8")

    def write_text(self, relative_path: str, content: str) -> FileEvidence:
        target = self._resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return self.file_evidence(relative_path)

    def file_evidence(self, relative_path: str) -> FileEvidence:
        target = self._resolve(relative_path)
        payload = target.read_bytes()
        return FileEvidence(
            relative_path=relative_path.replace("\\", "/"),
            sha256=hashlib.sha256(payload).hexdigest(),
            size=len(payload),
        )

    def search_text(self, needle: str, *, suffixes: Sequence[str] = (".py", ".md", ".yaml", ".yml", ".json")) -> tuple[str, ...]:
        if not needle:
            raise EngineeringEnvironmentError("search term is empty")
        hits: list[str] = []
        for prefix in self.allowed_paths:
            base = self.repo_root / prefix
            candidates = [base] if base.is_file() else base.rglob("*") if base.exists() else []
            for path in candidates:
                if not path.is_file() or (suffixes and path.suffix.lower() not in suffixes):
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                if needle in text:
                    hits.append(path.relative_to(self.repo_root).as_posix())
        return tuple(sorted(set(hits)))

    def run(self, argv: Sequence[str], *, timeout_seconds: int | None = None) -> CommandResult:
        if not argv:
            raise EngineeringEnvironmentError("command is empty")
        executable = Path(argv[0]).name.lower()
        if executable not in self.allowed_executables:
            raise EngineeringEnvironmentError(f"executable not allowed: {argv[0]}")
        env = os.environ.copy()
        completed = subprocess.run(
            list(argv),
            cwd=self.repo_root,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_seconds or self.timeout_seconds,
            shell=False,
            check=False,
        )
        return CommandResult(tuple(argv), completed.returncode, completed.stdout, completed.stderr)

    def git_status(self) -> CommandResult:
        return self.run(("git", "status", "--short"))

    def git_diff(self) -> CommandResult:
        return self.run(("git", "diff", "--"))

    def git_current_branch(self) -> CommandResult:
        return self.run(("git", "branch", "--show-current"))

    def commit(self, message: str, *, author_name: str = "DEV-001 Daniel", author_email: str = "dev-001@rvsc.local") -> CommandResult:
        if not message.strip():
            raise EngineeringEnvironmentError("commit message is empty")
        # Commit only already-staged changes. Staging policy remains controlled by the
        # work-package runner so Daniel cannot silently expand scope.
        return self.run((
            "git", "-c", f"user.name={author_name}", "-c", f"user.email={author_email}",
            "commit", "-m", message,
        ))
