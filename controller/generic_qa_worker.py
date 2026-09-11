from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid

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
_READ_ONLY_GIT_COMMANDS = {"branch", "diff", "log", "rev-parse", "show", "status"}
_FULL_COMMIT_SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")
Checkpoint = Callable[[str, tuple[str, ...]], None]

_COGNITIVE_CLASSIFICATIONS = {
    "QA_ACCEPTED",
    "QA_REJECTED_IMPLEMENTATION",
    "QA_REJECTED_REQUIREMENT",
    "QA_BLOCKED_CONTRACT",
    "QA_BLOCKED_HARNESS",
    "QA_BLOCKED_ENVIRONMENT",
    "QA_BLOCKED_BOUNDARY",
    "QA_BLOCKED_EVIDENCE",
}


QUINN_COGNITION_CONTEXT_CHAR_BUDGET = 12000
QUINN_KNOWLEDGE_CONTEXT_CHAR_BUDGET = 6000

_QUINN_KNOWLEDGE_SOURCES = (
    ("A1", "governance/AUTHORITATIVE_KNOWLEDGE_HIERARCHY.md"),
    ("A1", "governance/SOURCE_ISOLATION.md"),
    ("A1", "governance/WORK_PACKAGE_LIFECYCLE.md"),
    ("A2", "docs/ORCHESTRATION_ARCHITECTURE.md"),
    ("A2", "config/agents.yaml"),
    ("A2", "config/orchestration.yaml"),
    ("A2", "config/repositories.yaml"),
    ("A5", "PROJECT_REGISTRY.md"),
    ("A5", "ROADMAP.md"),
    ("A5", "COMMAND_DASHBOARD.md"),
    ("A5", "SPRINT_DASHBOARD.md"),
)


def _knowledge_terms(mission: dict[str, Any]) -> frozenset[str]:
    values = [
        mission.get("project"),
        mission.get("repository"),
        mission.get("objective"),
        *(mission.get("acceptance_criteria") or []),
        *(mission.get("allowed_paths") or []),
        *(mission.get("changed_files") or []),
    ]
    terms: set[str] = set()
    for value in values:
        for token in re.findall(r"[A-Za-z0-9_.-]{3,}", str(value or "").lower()):
            terms.add(token)
    return frozenset(terms)


_QUINN_KNOWLEDGE_CLASS_TERMS = {
    "A2": frozenset({
        "architecture",
        "orchestration",
        "controller",
        "control-plane",
        "control_plane",
        "routing",
        "dispatch",
        "worker",
        "configuration",
    }),
    "A5": frozenset({
        "status",
        "roadmap",
        "readiness",
        "progress",
        "milestone",
        "planned",
        "planning",
        "schedule",
        "dashboard",
        "projection",
    }),
}


def _knowledge_intent_terms(
    mission: dict[str, Any],
) -> frozenset[str]:
    values = [
        mission.get("objective"),
        *(mission.get("acceptance_criteria") or []),
    ]
    terms: set[str] = set()
    for value in values:
        for token in re.findall(
            r"[A-Za-z0-9_.-]{3,}",
            str(value or "").lower(),
        ):
            terms.add(token)
    return frozenset(terms)


def _knowledge_class_relevant(
    authority_class: str,
    terms: frozenset[str],
) -> bool:
    if authority_class == "A1":
        return True

    required = _QUINN_KNOWLEDGE_CLASS_TERMS.get(authority_class)
    if not required:
        return False

    return bool(required.intersection(terms))


def _knowledge_revision(authority_root: Path, relative_path: str) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=authority_root,
        text=True,
        capture_output=True,
        check=False,
    )
    revision = completed.stdout.strip()
    if completed.returncode != 0 or not _FULL_COMMIT_SHA.fullmatch(revision):
        raise RuntimeError(
            f"unable to establish knowledge revision for {relative_path}"
        )
    return revision


def _authoritative_knowledge_context(
    mission: dict[str, Any],
    review_root: Path,
    *,
    authority_root: Path,
) -> dict[str, Any]:
    authority_root = authority_root.resolve()
    terms = _knowledge_terms(mission)
    intent_terms = _knowledge_intent_terms(mission)
    selected: list[dict[str, Any]] = []

    for authority_class, relative_path in _QUINN_KNOWLEDGE_SOURCES:
        path = (authority_root / relative_path).resolve()
        try:
            path.relative_to(authority_root)
        except ValueError as exc:
            raise RuntimeError(
                f"knowledge source escaped controlled repository: {relative_path}"
            ) from exc

        if not path.is_file():
            if authority_class == "A1":
                raise RuntimeError(
                    f"required authoritative knowledge source missing: {relative_path}"
                )
            continue

        raw = path.read_text(encoding="utf-8-sig")
        lowered = raw.lower()

        # Governance is always eligible. Architecture/configuration and
        # operational projections require mission relevance.
        score = 100 if authority_class == "A1" else sum(
            1 for term in terms if term in lowered or term in relative_path.lower()
        )
        if authority_class != "A1":
            if score == 0:
                continue
            if not _knowledge_class_relevant(
                authority_class,
                intent_terms,
            ):
                continue

        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        selected.append(
            {
                "authority_class": authority_class,
                "path": relative_path,
                "revision": _knowledge_revision(authority_root, relative_path),
                "authority_root": str(authority_root),
                "sha256": digest,
                "score": score,
                "raw": raw.strip(),
            }
        )

    selected.sort(
        key=lambda item: (
            int(item["authority_class"][1:]),
            -int(item["score"]),
            str(item["path"]),
        )
    )

    # Preserve mandatory governance while reserving meaningful capacity for
    # mission-relevant architecture/configuration and operational evidence.
    class_budgets = {
        "A1": 3000,
        "A2": 2000,
        "A5": 1000,
    }
    class_used = {authority: 0 for authority in class_budgets}
    sources: list[dict[str, Any]] = []

    for item in selected:
        authority_class = str(item["authority_class"])
        class_budget = class_budgets.get(authority_class, 0)
        available = class_budget - class_used.get(authority_class, 0)
        if available <= 0:
            continue

        raw = str(item.pop("raw"))
        per_source_limit = min(1500, available)
        excerpt = raw[:per_source_limit]
        truncated = len(raw) > len(excerpt)

        sources.append(
            {
                **item,
                "excerpt": excerpt,
                "truncated": truncated,
                "source_chars": len(raw),
                "excerpt_chars": len(excerpt),
            }
        )
        class_used[authority_class] = (
            class_used.get(authority_class, 0) + len(excerpt)
        )

    return {
        "budget_chars": QUINN_KNOWLEDGE_CONTEXT_CHAR_BUDGET,
        "used_chars": sum(item["excerpt_chars"] for item in sources),
        "sources": sources,
    }



def _bounded_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[TRUNCATED]"


def _load_cognition_asset(authority_root: Path, relative_path: str) -> str:
    path = authority_root / relative_path
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(
            f"unable to load Quinn cognition asset {relative_path}: {exc}"
        ) from exc
    if not text:
        raise RuntimeError(f"Quinn cognition asset is empty: {relative_path}")
    return text


def _quinn_cognitive_prompt(
    *,
    mission: dict[str, Any],
    review_root: Path,
    branch: str,
    commit_sha: str,
    authority_root: Path,
) -> str:
    authority_root = authority_root.resolve()
    quinn_core = _load_cognition_asset(
        authority_root,
        "golden-core/QA_001_QUINN_COGNITION_CONTRACT_V1.md",
    )
    max_core = _load_cognition_asset(
        authority_root,
        "golden-core/MAX_PLATINUM_ENGINEERING_CORE_V1.md",
    )
    authoritative_knowledge = _authoritative_knowledge_context(
        mission,
        review_root,
        authority_root=authority_root,
    )

    contract = {
        "objective": mission.get("objective"),
        "acceptance_criteria": mission.get("acceptance_criteria") or [],
        "allowed_paths": mission.get("allowed_paths") or [],
        "project": mission.get("project"),
        "repository": mission.get("repository"),
        "reviewed_branch": branch,
        "reviewed_commit_sha": commit_sha,
        "engineering_run_id": mission.get("engineering_run_id"),
    }

    # Evidence context is intentionally bounded. Quinn receives the
    # authoritative contract and reviewed identity, not a repository dump.
    evidence_context = {
        "changed_files": mission.get("changed_files") or [],
        "engineering_evidence": mission.get("engineering_evidence") or [],
        "acceptance_results": mission.get("acceptance_results") or {},
        "validation_results": mission.get("validation_results") or {},
    }

    dynamic = json.dumps(
        {
            "contract": contract,
            "evidence_context": evidence_context,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    dynamic = _bounded_text(
        dynamic,
        QUINN_COGNITION_CONTEXT_CHAR_BUDGET,
    )

    return (
        "You are Quinn (QA-001), RAMTech independent Quality Assurance.\n\n"
        "QUINN COGNITION CONTRACT:\n"
        + quinn_core
        + "\n\nAPPLICABLE MAX OPERATIONAL DISCIPLINE:\n"
        + max_core
        + "\n\nBOUNDED REVIEW CONTEXT:\n"
        + dynamic
        + "\n\nCURRENT DECISION AUTHORITY CONTEXT:\n"
        + (
            "A3 = engineering/validation evidence already supplied in the "
            "BOUNDED REVIEW CONTEXT above; evaluate its sufficiency and provenance.\n"
            "A4 = active mission contract already supplied in the bounded contract "
            "context above; it defines the immediate objective and delegated scope.\n"
            "Do not duplicate A3/A4 payloads here."
        )
        + "\n\nAUTHORITATIVE INSTITUTIONAL KNOWLEDGE:\n"
        + json.dumps(
            authoritative_knowledge,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n\n"
        "Treat A3 evidence and the A4 mission as the immediate decision context. "
        "Institutional knowledge constrains interpretation of the active mission; "
        "it must not replace, broaden, or invent a different QA objective. "
        "Evaluate the evidence actually supplied for the active objective before "
        "drawing conclusions from broader institutional context. "
        "Treat institutional knowledge according to its authority_class and "
        "source provenance. Surface material conflicts; do not silently let "
        "operational projections override stronger governance or accepted "
        "evidence. "
        "Independently judge the objective, acceptance sufficiency, "
        "implementation evidence, authority boundaries, evidence integrity, "
        "and material uncertainty. Tests passing is not sufficient by itself. "
        "Do not modify implementation or rewrite the contract. Fail closed "
        "when evidence is insufficient.\n\n"
        "Return ONLY one JSON object with exactly these semantic fields:\n"
        "{"
        "\"causal_state\": one of "
        "\"SATISFIED\", "
        "\"IMPLEMENTATION_DEFECT\", "
        "\"REQUIREMENT_DEFECT\", "
        "\"CONTRACT_BLOCKER\", "
        "\"HARNESS_BLOCKER\", "
        "\"ENVIRONMENT_BLOCKER\", "
        "\"BOUNDARY_BLOCKER\", "
        "\"EVIDENCE_BLOCKER\"; "
        "\"summary\": non-empty string; "
        "\"findings\": array of non-empty strings"
        "}. "
        "Determine the evidenced root cause. Do not encode an RVSC QA_* "
        "disposition; RVSC policy maps causal_state to disposition."
    )



_QUINN_CAUSAL_STATES = frozenset(
    {
        "SATISFIED",
        "IMPLEMENTATION_DEFECT",
        "REQUIREMENT_DEFECT",
        "CONTRACT_BLOCKER",
        "HARNESS_BLOCKER",
        "ENVIRONMENT_BLOCKER",
        "BOUNDARY_BLOCKER",
        "EVIDENCE_BLOCKER",
    }
)

_CAUSAL_STATE_TO_CLASSIFICATION = {
    "SATISFIED": "QA_ACCEPTED",
    "IMPLEMENTATION_DEFECT": "QA_REJECTED_IMPLEMENTATION",
    "REQUIREMENT_DEFECT": "QA_REJECTED_REQUIREMENT",
    "CONTRACT_BLOCKER": "QA_BLOCKED_CONTRACT",
    "HARNESS_BLOCKER": "QA_BLOCKED_HARNESS",
    "ENVIRONMENT_BLOCKER": "QA_BLOCKED_ENVIRONMENT",
    "BOUNDARY_BLOCKER": "QA_BLOCKED_BOUNDARY",
    "EVIDENCE_BLOCKER": "QA_BLOCKED_EVIDENCE",
}


def _quinn_assurance_schema() -> dict[str, Any]:
    causal_states = sorted(_QUINN_CAUSAL_STATES)
    return {
        "type": "object",
        "properties": {
            "causal_state": {
                "type": "string",
                "enum": causal_states,
            },
            "summary": {
                "type": "string",
                "minLength": 1,
            },
            "findings": {
                "type": "array",
                "items": {
                    "type": "string",
                    "minLength": 1,
                },
                "minItems": 1,
            },
        },
        "required": [
            "causal_state",
            "summary",
            "findings",
        ],
        "additionalProperties": False,
    }


def _quinn_response_text(response: dict[str, Any]) -> str:
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if (
                content.get("type") == "output_text"
                and isinstance(content.get("text"), str)
                and content["text"].strip()
            ):
                return content["text"]
    raise RuntimeError(
        "Quinn provider response did not contain output_text"
    )


def _quinn_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()

    if candidate.startswith("```"):
        lines = candidate.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        candidate = "\n".join(lines).strip()

    value = json.loads(candidate)

    if not isinstance(value, dict):
        raise RuntimeError(
            "Quinn cognitive response must be a JSON object"
        )

    return value


def _quinn_ollama_call(prompt: str) -> dict[str, Any]:
    # QA owns its response contract. Never reuse Engineering's
    # proposal schema here.
    from controller.generic_engineering_worker import (
        DEFAULT_OLLAMA_MODEL,
        OLLAMA_URL,
    )

    body = json.dumps(
        {
            "model": DEFAULT_OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": _quinn_assurance_schema(),
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
            payload = json.loads(
                response.read().decode("utf-8")
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(
            "utf-8",
            errors="replace",
        )
        raise RuntimeError(
            f"Ollama HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Ollama transport error: {exc.reason}"
        ) from exc

    text = payload.get("response")

    if not isinstance(text, str) or not text.strip():
        raise RuntimeError(
            "Ollama response did not contain response text"
        )

    return {
        "id": f"ollama-{uuid.uuid4().hex}",
        "status": "completed",
        "model": str(
            payload.get("model", DEFAULT_OLLAMA_MODEL)
        ),
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


def _quinn_openai_call(
    api_key: str,
    prompt: str,
) -> dict[str, Any]:
    # Current OpenAI transport is neutral. Keep QA ownership of
    # dispatch while reusing only that transport implementation.
    from controller.generic_engineering_worker import _openai_call

    return _openai_call(api_key, prompt)


def _quinn_provider_call(
    prompt: str,
) -> tuple[dict[str, Any], str]:
    provider = os.environ.get(
        "RVSC_AI_PROVIDER",
        "ollama",
    ).strip().lower()

    if provider == "ollama":
        return _quinn_ollama_call(prompt), "ollama"

    if provider == "openai":
        api_key = os.environ.get(
            "OPENAI_API_KEY",
            "",
        ).strip()

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        return _quinn_openai_call(
            api_key,
            prompt,
        ), "openai"

    raise RuntimeError(
        f"unsupported RVSC_AI_PROVIDER: {provider}"
    )


def _cognitive_assurance(
    *,
    mission: dict[str, Any],
    review_root: Path,
    branch: str,
    commit_sha: str,
    authority_root: Path,
) -> dict[str, Any]:
    prompt = _quinn_cognitive_prompt(
        mission=mission,
        review_root=review_root,
        branch=branch,
        commit_sha=commit_sha,
        authority_root=authority_root,
    )

    response, provider = _quinn_provider_call(prompt)
    text = _quinn_response_text(response)
    result = _quinn_json_object(text)

    result["provider"] = provider
    result["provider_response_id"] = str(response.get("id", ""))
    result["prompt_chars"] = len(prompt)
    return result


def _acceptance_authority_gate(
    mission: dict[str, Any],
) -> dict[str, Any]:
    """Establish whether deterministic evidence permits QA acceptance.

    Quinn's probabilistic SATISFIED judgment is never authoritative by itself.
    Final acceptance requires controller-generated semantic acceptance evidence
    proving coverage of every active acceptance criterion.
    """
    reasons: list[str] = []

    if mission.get("requires_semantic_acceptance") is not True:
        reasons.append(
            "mission does not require controller-owned semantic acceptance"
        )

    criteria = mission.get("acceptance_criteria")
    if (
        not isinstance(criteria, list)
        or not criteria
        or not all(
            isinstance(item, str) and item.strip()
            for item in criteria
        )
    ):
        reasons.append(
            "acceptance_criteria are missing or invalid"
        )
        criteria = []

    checks = mission.get("acceptance_checks")
    if not isinstance(checks, list) or not checks:
        reasons.append(
            "acceptance_checks are missing"
        )

    raw_evidence = mission.get("engineering_evidence")
    if not isinstance(raw_evidence, (list, tuple)):
        reasons.append(
            "controller engineering evidence bundle is missing"
        )
        evidence: tuple[str, ...] = ()
    else:
        evidence = tuple(
            str(item).strip()
            for item in raw_evidence
            if str(item).strip()
        )
        if not evidence:
            reasons.append(
                "controller engineering evidence bundle is empty"
            )

    if criteria and evidence:
        expected_count = len(criteria)

        if (
            f"semantic_acceptance:criteria_verified:{expected_count}"
            not in evidence
        ):
            reasons.append(
                "semantic acceptance criterion count is unverified"
            )

        if "semantic_acceptance:passed" not in evidence:
            reasons.append(
                "semantic acceptance completion evidence is missing"
            )

        for criterion_index in range(1, expected_count + 1):
            prefix = (
                "semantic_acceptance:"
                f"criterion:{criterion_index}:"
            )
            if not any(
                item.startswith(prefix)
                for item in evidence
            ):
                reasons.append(
                    "semantic acceptance evidence missing for criterion "
                    f"{criterion_index}"
                )

    return {
        "eligible": not reasons,
        "classification": (
            "QA_ACCEPTED"
            if not reasons
            else "QA_BLOCKED_EVIDENCE"
        ),
        "reasons": reasons,
    }


def _classification_consistency_guard(
    mission: dict[str, Any],
    cognitive: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed when explicit structured blocker state conflicts with cognition.

    This guard does not infer from Quinn's prose and does not manufacture a
    replacement classification. It acts only when structured mission evidence
    establishes an unambiguous causal blocker.
    """
    classification = cognitive["classification"]
    causal_state = cognitive.get("causal_state")
    validation_results = mission.get("validation_results")

    if not isinstance(validation_results, dict):
        return cognitive

    environment_blocked = (
        validation_results.get("environment_ready") is False
        and validation_results.get("harness_integrity") is True
    )

    if environment_blocked and (
        causal_state != "ENVIRONMENT_BLOCKER"
        or classification != "QA_BLOCKED_ENVIRONMENT"
    ):
        raise ValueError(
            "cognitive causal state conflicts with explicit environment blocker"
        )

    return cognitive


def _validated_cognitive_assurance(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ValueError("cognitive assurance result must be an object")

    causal_state = str(result.get("causal_state", "")).strip()
    if causal_state not in _QUINN_CAUSAL_STATES:
        raise ValueError(
            "cognitive assurance returned unsupported causal state"
        )

    classification = _CAUSAL_STATE_TO_CLASSIFICATION[causal_state]

    summary = str(result.get("summary", "")).strip()
    if not summary:
        raise ValueError("cognitive assurance summary is required")

    findings = result.get("findings")
    if not isinstance(findings, list) or not all(
        isinstance(item, str) and item.strip() for item in findings
    ):
        raise ValueError(
            "cognitive assurance findings must be a list of non-empty strings"
        )

    validated = {
        "causal_state": causal_state,
        "classification": classification,
        "summary": summary,
        "findings": [item.strip() for item in findings],
    }

    for key in ("provider", "provider_response_id", "prompt_chars"):
        if key in result:
            validated[key] = result[key]

    return validated
_PROJECT_REPOSITORIES = {
    "rvsc": ("RVSC_RVSC_REPO", RVSC_ROOT, {"gitsly1/ramtech-rvsc-control-center", "ramtech-rvsc-control-center"}),
    "semantiq": ("RVSC_SEMANTIQ_REPO", Path(r"D:\Py_Proj\RAMTech-SEMANTIQ"), {"gitsly1/ramtech-semantiq", "ramtech-semantiq"}),
    "moxie": ("RVSC_MOXIE_REPO", Path(r"D:\Py_Proj\RAMTech-MOXIE"), {"gitsly1/ramtech-moxie", "ramtech-moxie"}),
}


def _record(checkpoint: Checkpoint | None, name: str, evidence: tuple[str, ...]) -> None:
    if checkpoint is not None:
        checkpoint(name, evidence)


def _reject(*, run_id: str, agent_id: str, branch: str | None, commit_sha: str | None, summary: str, evidence: list[str], validations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"success": False, "run_id": run_id or None, "agent_id": agent_id, "verdict": "QA_REJECTED", "reviewed_branch": branch, "reviewed_commit_sha": commit_sha, "summary": summary, "evidence": evidence, "validations": validations or [], "retryable": False}


def _mission_text(mission: dict[str, Any], key: str) -> str:
    value = mission.get(key)
    return value.strip() if isinstance(value, str) else ""


def _repository_key(value: str) -> str:
    normalized = value.strip().replace("\\", "/").rstrip("/")
    if normalized.lower().endswith(".git"):
        normalized = normalized[:-4]
    return normalized.lower()


def _is_absolute_local_repository_context(value: str) -> bool:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        return False
    if normalized.startswith("//"):
        return True
    return len(normalized) >= 3 and normalized[0].isalpha() and normalized[1] == ":" and normalized[2] == "/"


def _repo_root(mission: dict[str, Any]) -> Path:
    project = _mission_text(mission, "engineering_project") or _mission_text(mission, "project")
    project = project.lower()
    mapping = _PROJECT_REPOSITORIES.get(project)
    if mapping is None:
        raise ValueError(f"no controlled repository mapping for QA project {project or '<missing>'}")
    env_name, default, accepted_repositories = mapping

    repository = _mission_text(mission, "engineering_repository") or _mission_text(mission, "repository")
    if (
        repository
        and not _is_absolute_local_repository_context(repository)
        and _repository_key(repository) not in accepted_repositories
    ):
        raise ValueError(f"repository {repository} does not match QA project {project}")

    return Path(os.environ.get(env_name, str(default))).resolve()


def _requested_commit(mission: dict[str, Any]) -> str:
    reviewed = _mission_text(mission, "reviewed_commit_sha")
    engineering = _mission_text(mission, "engineering_commit_sha")
    legacy = _mission_text(mission, "commit_sha")
    if reviewed:
        for field, value in (("engineering_commit_sha", engineering), ("commit_sha", legacy)):
            if value and value.lower() != reviewed.lower():
                raise ValueError(f"{field} does not match reviewed_commit_sha")
        return reviewed
    if engineering:
        raise ValueError("reviewed_commit_sha is required when engineering_commit_sha is supplied")
    return legacy


def _git_value(repo_root: Path, *args: str, timeout: int = 30) -> str:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    completed = subprocess.run(["git", *args], cwd=repo_root, text=True, capture_output=True, check=False, timeout=timeout, env=environment)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "unknown Git error")
    return completed.stdout.strip()


def _normalized_origin_url(workspace_root: Path, origin_url: str) -> str:
    if "://" in origin_url or re.match(r"^[^/\\]+@[^:]+:", origin_url):
        return origin_url
    path = Path(origin_url).expanduser()
    if not path.is_absolute():
        path = workspace_root / path
    return str(path.resolve())


@contextmanager
def _review_repository(workspace_root: Path, expected_branch: str, expected_commit: str) -> Iterator[tuple[Path, str, str, tuple[str, ...]]]:
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
        origin_url = _normalized_origin_url(workspace_root, _git_value(workspace_root, "remote", "get-url", "origin"))
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise ValueError("origin is unavailable for requested target acquisition") from exc

    with tempfile.TemporaryDirectory(prefix="rvsc-qa-target-") as temporary:
        acquired_root = Path(temporary) / "repository"
        acquired_root.mkdir()
        remote_ref = f"refs/remotes/origin/{expected_branch}"
        try:
            _git_value(acquired_root, "init")
            _git_value(acquired_root, "remote", "add", "origin", origin_url)
            _git_value(acquired_root, "fetch", "--no-tags", "origin", f"+refs/heads/{expected_branch}:{remote_ref}", timeout=120)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            raise ValueError("requested branch is unavailable from origin") from exc
        normalized_commit = expected_commit.lower()
        try:
            fetched_tip = _git_value(acquired_root, "rev-parse", "--verify", f"{remote_ref}^{{commit}}").lower()
            verified_commit = _git_value(acquired_root, "rev-parse", "--verify", f"{normalized_commit}^{{commit}}").lower()
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            raise ValueError("requested commit SHA is invalid or unavailable") from exc
        if verified_commit != normalized_commit:
            raise ValueError("requested commit SHA could not be verified exactly")
        if fetched_tip != normalized_commit:
            raise ValueError(f"requested branch/commit mismatch: origin branch tip is {fetched_tip}, not {normalized_commit}")
        try:
            _git_value(acquired_root, "checkout", "--detach", normalized_commit)
            checked_out = _git_value(acquired_root, "rev-parse", "HEAD").lower()
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            raise ValueError("requested commit could not be prepared for isolated QA review") from exc
        if checked_out != normalized_commit:
            raise ValueError("acquired review target does not match the requested commit")
        yield acquired_root, expected_branch, normalized_commit, ("target_acquisition:origin_fetch", f"target_ref:{remote_ref}", "target_verification:branch_tip_matches_commit", "target_checkout:detached")


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
    return f"inspected:{path.relative_to(repo_root.resolve()).as_posix()}:sha256:{digest}"


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
        if executable == "git" and (len(argv) < 2 or argv[1].lower() not in _READ_ONLY_GIT_COMMANDS):
            raise ValueError(f"validation command {name} uses a non-read-only Git operation")
        commands.append((name, list(argv)))
    return commands


def _copy_repository(repo_root: Path, destination: Path) -> None:
    ignored_names = {".git", ".rvsc", "__pycache__", ".pytest_cache", ".mypy_cache"}

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in ignored_names or name.endswith(".pyc")}

    shutil.copytree(repo_root, destination, ignore=ignore)


def execute_mission(*, agent_id: str, agent_name: str, role: str, qa_eligible: bool, mission: dict[str, Any], checkpoint: Checkpoint | None = None, repo_root: Path | None = None) -> dict[str, Any]:
    run_id = str(mission.get("run_id", "")).strip()
    evidence = [f"qa_agent:{agent_id}", f"qa_name:{agent_name}", f"qa_role:{role}"]
    if not qa_eligible:
        evidence.append("authorization:denied")
        return _reject(run_id=run_id, agent_id=agent_id, branch=None, commit_sha=None, summary=f"{agent_id} is not authorized for independent QA execution", evidence=evidence)
    evidence.append("authorization:qa_eligible")
    if not run_id:
        evidence.append("required_evidence:run_id:missing")
        return _reject(run_id="", agent_id=agent_id, branch=None, commit_sha=None, summary="required QA run_id is missing", evidence=evidence)

    branch: str | None = None
    commit_sha: str | None = None
    validations: list[dict[str, Any]] = []
    try:
        root = (repo_root if repo_root is not None else _repo_root(mission)).resolve()
        evidence.append(f"target_workspace:{root}")
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
            _record(checkpoint, "qa_inspection_complete", (f"run_id:{run_id}", f"branch:{branch}", f"commit:{commit_sha}"))

            cognitive = _validated_cognitive_assurance(
                _cognitive_assurance(
                    mission=mission,
                    review_root=review_root,
                    branch=branch,
                    commit_sha=commit_sha,
                    authority_root=RVSC_ROOT,
                )
            )
            cognitive = _classification_consistency_guard(
                mission,
                cognitive,
            )
            cognitive_classification = cognitive["classification"]
            evidence.extend(
                (
                    f"cognitive_assurance:{cognitive_classification}",
                    f"cognitive_summary:{cognitive['summary']}",
                )
            )
            evidence.extend(
                f"cognitive_finding:{finding}"
                for finding in cognitive["findings"]
            )
            _record(
                checkpoint,
                "qa_cognitive_assurance_observed",
                (
                    f"run_id:{run_id}",
                    f"classification:{cognitive_classification}",
                ),
            )

            if cognitive_classification != "QA_ACCEPTED":
                evidence.append("verdict:QA_REJECTED")
                _record(
                    checkpoint,
                    "qa_rejected",
                    (
                        f"run_id:{run_id}",
                        f"cognitive_classification:{cognitive_classification}",
                    ),
                )
                result = _reject(
                    run_id=run_id,
                    agent_id=agent_id,
                    branch=branch,
                    commit_sha=commit_sha,
                    summary=cognitive["summary"],
                    evidence=evidence,
                    validations=validations,
                )
                result["cognitive_classification"] = cognitive_classification
                result["cognitive_assurance"] = cognitive
                return result

            acceptance_authority = _acceptance_authority_gate(mission)

            evidence.append(
                "acceptance_authority:"
                + (
                    "eligible"
                    if acceptance_authority["eligible"]
                    else "blocked"
                )
            )
            evidence.extend(
                "acceptance_authority_reason:" + reason
                for reason in acceptance_authority["reasons"]
            )

            _record(
                checkpoint,
                "qa_acceptance_authority_observed",
                (
                    f"run_id:{run_id}",
                    "eligible:"
                    + str(
                        acceptance_authority["eligible"]
                    ).lower(),
                ),
            )

            if not acceptance_authority["eligible"]:
                evidence.append("verdict:QA_REJECTED")

                _record(
                    checkpoint,
                    "qa_rejected",
                    (
                        f"run_id:{run_id}",
                        "acceptance_authority:blocked",
                    ),
                )

                result = _reject(
                    run_id=run_id,
                    agent_id=agent_id,
                    branch=branch,
                    commit_sha=commit_sha,
                    summary=(
                        "acceptance authority blocked: "
                        + "; ".join(
                            acceptance_authority["reasons"]
                        )
                    ),
                    evidence=evidence,
                    validations=validations,
                )
                result["cognitive_classification"] = (
                    cognitive_classification
                )
                result["cognitive_assurance"] = cognitive
                result["acceptance_classification"] = (
                    "QA_BLOCKED_EVIDENCE"
                )
                result["acceptance_authority"] = (
                    acceptance_authority
                )
                return result

            timeout = int(mission.get("validation_timeout_seconds", 900))
            if timeout < 1 or timeout > 3600:
                raise ValueError("validation timeout must be between 1 and 3600 seconds")
            with tempfile.TemporaryDirectory(prefix="rvsc-qa-") as temporary:
                validation_root = Path(temporary) / "repository"
                _copy_repository(review_root, validation_root)
                environment = os.environ.copy()
                environment["PYTHONDONTWRITEBYTECODE"] = "1"
                for name, argv in commands:
                    completed = subprocess.run(argv, cwd=validation_root, text=True, capture_output=True, check=False, timeout=timeout, env=environment)
                    result = {"name": name, "argv": argv, "returncode": completed.returncode, "stdout": completed.stdout[-12000:], "stderr": completed.stderr[-12000:]}
                    validations.append(result)
                    evidence.append(f"validation:{name}:exit:{completed.returncode}")
                    _record(checkpoint, "qa_validation_observed", (f"run_id:{run_id}", f"validation:{name}", f"exit:{completed.returncode}"))
                    if completed.returncode != 0:
                        return _reject(run_id=run_id, agent_id=agent_id, branch=branch, commit_sha=commit_sha, summary=f"validation failed: {name}", evidence=evidence, validations=validations)
            evidence.extend(("source_execution:isolated_copy", "verdict:QA_ACCEPTED"))
            _record(checkpoint, "qa_accepted", (f"run_id:{run_id}", f"branch:{branch}", f"commit:{commit_sha}"))
            return {"success": True, "run_id": run_id, "agent_id": agent_id, "verdict": "QA_ACCEPTED", "cognitive_classification": cognitive_classification, "cognitive_assurance": cognitive, "acceptance_classification": "QA_ACCEPTED", "acceptance_authority": acceptance_authority, "reviewed_branch": branch, "reviewed_commit_sha": commit_sha, "summary": f"independent QA accepted {branch} at {commit_sha}", "evidence": evidence, "validations": validations, "retryable": False}
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        evidence.extend((f"qa_failure:{exc}", "verdict:QA_REJECTED"))
        _record(checkpoint, "qa_rejected", (f"run_id:{run_id}", f"failure:{exc}"))
        return _reject(run_id=run_id, agent_id=agent_id, branch=branch, commit_sha=commit_sha, summary=str(exc), evidence=evidence, validations=validations)
