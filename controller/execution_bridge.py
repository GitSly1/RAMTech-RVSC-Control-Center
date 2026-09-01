from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Callable, Mapping

from .adapters import WorkerAdapter, WorkerRequest, WorkerResult


class ExecutionBridgeError(RuntimeError):
    """Raised when a configured execution provider cannot be invoked safely."""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    endpoint: str
    token_env: str | None = None
    timeout_seconds: int = 120

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("provider requires a name")
        if not self.endpoint.startswith(("http://", "https://")):
            raise ValueError("provider endpoint must be http(s)")
        if self.timeout_seconds <= 0:
            raise ValueError("provider timeout must be positive")


@dataclass(frozen=True)
class ProviderResponse:
    status: int
    body: bytes


Transport = Callable[[urllib.request.Request, int], ProviderResponse]


def _default_transport(request: urllib.request.Request, timeout: int) -> ProviderResponse:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return ProviderResponse(status=int(response.status), body=response.read())
    except urllib.error.HTTPError as exc:
        return ProviderResponse(status=int(exc.code), body=exc.read())
    except urllib.error.URLError as exc:
        raise ExecutionBridgeError(f"provider transport failed: {exc.reason}") from exc


def _worker_error_result(provider: str, response: ProviderResponse) -> WorkerResult:
    summary = f"provider returned HTTP {response.status}"
    evidence: tuple[str, ...] = (f"provider:{provider}", f"http_status:{response.status}")
    retryable = response.status >= 500 or response.status == 429

    try:
        data = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return WorkerResult(success=False, summary=summary, evidence=evidence, retryable=retryable)

    if not isinstance(data, Mapping):
        return WorkerResult(success=False, summary=summary, evidence=evidence, retryable=retryable)

    worker_summary = data.get("summary")
    if isinstance(worker_summary, str) and worker_summary.strip():
        summary = worker_summary.strip()

    worker_evidence = data.get("evidence")
    if isinstance(worker_evidence, list) and all(isinstance(item, str) for item in worker_evidence):
        evidence = tuple(item for item in worker_evidence if item.strip()) + evidence

    if isinstance(data.get("retryable"), bool):
        retryable = bool(data["retryable"])

    return WorkerResult(success=False, summary=summary, evidence=evidence, retryable=retryable)


class HttpJsonWorkerAdapter:
    """Provider-neutral bridge from RVSC WorkerRequest to an external worker runtime.

    Provider contract:
      POST JSON containing the bounded RVSC worker request.
      Return JSON with: success(bool), summary(str), evidence(list[str]),
      and optional retryable(bool).

    The bridge deliberately does not grant repository credentials or broaden paths;
    those remain concerns of the approved external runtime and mission policy.
    """

    def __init__(self, config: ProviderConfig, transport: Transport | None = None) -> None:
        config.validate()
        self.config = config
        self.name = config.name
        self._transport = transport or _default_transport

    def execute(self, request: WorkerRequest) -> WorkerResult:
        token = None
        if self.config.token_env:
            token = os.environ.get(self.config.token_env)
            if not token:
                raise ExecutionBridgeError(
                    f"provider credential missing from environment: {self.config.token_env}"
                )

        payload = {
            "protocol": "rvsc.worker.v1",
            "provider": self.config.name,
            "mission": asdict(request),
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        http_request = urllib.request.Request(
            self.config.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        response = self._transport(http_request, self.config.timeout_seconds)
        if response.status < 200 or response.status >= 300:
            return _worker_error_result(self.config.name, response)

        try:
            data = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExecutionBridgeError("provider returned invalid JSON") from exc

        if not isinstance(data, Mapping):
            raise ExecutionBridgeError("provider response must be a JSON object")
        if not isinstance(data.get("success"), bool):
            raise ExecutionBridgeError("provider response requires boolean success")
        summary = data.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ExecutionBridgeError("provider response requires summary")
        evidence_raw = data.get("evidence", [])
        if not isinstance(evidence_raw, list) or not all(isinstance(item, str) for item in evidence_raw):
            raise ExecutionBridgeError("provider evidence must be a list of strings")

        evidence = tuple(item for item in evidence_raw if item.strip()) + (
            f"provider:{self.config.name}",
            "protocol:rvsc.worker.v1",
        )
        return WorkerResult(
            success=data["success"],
            summary=summary.strip(),
            evidence=evidence,
            retryable=bool(data.get("retryable", False)),
        )


class ExecutionBroker:
    """Dynamic registry/router for approved real worker execution providers."""

    def __init__(self) -> None:
        self._providers: dict[str, WorkerAdapter] = {}

    def register(self, adapter: WorkerAdapter) -> None:
        name = getattr(adapter, "name", "")
        if not name:
            raise ValueError("execution provider requires a name")
        self._providers[name] = adapter

    def providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def execute(self, provider: str, request: WorkerRequest) -> WorkerResult:
        try:
            adapter = self._providers[provider]
        except KeyError as exc:
            raise ExecutionBridgeError(f"execution provider not registered: {provider}") from exc
        return adapter.execute(request)
