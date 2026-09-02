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
DEFAULT_MODEL = os.environ.get("RVSC_OPENAI_MODEL", "gpt-5.6")
RVSC_ROOT = Path(__file__).resolve().parents[1]
MAX_CORE_PATH = Path(os.environ.get("RVSC_MAX_CORE_PATH", str(RVSC_ROOT / "golden-core" / "MAX_PLATINUM_ENGINEERING_CORE_V1.md")))

CheckpointReporter = Callable[[str, tuple[str, ...]], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _openai_call(api_key: str, prompt: str) -> dict[str, Any]:
    body = json.dumps({"model": DEFAULT_MODEL, "input": prompt}).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_URL,
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI transport error: {exc.reason}") from exc


def _response_text(response: dict[str, Any]) -> str:
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise RuntimeError("provider response did not contain output_text")


def _json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
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
    return WorkerRequest(
        agent_id=str(mission.get("agent_id", "")),
        wp_id=str(mission.get("wp_id", "")),
        project=str(mission.get("project", "")),
        repository=str(mission.get("repository", "")),
        base_branch=str(mission.get("base_branch", "")),
        work_branch=str(mission.get("work_branch", "")),
        objective=str(mission.get("objective", "")),
        allowed_paths=tuple(str(item) for item in mission.get("allowed_paths", ())),
        acceptance_criteria=tuple(str(item) for item in mission.get("acceptance_criteria", ())),
    )


def _repo_root(mission: dict[str, Any]) -> Path:
    project = str(mission.get("project", "")).strip().lower()
    env_name = {
        "rvsc": "RVSC_RVSC_REPO",
        "semantiq": "RVSC_SEMANTIQ_REPO",
        "moxie": "RVSC_MOXIE_REPO",
    }.get(project)
    if env_name is None:
        raise ValueError(f"no controlled repository mapping for project {project or '<missing>'}")
    defaults = {
        "rvsc": RVSC_ROOT,
        "semantiq": Path(r"D:\Py_Proj\RAMTech-SEMANTIQ"),
        "moxie": Path(r"D:\Py_Proj\RAMTech-MOXIE"),
    }
    return Path(os.environ.get(env_name, str(defaults[project]))).resolve()


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


def _engineering_prompt(agent_id: str, agent_name: str, role: str, mission: dict[str, Any], source_files: dict[str, str]) -> str:
    max_core = _load_text(MAX_CORE_PATH, "Max Platinum Engineering Core")
    return (
        f"You are {agent_id} {agent_name}, serving as {role} inside RVSC. "
        "Operate only within the supplied mission contract. The Max Platinum Engineering Core defines the engineering methodology you must apply; do not quote or summarize it. "
        "Mission scope, repository authorization, allowed paths, and safety restrictions override all broader capability language. Never expose credentials or secrets.\n\n"
        f"MAX PLATINUM ENGINEERING CORE:\n{max_core}\n\n"
        "Perform the bounded engineering mission. Independently inspect the supplied baseline files, implement the smallest general solution that satisfies the acceptance criteria, and preserve unrelated behavior. "
        "Do not claim filesystem actions, tests, commits, pushes, or QA; the controlled runtime performs and records those actions. Return ONLY valid JSON with exactly these top-level keys: files, commit_message, engineering_summary. "
        "files must contain exactly the authorized file paths, each mapped to COMPLETE replacement UTF-8 content. No markdown fences.\n\n"
        f"MISSION:\n{json.dumps(mission, indent=2)}\n\nBASELINE FILES:\n{json.dumps(source_files, indent=2)}"
    )


def execute_mission(
    *,
    agent_id: str,
    agent_name: str,
    role: str,
    mission: dict[str, Any],
    checkpoint: CheckpointReporter | None = None,
) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    request = _worker_request(mission)
    if request.agent_id != agent_id:
        raise ValueError(f"mission agent mismatch: expected {agent_id}, got {request.agent_id}")
    if not request.wp_id or not request.base_branch or not request.work_branch or not request.allowed_paths:
        raise ValueError("mission requires wp_id, base_branch, work_branch, and allowed_paths")

    run_id = str(mission.get("run_id", "")).strip() or f"RVSC-{agent_id}-{uuid.uuid4().hex[:12].upper()}"
    started = _utc_now()
    runner = EngineeringMissionRunner(request, _repo_root(mission), validations=_validations(mission))
    environment = runner.environment
    _prepare_branch(environment, request)
    evidence = list(runner.preflight())
    if checkpoint:
        checkpoint("preflight_passed", tuple(evidence) + (f"run_id:{run_id}",))

    source_files = {path: environment.read_text(path) for path in request.allowed_paths}
    response = _openai_call(api_key, _engineering_prompt(agent_id, agent_name, role, mission, source_files))
    provider_response_id = str(response.get("id", ""))
    provider_status = str(response.get("status", "unknown"))
    model = str(response.get("model", DEFAULT_MODEL))
    if provider_status != "completed":
        raise RuntimeError(f"provider status was {provider_status}")
    if checkpoint:
        checkpoint("proposal_received", (f"run_id:{run_id}", f"provider_status:{provider_status}", f"provider_response_id:{provider_response_id}"))

    proposal = _json_object(_response_text(response))
    files = proposal.get("files")
    if not isinstance(files, dict) or set(files) != set(request.allowed_paths):
        returned = sorted(files) if isinstance(files, dict) else []
        raise RuntimeError(f"worker returned unauthorized or incomplete file set: {returned}")
    for path in request.allowed_paths:
        content = files[path]
        if not isinstance(content, str):
            raise RuntimeError(f"worker content for {path} is not text")
        environment.write_text(path, content)

    changed = runner.evidence_after_change(request.allowed_paths)
    evidence.extend(changed)
    if checkpoint:
        checkpoint("implementation_applied", changed + (f"run_id:{run_id}",))
    validations = runner.validate()
    evidence.extend(validations)
    if checkpoint:
        checkpoint("tests_passed", validations + (f"run_id:{run_id}",))

    commit_message = str(proposal.get("commit_message", "")).strip() or f"{request.wp_id}: {agent_id} controlled engineering"
    committed = runner.commit(request.allowed_paths, commit_message)
    evidence.extend(committed)
    if checkpoint:
        checkpoint("commit_created", committed + (f"run_id:{run_id}",))
    push = environment.run(("git", "push", "origin", f"HEAD:refs/heads/{request.work_branch}"))
    if push.returncode != 0:
        raise EngineeringEnvironmentError(push.stderr.strip() or push.stdout.strip() or "git push failed")
    evidence.append("push:success")
    if checkpoint:
        checkpoint("push_confirmed", ("push:success", f"run_id:{run_id}"))

    final_status = environment.git_status()
    if final_status.returncode != 0:
        raise EngineeringEnvironmentError(final_status.stderr.strip() or "post-commit git status failed")
    clean = not bool(final_status.stdout.strip())
    evidence.extend((
        f"repo_clean_after:{str(clean).lower()}",
        f"run_id:{run_id}",
        f"started_at:{started}",
        f"ended_at:{_utc_now()}",
        "provider:openai",
        f"model:{model}",
        f"provider_response_id:{provider_response_id}",
        f"provider_status:{provider_status}",
        f"agent:{agent_id}",
        "execution_mode:generic_model_proposal_controlled_apply_validate_commit_push",
    ))
    if checkpoint:
        checkpoint("execution_completed", tuple(evidence[-10:]))
    return {
        "success": True,
        "summary": str(proposal.get("engineering_summary") or f"{agent_id} completed controlled engineering mission"),
        "evidence": evidence,
        "retryable": False,
        "run_id": run_id,
    }
