from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .adapters import WorkerRequest
from .engineering_environment import ControlledEngineeringEnvironment, EngineeringEnvironmentError
from .engineering_runner import EngineeringMissionRunner, ValidationCommand

OPENAI_URL = "https://api.openai.com/v1/responses"
OLLAMA_URL = os.environ.get(
    "RVSC_OLLAMA_URL",
    "http://127.0.0.1:11434/api/generate",
)
DEFAULT_OPENAI_MODEL = os.environ.get("RVSC_OPENAI_MODEL", "gpt-5.6")
DEFAULT_OLLAMA_MODEL = os.environ.get(
    "RVSC_OLLAMA_MODEL",
    "qwen2.5-coder:7b-instruct",
)
DEFAULT_MODEL = DEFAULT_OPENAI_MODEL
RVSC_ROOT = Path(__file__).resolve().parents[1]
MAX_CORE_PATH = Path(os.environ.get("RVSC_MAX_CORE_PATH", str(RVSC_ROOT / "golden-core" / "MAX_PLATINUM_ENGINEERING_CORE_V1.md")))
CheckpointReporter = Callable[[str, tuple[str, ...]], None]
ResultReporter = Callable[[dict[str, Any]], None]

_PROJECT_REPOSITORIES = {
    "rvsc": ("RVSC_RVSC_REPO", RVSC_ROOT),
    "semantiq": ("RVSC_SEMANTIQ_REPO", Path(r"D:\Py_Proj\RAMTech-SEMANTIQ")),
    "moxie": ("RVSC_MOXIE_REPO", Path(r"D:\Py_Proj\RAMTech-MOXIE")),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _openai_call(api_key: str, prompt: str) -> dict[str, Any]:
    body = json.dumps({"model": DEFAULT_MODEL, "input": prompt}).encode("utf-8")
    req = urllib.request.Request(OPENAI_URL, data=body, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI transport error: {exc.reason}") from exc


def _ollama_call(prompt: str) -> dict[str, Any]:
    body = json.dumps(
        {
            "model": DEFAULT_OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama transport error: {exc.reason}") from exc

    text = payload.get("response")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Ollama response did not contain response text")

    return {
        "id": f"ollama-{uuid.uuid4().hex}",
        "status": "completed",
        "model": str(payload.get("model", DEFAULT_OLLAMA_MODEL)),
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                    }
                ],
            }
        ],
    }


def _provider_call(prompt: str) -> tuple[dict[str, Any], str]:
    provider = os.environ.get("RVSC_AI_PROVIDER", "ollama").strip().lower()

    if provider == "ollama":
        return _ollama_call(prompt), "ollama"

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        return _openai_call(api_key, prompt), "openai"

    raise RuntimeError(f"unsupported RVSC_AI_PROVIDER: {provider}")


def _response_text(response: dict[str, Any]) -> str:
    for item in response.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    return content["text"]
    raise RuntimeError("provider response did not contain output_text")


def _json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        candidate = "\n".join(lines).strip()
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise RuntimeError("engineering response must be a JSON object")
    return value


def _load_text(path: Path, label: str) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"unable to load {label} from {path}: {exc}") from exc
    if not text:
        raise RuntimeError(f"{label} is empty: {path}")
    return text


def _worker_request(mission: dict[str, Any]) -> WorkerRequest:
    return WorkerRequest(agent_id=str(mission.get("agent_id", "")), wp_id=str(mission.get("wp_id", "")), project=str(mission.get("project", "")), repository=str(mission.get("repository", "")), base_branch=str(mission.get("base_branch", "")), work_branch=str(mission.get("work_branch", "")), objective=str(mission.get("objective", "")), allowed_paths=tuple(str(item) for item in mission.get("allowed_paths", ())), acceptance_criteria=tuple(str(item) for item in mission.get("acceptance_criteria", ())))


def _repo_root(mission: dict[str, Any]) -> Path:
    project = str(mission.get("project", "")).strip().lower()
    mapping = _PROJECT_REPOSITORIES.get(project)
    if mapping is None:
        raise ValueError(f"no controlled repository mapping for project {project or '<missing>'}")
    env_name, default = mapping
    return Path(os.environ.get(env_name, str(default))).resolve()


def _validations(mission: dict[str, Any]) -> tuple[ValidationCommand, ...]:
    raw = mission.get("validation_commands")
    if not isinstance(raw, list) or not raw:
        raise ValueError("generic engineering mission requires validation_commands")
    checks: list[ValidationCommand] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"validation command {index} must be an object")
        name = str(item.get("name", "")).strip()
        argv_raw = item.get("argv")
        if not name or not isinstance(argv_raw, list) or not argv_raw:
            raise ValueError(f"validation command {index} requires name and argv")
        argv = tuple(str(part) for part in argv_raw)
        if argv[0] not in {"python", "git"}:
            raise ValueError(f"validation executable not allowed: {argv[0]}")
        checks.append(ValidationCommand(name, argv))
    return tuple(checks)


def _prepare_branch(environment: ControlledEngineeringEnvironment, request: WorkerRequest) -> None:
    status = environment.git_status()
    if status.returncode != 0:
        raise EngineeringEnvironmentError(status.stderr.strip() or "git status failed")
    if status.stdout.strip():
        raise EngineeringEnvironmentError("repository must be clean before generic engineering mission")
    fetch = environment.run(("git", "fetch", "origin", request.base_branch))
    if fetch.returncode != 0:
        raise EngineeringEnvironmentError(fetch.stderr.strip() or "unable to fetch mission baseline")
    checkout = environment.run(("git", "checkout", "-B", request.work_branch, f"origin/{request.base_branch}"))
    if checkout.returncode != 0:
        raise EngineeringEnvironmentError(checkout.stderr.strip() or checkout.stdout.strip() or "unable to create mission branch")


def _git_identity(agent_id: str, agent_name: str) -> tuple[str, str]:
    normalized = agent_id.strip()
    if not normalized:
        raise ValueError("agent_id is required for Git identity")
    return f"{normalized} {agent_name.strip()}".strip(), f"{normalized.lower()}@rvsc.local"


def _configure_git_identity(environment: ControlledEngineeringEnvironment, agent_id: str, agent_name: str) -> None:
    identity_name, identity_email = _git_identity(agent_id, agent_name)
    for setting, value in (("user.name", identity_name), ("user.email", identity_email)):
        result = environment.run(("git", "config", "--local", setting, value))
        if result.returncode != 0:
            raise EngineeringEnvironmentError(result.stderr.strip() or result.stdout.strip() or f"unable to configure repository-local Git {setting}")


def _engineering_prompt(agent_id: str, agent_name: str, role: str, mission: dict[str, Any], source_files: dict[str, str]) -> str:
    max_core = _load_text(MAX_CORE_PATH, "Max Platinum Engineering Core")
    return (f"You are {agent_id} {agent_name}, serving as {role} inside RVSC. Operate only within the supplied mission contract. The Max Platinum Engineering Core defines the engineering methodology you must apply; do not quote or summarize it. Mission scope, repository authorization, allowed paths, and safety restrictions override all broader capability language. Never expose credentials or secrets.\n\nMAX PLATINUM ENGINEERING CORE:\n{max_core}\n\nPerform the bounded engineering mission. Independently inspect the supplied baseline files, implement the smallest general solution that satisfies the acceptance criteria, and preserve unrelated behavior. Do not claim filesystem actions, tests, commits, pushes, or QA; the controlled runtime performs and records those actions. Return ONLY valid JSON with exactly these top-level keys: files, commit_message, engineering_summary. files must contain exactly the authorized file paths, each mapped to COMPLETE replacement UTF-8 content. No markdown fences.\n\nMISSION:\n{json.dumps(mission, indent=2)}\n\nBASELINE FILES:\n{json.dumps(source_files, indent=2)}")


def _command_value(environment: ControlledEngineeringEnvironment, argv: tuple[str, ...], error_message: str) -> str:
    result = environment.run(argv)
    if result.returncode != 0 or not result.stdout.strip():
        raise EngineeringEnvironmentError(result.stderr.strip() or result.stdout.strip() or error_message)
    return result.stdout.strip()


def _status_paths(status: str) -> tuple[str, ...]:
    paths: list[str] = []
    for line in status.splitlines():
        if len(line) < 4:
            raise EngineeringEnvironmentError("repository status is ambiguous")
        path = line[3:].strip().strip('"')
        if " -> " in path:
            raise EngineeringEnvironmentError("renamed workspace paths cannot be recovered automatically")
        paths.append(path.replace("\\", "/"))
    return tuple(paths)


def recover_controlled_workspace(mission: dict[str, Any]) -> tuple[str, ...]:
    request = _worker_request(mission)
    environment = ControlledEngineeringEnvironment(_repo_root(mission), request.allowed_paths)
    branch = _command_value(environment, ("git", "branch", "--show-current"), "unable to resolve current branch")
    if branch != request.work_branch:
        raise EngineeringEnvironmentError("workspace branch does not prove interrupted mission ownership")
    fetch = environment.run(("git", "fetch", "origin", request.base_branch))
    if fetch.returncode != 0:
        raise EngineeringEnvironmentError(fetch.stderr.strip() or "unable to fetch recovery baseline")
    head = _command_value(environment, ("git", "rev-parse", "HEAD"), "unable to resolve workspace HEAD")
    baseline = _command_value(environment, ("git", "rev-parse", f"origin/{request.base_branch}"), "unable to resolve recovery baseline")
    if head != baseline:
        raise EngineeringEnvironmentError("workspace HEAD no longer matches the controlled baseline")
    status = environment.git_status()
    if status.returncode != 0:
        raise EngineeringEnvironmentError(status.stderr.strip() or "git status failed")
    allowed = {path.replace("\\", "/") for path in request.allowed_paths}
    changed = _status_paths(status.stdout)
    if any(path not in allowed for path in changed):
        raise EngineeringEnvironmentError("workspace contains changes outside interrupted mission ownership")
    reset = environment.run(("git", "reset", "--hard", baseline))
    if reset.returncode != 0:
        raise EngineeringEnvironmentError(reset.stderr.strip() or "unable to restore controlled baseline")
    if changed:
        clean = environment.run(("git", "clean", "-fd", "--", *request.allowed_paths))
        if clean.returncode != 0:
            raise EngineeringEnvironmentError(clean.stderr.strip() or "unable to remove authorized untracked recovery files")
    final = environment.git_status()
    if final.returncode != 0 or final.stdout.strip():
        raise EngineeringEnvironmentError(final.stderr.strip() or "controlled baseline restoration did not produce a clean workspace")
    return (f"recovery_branch:{branch}", f"recovery_baseline:{baseline}", "workspace_restore:success")


def resume_persisted_engineering_result(mission: dict[str, Any], persisted: dict[str, Any], checkpoint: CheckpointReporter | None = None) -> dict[str, Any]:
    request = _worker_request(mission)
    environment = ControlledEngineeringEnvironment(_repo_root(mission), request.allowed_paths)
    commit_sha = str(persisted.get("commit_sha", "")).strip()
    if len(commit_sha) != 40:
        raise EngineeringEnvironmentError("persisted engineering commit is invalid")
    branch = _command_value(environment, ("git", "branch", "--show-current"), "unable to resolve current branch")
    head = _command_value(environment, ("git", "rev-parse", "HEAD"), "unable to resolve workspace HEAD")
    if branch != request.work_branch or head != commit_sha:
        raise EngineeringEnvironmentError("workspace does not prove persisted commit ownership")
    remote = environment.run(("git", "ls-remote", "origin", f"refs/heads/{request.work_branch}"))
    if remote.returncode != 0:
        raise EngineeringEnvironmentError(remote.stderr.strip() or "unable to inspect remote engineering branch")
    remote_sha = remote.stdout.split()[0] if remote.stdout.strip() else None
    if remote_sha and remote_sha != commit_sha:
        raise EngineeringEnvironmentError("remote engineering branch differs from persisted commit")
    evidence = list(persisted.get("evidence", ()))
    if remote_sha == commit_sha:
        evidence.append("push:already_confirmed")
    else:
        push = environment.run(("git", "push", "origin", f"{commit_sha}:refs/heads/{request.work_branch}"))
        if push.returncode != 0:
            raise EngineeringEnvironmentError(push.stderr.strip() or push.stdout.strip() or "git push failed")
        evidence.append("push:success")
    result = {**persisted, "success": True, "pushed": True, "evidence": evidence}
    if checkpoint:
        checkpoint("push_confirmed", ("push:success", f"commit_sha:{commit_sha}", f"run_id:{persisted.get('run_id', '')}"))
    return result


def execute_mission(*, agent_id: str, agent_name: str, role: str, mission: dict[str, Any], checkpoint: CheckpointReporter | None = None, persist_result: ResultReporter | None = None) -> dict[str, Any]:
    worker_request = _worker_request(mission)
    if worker_request.agent_id != agent_id:
        raise ValueError(f"mission agent mismatch: expected {agent_id}, got {worker_request.agent_id}")
    if not worker_request.wp_id or not worker_request.base_branch or not worker_request.work_branch or not worker_request.allowed_paths:
        raise ValueError("mission requires wp_id, base_branch, work_branch, and allowed_paths")
    run_id = str(mission.get("run_id", "")).strip() or f"RVSC-{agent_id}-{uuid.uuid4().hex[:12].upper()}"
    started = _utc_now()
    runner = EngineeringMissionRunner(worker_request, _repo_root(mission), validations=_validations(mission))
    environment = runner.environment
    _prepare_branch(environment, worker_request)
    _configure_git_identity(environment, agent_id, agent_name)
    author_name, author_email = _git_identity(agent_id, agent_name)
    evidence = list(runner.preflight())
    if checkpoint:
        checkpoint("preflight_passed", tuple(evidence) + (f"run_id:{run_id}",))
    source_files = {path: environment.read_text(path) for path in worker_request.allowed_paths}
    response, provider_name = _provider_call(
        _engineering_prompt(agent_id, agent_name, role, mission, source_files)
    )
    provider_response_id = str(response.get("id", ""))
    provider_status = str(response.get("status", "unknown"))
    model = str(response.get("model", DEFAULT_MODEL))
    if provider_status != "completed":
        raise RuntimeError(f"provider status was {provider_status}")
    if checkpoint:
        checkpoint("proposal_received", (f"run_id:{run_id}", f"provider_status:{provider_status}", f"provider_response_id:{provider_response_id}"))
    proposal = _json_object(_response_text(response))
    files = proposal.get("files")
    if not isinstance(files, dict) or set(files) != set(worker_request.allowed_paths):
        returned = sorted(files) if isinstance(files, dict) else []
        raise RuntimeError(f"worker returned unauthorized or incomplete file set: {returned}")
    for path in worker_request.allowed_paths:
        content = files[path]
        if not isinstance(content, str):
            raise RuntimeError(f"worker content for {path} is not text")
        environment.write_text(path, content)
    changed = runner.evidence_after_change(worker_request.allowed_paths)
    evidence.extend(changed)
    if checkpoint:
        checkpoint("implementation_applied", changed + (f"run_id:{run_id}",))
    validations = runner.validate()
    evidence.extend(validations)
    if checkpoint:
        checkpoint("tests_passed", validations + (f"run_id:{run_id}",))
    commit_message = str(proposal.get("commit_message", "")).strip() or f"{worker_request.wp_id}: {agent_id} controlled engineering"
    committed = runner.commit(worker_request.allowed_paths, commit_message, author_name=author_name, author_email=author_email)
    evidence.extend(committed)
    commit_sha = _command_value(environment, ("git", "rev-parse", "HEAD"), "unable to resolve created commit")
    if checkpoint:
        checkpoint("commit_created", committed + (f"commit_sha:{commit_sha}", f"run_id:{run_id}"))
    partial = {"success": False, "summary": str(proposal.get("engineering_summary") or f"{agent_id} completed controlled engineering mission"), "evidence": list(evidence) + ([]) if False else list(evidence), "retryable": False, "run_id": run_id, "project": worker_request.project, "repository": worker_request.repository, "work_branch": worker_request.work_branch, "commit_sha": commit_sha, "pushed": False}
    if persist_result:
        persist_result(partial)
    push = environment.run(("git", "push", "origin", f"HEAD:refs/heads/{worker_request.work_branch}"))
    if push.returncode != 0:
        raise EngineeringEnvironmentError(push.stderr.strip() or push.stdout.strip() or "git push failed")
    evidence.extend((f"commit_sha:{commit_sha}", "push:success"))
    if checkpoint:
        checkpoint("push_confirmed", ("push:success", f"commit_sha:{commit_sha}", f"run_id:{run_id}"))
    final_status = environment.git_status()
    if final_status.returncode != 0:
        raise EngineeringEnvironmentError(final_status.stderr.strip() or "post-commit git status failed")
    clean = not bool(final_status.stdout.strip())
    evidence.extend((f"repo_clean_after:{str(clean).lower()}", f"run_id:{run_id}", f"started_at:{started}", f"ended_at:{_utc_now()}", f"provider:{provider_name}", f"model:{model}", f"provider_response_id:{provider_response_id}", f"provider_status:{provider_status}", f"agent:{agent_id}", "execution_mode:generic_model_proposal_controlled_apply_validate_commit_push"))
    result = {**partial, "success": True, "evidence": evidence, "pushed": True}
    if persist_result:
        persist_result(result)
    if checkpoint:
        checkpoint("execution_completed", tuple(evidence[-10:]))
    return result
