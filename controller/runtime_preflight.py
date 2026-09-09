from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence


DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:7b-instruct"


class StartupPreflightError(RuntimeError):
    """Raised when the runtime execution environment is not safe to start."""


def _detail(value: object, limit: int = 700) -> str:
    text = str(value or "").strip().replace("\r\n", "\n")
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _ollama_tags_url(generate_url: str) -> str:
    value = generate_url.strip().rstrip("/")
    suffix = "/api/generate"

    if value.endswith(suffix):
        return value[:-len(suffix)] + "/api/tags"

    if value.endswith("/api"):
        return value + "/tags"

    return value + "/api/tags"


def run_startup_preflight(
    repository_mappings: Mapping[str, str],
    *,
    mission_store_path: Optional[str | Path] = None,
    environ: Optional[Mapping[str, str]] = None,
    executable: Optional[str] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
    runner: Callable[..., object] = subprocess.run,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> tuple[str, ...]:
    env = os.environ if environ is None else environ
    python_executable = str(executable or sys.executable)
    evidence: list[str] = []
    failures: list[str] = []

    python_path = Path(python_executable)

    if not python_path.is_file():
        failures.append(
            "python interpreter unavailable: %s" % python_executable
        )
    else:
        evidence.append("python:%s" % python_path)

    git_executable = which("git")

    if not git_executable:
        failures.append("git executable unavailable")
    else:
        evidence.append("git:%s" % git_executable)

    configured = {
        str(key): str(value).strip()
        for key, value in repository_mappings.items()
        if str(value).strip()
    }

    if not configured:
        failures.append("no controlled repository mapping configured")

    checked_paths: set[str] = set()

    runtime_root = str(env.get("RVSC_ROOT", "") or "").strip()

    path_checks: list[tuple[str, str, bool]] = []

    if runtime_root:
        path_checks.append(("runtime_root", runtime_root, True))

    for key, value in sorted(configured.items()):
        path_checks.append(("repository:%s" % key, value, True))

    runtime_state = str(
        env.get("RVSC_RUNTIME_STATE_DIR", "") or ""
    ).strip()

    if runtime_state:
        path_checks.append(("runtime_state", runtime_state, False))

    for label, raw_path, require_git in path_checks:
        path = Path(raw_path).expanduser()

        try:
            resolved = path.resolve()
        except OSError:
            resolved = path

        identity = str(resolved).lower()

        if not path.is_dir():
            failures.append("%s unavailable: %s" % (label, path))
            continue

        if not os.access(path, os.R_OK):
            failures.append("%s is not readable: %s" % (label, path))
            continue

        if label == "runtime_state" and not os.access(path, os.W_OK):
            failures.append("%s is not writable: %s" % (label, path))
            continue

        evidence.append("%s:%s" % (label, resolved))

        if not require_git or identity in checked_paths:
            continue

        checked_paths.add(identity)

        if not git_executable:
            continue

        try:
            result = runner(
                [
                    git_executable,
                    "-C",
                    str(path),
                    "status",
                    "--porcelain",
                    "--untracked-files=no",
                ],
                text=True,
                capture_output=True,
                timeout=10,
                shell=False,
                check=False,
            )
        except Exception as exc:
            failures.append(
                "%s git access failed: %s: %s"
                % (label, type(exc).__name__, _detail(exc))
            )
            continue

        returncode = int(getattr(result, "returncode", 1))

        if returncode != 0:
            stderr = _detail(getattr(result, "stderr", ""))
            stdout = _detail(getattr(result, "stdout", ""))
            reason = stderr or stdout or "git status failed"
            failures.append("%s git access failed: %s" % (label, reason))
            continue

        evidence.append("%s:git_access:true" % label)

    if mission_store_path is not None:
        store = Path(mission_store_path).expanduser()
        parent = store.parent

        if not parent.is_dir():
            failures.append(
                "mission-store parent unavailable: %s" % parent
            )
        elif not os.access(parent, os.W_OK):
            failures.append(
                "mission-store parent is not writable: %s" % parent
            )
        else:
            evidence.append("mission_store_parent:%s" % parent)

    provider = str(
        env.get("RVSC_AI_PROVIDER", "ollama") or "ollama"
    ).strip().lower()

    evidence.append("provider:%s" % provider)

    if provider == "openai":
        if not str(env.get("OPENAI_API_KEY", "") or "").strip():
            failures.append(
                "OPENAI_API_KEY is required for OpenAI engineering"
            )
        else:
            evidence.append("provider_credential:true")

    elif provider == "ollama":
        generate_url = str(
            env.get("RVSC_OLLAMA_URL", DEFAULT_OLLAMA_URL)
            or DEFAULT_OLLAMA_URL
        ).strip()

        model = str(
            env.get("RVSC_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
            or DEFAULT_OLLAMA_MODEL
        ).strip()

        tags_url = _ollama_tags_url(generate_url)

        try:
            with opener(tags_url, timeout=5) as response:
                payload = json.loads(
                    response.read().decode("utf-8")
                )
        except Exception as exc:
            failures.append(
                "Ollama service unavailable at %s: %s: %s"
                % (tags_url, type(exc).__name__, _detail(exc))
            )
        else:
            models = payload.get("models", [])
            names = {
                str(
                    item.get("name")
                    or item.get("model")
                    or ""
                ).strip()
                for item in models
                if isinstance(item, Mapping)
            }

            names.discard("")

            if model not in names:
                failures.append(
                    "required Ollama model unavailable: %s" % model
                )
            else:
                evidence.append("ollama_service:true")
                evidence.append("ollama_model:%s" % model)

    else:
        failures.append(
            "unsupported engineering provider: %s" % provider
        )

    if failures:
        raise StartupPreflightError(
            "startup preflight failed: " + "; ".join(failures)
        )

    return tuple(evidence)
