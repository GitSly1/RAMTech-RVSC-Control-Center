from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from controller.engineering_environment import ControlledEngineeringEnvironment, EngineeringEnvironmentError


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "src").mkdir()
    return tmp_path


def test_write_and_read_are_limited_to_allowed_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    env = ControlledEngineeringEnvironment(repo, allowed_paths=("docs",))
    evidence = env.write_text("docs/result.md", "verified\n")
    assert env.read_text("docs/result.md") == "verified\n"
    assert evidence.relative_path == "docs/result.md"
    assert evidence.size == len(b"verified\n")
    assert len(evidence.sha256) == 64
    with pytest.raises(EngineeringEnvironmentError):
        env.write_text("src/forbidden.py", "x = 1\n")


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    env = ControlledEngineeringEnvironment(repo, allowed_paths=("docs",))
    with pytest.raises(EngineeringEnvironmentError):
        env.read_text("docs/../../outside.txt")


def test_unapproved_executable_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    env = ControlledEngineeringEnvironment(repo, allowed_paths=("docs",), allowed_executables=("git",))
    with pytest.raises(EngineeringEnvironmentError):
        env.run(("python", "-c", "print('no')"))


def test_git_operations_are_observable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    env = ControlledEngineeringEnvironment(repo, allowed_paths=("docs",), allowed_executables=("git",))
    env.write_text("docs/result.md", "evidence\n")
    status = env.git_status()
    assert status.returncode == 0
    assert "docs/" in status.stdout


def test_search_is_scoped(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "docs" / "allowed.md").write_text("golden evidence", encoding="utf-8")
    (repo / "src" / "hidden.py").write_text("golden evidence", encoding="utf-8")
    env = ControlledEngineeringEnvironment(repo, allowed_paths=("docs",))
    assert env.search_text("golden evidence") == ("docs/allowed.md",)
