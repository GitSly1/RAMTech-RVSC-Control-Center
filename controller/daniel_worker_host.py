from __future__ import annotations

import json
import os
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .engineering_environment import ControlledEngineeringEnvironment, EngineeringEnvironmentError
from .engineering_runner import EngineeringMissionRunner, ValidationCommand

OPENAI_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = os.environ.get("RVSC_OPENAI_MODEL", "gpt-5.6")
RVSC_ROOT = Path(__file__).resolve().parents[1]
DANIEL_CORE_PATH = Path(os.environ.get("RVSC_DANIEL_CORE_PATH", str(RVSC_ROOT / "golden-core" / "DANIEL_GOLDEN_CORE_V1.md")))
MAX_CORE_PATH = Path(os.environ.get("RVSC_MAX_CORE_PATH", str(RVSC_ROOT / "golden-core" / "MAX_PLATINUM_ENGINEERING_CORE_V1.md")))
SEMANTIQ_REPO = Path(os.environ.get("RVSC_SEMANTIQ_REPO", r"D:\py_proj\RAMTech-SEMANTIQ"))
SEM_DANIEL_WP = "SEM-DANIEL-002"
SEM_DANIEL_BASE_BRANCH = "rvsc/SEM-003-rtudes-baseline-import"
SEM_DANIEL_BRANCH = "rvsc/SEM-DANIEL-002-runtime-proof"
SEM_DANIEL_ALLOWED = (
    "src/semantiq/identity.py",
    "src/semantiq/__init__.py",
    "tests/test_identity.py",
)

CheckpointReporter = Callable[[str, tuple[str, ...]], None]


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
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Daniel returned invalid engineering JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Daniel engineering response must be a JSON object")
    return value


def _load_core(path: Path, label: str) -> str:
    try:
        core = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"unable to load {label} from {path}: {exc}") from exc
    if not core:
        raise RuntimeError(f"{label} is empty: {path}")
    return core


def _load_daniel_core() -> str:
    return _load_core(DANIEL_CORE_PATH, "Daniel Golden Core")


def _load_max_platinum_core() -> str:
    return _load_core(MAX_CORE_PATH, "Max Platinum Engineering Core")


def _prepare_semantiq_branch(environment: ControlledEngineeringEnvironment) -> None:
    status = environment.git_status()
    if status.returncode != 0:
        raise EngineeringEnvironmentError(status.stderr.strip() or "git status failed")
    if status.stdout.strip():
        raise EngineeringEnvironmentError("SEMANTIQ repository must be clean before Daniel mission")
    fetch = environment.run(("git", "fetch", "origin", SEM_DANIEL_BASE_BRANCH))
    if fetch.returncode != 0:
        raise EngineeringEnvironmentError(fetch.stderr.strip() or "unable to fetch SEMANTIQ qualification baseline")
    checkout = environment.run(("git", "checkout", "-B", SEM_DANIEL_BRANCH, f"origin/{SEM_DANIEL_BASE_BRANCH}"))
    if checkout.returncode != 0:
        raise EngineeringEnvironmentError(checkout.stderr.strip() or checkout.stdout.strip() or "unable to create Daniel proof branch from baseline")


def _engineering_prompt(mission: dict[str, Any], source_files: dict[str, str]) -> str:
    golden_core = _load_daniel_core()
    max_core = _load_max_platinum_core()
    return (
        "You are DEV-001 Daniel, Lead Software Engineer inside RVSC. The Daniel Golden Agent Core defines your identity and operating policy. "
        "The Max Platinum Engineering Core defines the engineering reasoning and execution methodology you must apply. Use both cores as executable behavior; do not summarize, quote, or repeat them in your response. "
        "Mission scope, explicit runtime restrictions, repository authorization, and safety constraints override any broader capability language in either core. Never expose credentials, secrets, environment values, or authorization material in generated files, summaries, evidence, or logs.\n\n"
        f"DANIEL GOLDEN AGENT CORE:\n{golden_core}\n\nMAX PLATINUM ENGINEERING CORE:\n{max_core}\n\n"
        "Perform the bounded engineering mission below. You are not being asked for an acknowledgement. Independently inspect the supplied baseline evidence, identify the actual requirement and regression surface, reason about the smallest general solution, preserve unrelated working behavior, and produce the implementation. "
        "Do not claim tests, commits, filesystem actions, QA, or other execution that you cannot perform; the controlled RVSC runtime applies your proposal and captures those results. Return ONLY valid JSON with exactly these top-level keys: files, commit_message, engineering_summary. "
        "files must be an object whose keys are exactly the authorized file paths and whose values are the COMPLETE replacement UTF-8 file contents. Do not use markdown fences. The engineering_summary must explain the implementation reasoning, risks considered, and any remaining uncertainty without fabricating evidence.\n\n"
        f"MISSION:\n{json.dumps(mission, indent=2)}\n\nBASELINE FILES:\n{json.dumps(source_files, indent=2)}"
    )


def _emit(checkpoint: CheckpointReporter | None, name: str, evidence: tuple[str, ...] = ()) -> None:
    if checkpoint is not None:
        checkpoint(name, evidence)


def _execute_sem_daniel(api_key: str, mission: dict[str, Any], run_id: str, started: str, checkpoint: CheckpointReporter | None = None) -> dict[str, Any]:
    if mission.get("base_branch") != SEM_DANIEL_BASE_BRANCH:
        raise ValueError(f"{SEM_DANIEL_WP} requires base branch {SEM_DANIEL_BASE_BRANCH}")
    if mission.get("work_branch") != SEM_DANIEL_BRANCH:
        raise ValueError(f"{SEM_DANIEL_WP} requires branch {SEM_DANIEL_BRANCH}")
    if tuple(mission.get("allowed_paths", ())) != SEM_DANIEL_ALLOWED:
        raise ValueError(f"{SEM_DANIEL_WP} allowed path contract mismatch")

    runner = EngineeringMissionRunner(request=_worker_request_from_mission(mission), repo_root=SEMANTIQ_REPO, validations=(
        ValidationCommand("compile", ("python", "-m", "compileall", "-q", "src", "tests")),
        ValidationCommand("unittest", ("python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v")),
    ))
    environment = runner.environment
    _prepare_semantiq_branch(environment)
    evidence = list(runner.preflight())
    _emit(checkpoint, "preflight_passed", tuple(evidence))
    source_files = {path: environment.read_text(path) for path in SEM_DANIEL_ALLOWED}

    response = _openai_call(api_key, _engineering_prompt(mission, source_files))
    provider_response_id = str(response.get("id", ""))
    model = str(response.get("model", DEFAULT_MODEL))
    status = str(response.get("status", "unknown"))
    if status != "completed":
        raise RuntimeError(f"provider status was {status}")
    _emit(checkpoint, "proposal_received", (f"provider_status:{status}", f"provider_response_id:{provider_response_id}"))

    proposal = _json_object(_response_text(response))
    files = proposal.get("files")
    if not isinstance(files, dict):
        raise RuntimeError("Daniel response missing files object")
    if set(files) != set(SEM_DANIEL_ALLOWED):
        raise RuntimeError(f"Daniel returned unauthorized or incomplete file set: {sorted(files)}")
    for path in SEM_DANIEL_ALLOWED:
        content = files[path]
        if not isinstance(content, str):
            raise RuntimeError(f"Daniel content for {path} is not text")
        environment.write_text(path, content)

    change_evidence = runner.evidence_after_change(SEM_DANIEL_ALLOWED)
    evidence.extend(change_evidence)
    _emit(checkpoint, "implementation_applied", change_evidence)
    validation_evidence = runner.validate()
    evidence.extend(validation_evidence)
    _emit(checkpoint, "tests_passed", validation_evidence)
    commit_message = proposal.get("commit_message")
    if not isinstance(commit_message, str) or not commit_message.strip():
        commit_message = f"{SEM_DANIEL_WP}: DEV-001 independent engineering runtime proof"
    commit_evidence = runner.commit(SEM_DANIEL_ALLOWED, commit_message.strip())
    evidence.extend(commit_evidence)
    _emit(checkpoint, "commit_created", commit_evidence)

    push = environment.run(("git", "push", "origin", f"HEAD:refs/heads/{SEM_DANIEL_BRANCH}"))
    if push.returncode != 0:
        raise EngineeringEnvironmentError(push.stderr.strip() or push.stdout.strip() or "git push failed")
    evidence.append("push:success")
    _emit(checkpoint, "push_confirmed", ("push:success",))

    final_status = environment.git_status()
    if final_status.returncode != 0:
        raise EngineeringEnvironmentError(final_status.stderr.strip() or "post-commit git status failed")
    evidence.append(f"repo_clean_after:{str(not bool(final_status.stdout.strip())).lower()}")
    ended = _utc_now()
    evidence.extend((f"run_id:{run_id}", f"started_at:{started}", f"ended_at:{ended}", "provider:openai", f"model:{model}", f"provider_response_id:{provider_response_id}", f"provider_status:{status}", f"golden_core:{DANIEL_CORE_PATH.name}", "golden_core_injected:true", f"max_platinum_core:{MAX_CORE_PATH.name}", "max_platinum_core_injected:true", "execution_mode:model_proposal_controlled_apply_validate_commit_push"))
    _emit(checkpoint, "execution_completed", tuple(evidence[-12:]))
    summary = str(proposal.get("engineering_summary") or "DEV-001 completed controlled SEMANTIQ engineering qualification")
    return {"success": True, "summary": summary, "evidence": evidence, "retryable": False}


def _worker_request_from_mission(mission: dict[str, Any]):
    from .adapters import WorkerRequest
    return WorkerRequest(agent_id=str(mission.get("agent_id", "")), wp_id=str(mission.get("wp_id", "")), project=str(mission.get("project", "")), repository=str(mission.get("repository", "")), base_branch=str(mission.get("base_branch", "")), work_branch=str(mission.get("work_branch", "")), objective=str(mission.get("objective", "")), allowed_paths=tuple(mission.get("allowed_paths", ())), acceptance_criteria=tuple(mission.get("acceptance_criteria", ())))


def execute_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("protocol") != "rvsc.worker.v1":
        raise ValueError("unsupported protocol")
    mission = payload.get("mission")
    if not isinstance(mission, dict):
        raise ValueError("mission must be an object")
    if mission.get("agent_id") != "DEV-001":
        raise ValueError("pilot host only accepts DEV-001")
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    run_id = f"RVSC-DAN-{uuid.uuid4().hex[:12].upper()}"
    started = _utc_now()
    if mission.get("wp_id") == SEM_DANIEL_WP:
        return _execute_sem_daniel(api_key, mission, run_id, started)
    raise ValueError(f"unsupported Daniel mission: {mission.get('wp_id')}")


class DanielWorkerHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:
        if self.path != "/execute":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = execute_payload(payload)
            self._send_json(200, result)
        except Exception as exc:
            self._send_json(500, {"success": False, "summary": str(exc), "evidence": ["worker_host:daniel"], "retryable": False})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[DanielWorkerHost] {fmt % args}")


def main() -> None:
    host = os.environ.get("RVSC_DANIEL_HOST", "127.0.0.1")
    port = int(os.environ.get("RVSC_DANIEL_PORT", "8767"))
    print(f"DEV-001 Daniel worker host listening on http://{host}:{port}/execute")
    ThreadingHTTPServer((host, port), DanielWorkerHandler).serve_forever()


if __name__ == "__main__":
    main()
