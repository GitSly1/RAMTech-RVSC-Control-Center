"""Controlled runtime supervisor for RVSC Golden Team worker hosts."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


REPOSITORY_ENV_KEYS = (
    "RVSC_RVSC_REPO",
    "RVSC_SEMANTIQ_REPO",
    "RVSC_MOXIE_REPO",
)

QA_ROUTING_ENV_KEYS = (
    "RVSC_QA_ENDPOINT",
    "RVSC_QA_URL",
    "RVSC_QA_WORKER_ENDPOINT",
    "RVSC_QA_WORKER_URL",
)

DEFAULT_QA_ENDPOINT = "http://127.0.0.1:8771/execute"


class RuntimeSupervisorError(RuntimeError):
    """Base error raised by the runtime supervisor."""


class RuntimeConflictError(RuntimeSupervisorError):
    """Raised when a configured port is occupied by an unexpected worker."""


@dataclass(frozen=True)
class WorkerConfig:
    agent_id: str
    name: str
    port: int
    role: str
    command: Optional[Tuple[str, ...]] = None


@dataclass
class HealthResult:
    healthy: bool
    agent_id: Optional[str] = None
    detail: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "healthy": self.healthy,
            "agent_id": self.agent_id,
            "detail": self.detail,
            "payload": self.payload,
        }


@dataclass
class WorkerStatus:
    agent_id: str
    name: str
    port: int
    running: bool
    ready: bool
    health_result: Dict[str, Any]
    supervisor_owned: bool
    restart_count: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "port": self.port,
            "running": self.running,
            "ready": self.ready,
            "health_result": self.health_result,
            "supervisor_owned": self.supervisor_owned,
            "restart_count": self.restart_count,
        }


def golden_team_configs() -> Tuple[WorkerConfig, ...]:
    """Return the controlled Golden Team identity and port assignments."""
    return (
        WorkerConfig("OPS-001", "Noah", 8770, "engineering"),
        WorkerConfig("DEV-001", "Daniel", 8765, "engineering"),
        WorkerConfig("QA-001", "Quinn", 8771, "qa"),
    )


class RuntimeSupervisor:
    """Owns and monitors worker processes started through this instance.

    Healthy matching workers which predate the supervisor are observed but are
    never adopted as owned processes. Unknown listeners and identity mismatches
    are treated as conflicts and are not terminated.
    """

    def __init__(
        self,
        configs: Optional[Iterable[WorkerConfig]] = None,
        repository_mappings: Optional[Mapping[str, str]] = None,
        qa_endpoint: str = DEFAULT_QA_ENDPOINT,
        worker_module: str = "controller.generic_worker_host",
        max_restarts: int = 3,
        health_timeout: float = 1.0,
        process_factory: Optional[Callable[..., Any]] = None,
        health_checker: Optional[Callable[[WorkerConfig], Any]] = None,
        port_checker: Optional[Callable[[int], bool]] = None,
    ) -> None:
        selected = tuple(configs or golden_team_configs())
        self._configs = self._validate_configs(selected)
        self._config_by_id = {config.agent_id: config for config in self._configs}
        self.qa_endpoint = qa_endpoint.rstrip("/")
        self.worker_module = worker_module
        self.max_restarts = max(0, int(max_restarts))
        self.health_timeout = max(0.01, float(health_timeout))
        self._process_factory = process_factory or subprocess.Popen
        self._health_checker = health_checker or self._http_health
        self._port_checker = port_checker or self._is_port_open
        self._owned: Dict[str, Any] = {}
        self._restart_counts: Dict[str, int] = {
            config.agent_id: 0 for config in self._configs
        }
        self._intentional_stops = set()
        self._last_errors: Dict[str, str] = {}
        self._lock = threading.RLock()
        self._shutdown_requested = threading.Event()

        supplied = repository_mappings or {}
        unknown = set(supplied).difference(REPOSITORY_ENV_KEYS)
        if unknown:
            raise ValueError("unsupported repository mapping keys: %s" % sorted(unknown))
        self.repository_mappings: Dict[str, str] = {}
        for key in REPOSITORY_ENV_KEYS:
            value = supplied.get(key, os.environ.get(key))
            if value:
                self.repository_mappings[key] = str(value)

    @staticmethod
    def _validate_configs(configs: Sequence[WorkerConfig]) -> Tuple[WorkerConfig, ...]:
        if not configs:
            raise ValueError("at least one worker configuration is required")
        ids = set()
        ports = set()
        for config in configs:
            if not config.agent_id or not config.name:
                raise ValueError("worker identity and name are required")
            if config.agent_id in ids:
                raise ValueError("duplicate worker identity: %s" % config.agent_id)
            if config.port in ports:
                raise ValueError("duplicate worker port: %s" % config.port)
            if not 1 <= int(config.port) <= 65535:
                raise ValueError("invalid worker port: %s" % config.port)
            ids.add(config.agent_id)
            ports.add(config.port)
        return tuple(configs)

    @property
    def configs(self) -> Tuple[WorkerConfig, ...]:
        return self._configs

    def build_launch(self, worker: Any) -> Tuple[List[str], Dict[str, str]]:
        config = self._resolve(worker)
        if config.command:
            command = list(config.command)
        else:
            command = [sys.executable, "-m", self.worker_module]

        environment = os.environ.copy()
        environment.update(self.repository_mappings)
        environment.update(
            {
                "RVSC_AGENT_ID": config.agent_id,
                "RVSC_WORKER_AGENT_ID": config.agent_id,
                "RVSC_AGENT_NAME": config.name,
                "RVSC_AGENT_ROLE": config.role,
                "RVSC_WORKER_PORT": str(config.port),
                "RVSC_PORT": str(config.port),
            }
        )
        if config.role == "engineering":
            environment.update(
                {key: self.qa_endpoint for key in QA_ROUTING_ENV_KEYS}
            )
        else:
            for key in QA_ROUTING_ENV_KEYS:
                environment.pop(key, None)
        return command, environment

    def start(self, worker: Any) -> WorkerStatus:
        config = self._resolve(worker)
        with self._lock:
            process = self._owned.get(config.agent_id)
            if process is not None and process.poll() is None:
                return self._status_for(config)
            if process is not None:
                self._owned.pop(config.agent_id, None)

            health = self.check_health(config)
            occupied = bool(self._port_checker(config.port))
            if health.healthy:
                if health.agent_id != config.agent_id:
                    raise RuntimeConflictError(
                        "port %s reports identity %r; expected %s"
                        % (config.port, health.agent_id, config.agent_id)
                    )
                self._intentional_stops.discard(config.agent_id)
                self._restart_counts[config.agent_id] = 0
                self._last_errors.pop(config.agent_id, None)
                return self._status_for(config, known_health=health)
            if occupied:
                raise RuntimeConflictError(
                    "port %s is occupied without a healthy matching identity"
                    % config.port
                )

            self._intentional_stops.discard(config.agent_id)
            self._restart_counts[config.agent_id] = 0
            self._spawn(config)
            return self._status_for(config)

    def start_all(self) -> List[WorkerStatus]:
        started_here = []
        try:
            for config in self._configs:
                was_owned = config.agent_id in self._owned
                self.start(config)
                if not was_owned and config.agent_id in self._owned:
                    started_here.append(config.agent_id)
        except Exception:
            for agent_id in reversed(started_here):
                self.stop(agent_id)
            raise
        return self.status()

    def _spawn(self, config: WorkerConfig) -> Any:
        command, environment = self.build_launch(config)
        process = self._process_factory(command, env=environment)
        self._owned[config.agent_id] = process
        self._last_errors.pop(config.agent_id, None)
        return process

    def stop(self, worker: Any, timeout: float = 5.0) -> bool:
        config = self._resolve(worker)
        with self._lock:
            self._intentional_stops.add(config.agent_id)
            process = self._owned.get(config.agent_id)
            if process is None:
                return False
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=max(0.0, timeout))
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=max(0.0, timeout))
            finally:
                self._owned.pop(config.agent_id, None)
            return True

    def stop_all(self, timeout: float = 5.0) -> None:
        for config in reversed(self._configs):
            self.stop(config, timeout=timeout)

    def check_health(self, worker: Any) -> HealthResult:
        config = self._resolve(worker)
        try:
            return self._normalise_health(self._health_checker(config))
        except Exception as exc:
            return HealthResult(False, detail="health check failed: %s" % exc)

    @staticmethod
    def _normalise_health(value: Any) -> HealthResult:
        if isinstance(value, HealthResult):
            return value
        if isinstance(value, bool):
            return HealthResult(value)
        if not isinstance(value, Mapping):
            return HealthResult(False, detail="invalid health response")

        payload = dict(value)
        identity = payload.get("agent_id") or payload.get("identity") or payload.get("id")
        for nested_key in ("worker", "agent"):
            nested = payload.get(nested_key)
            if identity is None and isinstance(nested, Mapping):
                identity = nested.get("agent_id") or nested.get("identity") or nested.get("id")

        if "healthy" in payload:
            healthy = bool(payload["healthy"])
        elif "ok" in payload:
            healthy = bool(payload["ok"])
        elif "ready" in payload:
            healthy = bool(payload["ready"])
        elif "status" in payload:
            healthy = str(payload["status"]).lower() in {
                "ok",
                "healthy",
                "ready",
                "running",
            }
        else:
            healthy = True
        detail = str(payload.get("detail") or payload.get("message") or "")
        return HealthResult(healthy, str(identity) if identity is not None else None, detail, payload)

    def _http_health(self, config: WorkerConfig) -> HealthResult:
        url = "http://127.0.0.1:%s/health" % config.port
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.health_timeout) as response:
                status = getattr(response, "status", response.getcode())
                if status < 200 or status >= 300:
                    return HealthResult(False, detail="HTTP %s" % status)
                data = json.loads(response.read().decode("utf-8"))
                return self._normalise_health(data)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return HealthResult(False, detail="unavailable: %s" % exc)

    @staticmethod
    def _is_port_open(port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(0.2)
            return sock.connect_ex(("127.0.0.1", int(port))) == 0
        finally:
            sock.close()

    def poll_once(self) -> List[WorkerStatus]:
        """Observe owned workers and apply the bounded restart policy once."""
        with self._lock:
            for config in self._configs:
                process = self._owned.get(config.agent_id)
                if process is None or process.poll() is None:
                    continue

                self._owned.pop(config.agent_id, None)
                if config.agent_id in self._intentional_stops:
                    continue

                count = self._restart_counts[config.agent_id]
                if count >= self.max_restarts:
                    self._last_errors[config.agent_id] = "restart limit reached"
                    continue

                self._restart_counts[config.agent_id] = count + 1
                health = self.check_health(config)
                if health.healthy:
                    if health.agent_id == config.agent_id:
                        continue
                    self._last_errors[config.agent_id] = "identity conflict after exit"
                    continue
                if self._port_checker(config.port):
                    self._last_errors[config.agent_id] = "port conflict after exit"
                    continue
                try:
                    self._spawn(config)
                except Exception as exc:
                    self._last_errors[config.agent_id] = "restart failed: %s" % exc
            return self.status()

    def status(self) -> List[WorkerStatus]:
        with self._lock:
            return [self._status_for(config) for config in self._configs]

    def status_dicts(self) -> List[Dict[str, Any]]:
        return [item.as_dict() for item in self.status()]

    def _status_for(
        self,
        config: WorkerConfig,
        known_health: Optional[HealthResult] = None,
    ) -> WorkerStatus:
        process = self._owned.get(config.agent_id)
        owned_running = process is not None and process.poll() is None
        health = known_health or self.check_health(config)
        identity_matches = health.agent_id == config.agent_id
        ready = bool(health.healthy and identity_matches)
        running = bool(owned_running or ready or self._port_checker(config.port))
        health_payload = health.as_dict()
        if config.agent_id in self._last_errors:
            health_payload["supervisor_error"] = self._last_errors[config.agent_id]
        return WorkerStatus(
            agent_id=config.agent_id,
            name=config.name,
            port=config.port,
            running=running,
            ready=ready,
            health_result=health_payload,
            supervisor_owned=owned_running,
            restart_count=self._restart_counts[config.agent_id],
        )

    def _resolve(self, worker: Any) -> WorkerConfig:
        if isinstance(worker, WorkerConfig):
            configured = self._config_by_id.get(worker.agent_id)
            if configured != worker:
                raise KeyError("worker is not managed by this supervisor")
            return configured
        try:
            return self._config_by_id[str(worker)]
        except KeyError:
            raise KeyError("unknown worker identity: %s" % worker)

    def request_shutdown(self) -> None:
        self._shutdown_requested.set()

    def run(self, poll_interval: float = 1.0) -> None:
        """Start the team and supervise it until a controlled shutdown."""
        self.start_all()
        try:
            while not self._shutdown_requested.wait(max(0.05, poll_interval)):
                self.poll_once()
        finally:
            self.stop_all()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RVSC Golden Team runtime supervisor")
    parser.add_argument("action", nargs="?", choices=("run", "status"), default="run")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--max-restarts", type=int, default=3)
    parser.add_argument("--qa-endpoint", default=DEFAULT_QA_ENDPOINT)
    parser.add_argument("--worker-module", default="controller.generic_worker_host")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    supervisor = RuntimeSupervisor(
        qa_endpoint=args.qa_endpoint,
        worker_module=args.worker_module,
        max_restarts=args.max_restarts,
    )
    if args.action == "status":
        print(json.dumps(supervisor.status_dicts(), indent=2, sort_keys=True))
        return 0

    def stop_handler(_signum: int, _frame: Any) -> None:
        supervisor.request_shutdown()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    try:
        supervisor.run(poll_interval=args.poll_interval)
    except RuntimeConflictError as exc:
        print("runtime supervisor conflict: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
