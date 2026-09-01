from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = os.environ.get("RVSC_DANIEL_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("RVSC_DANIEL_MULTI_PORT", "8768"))


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    required: bool = True

    @property
    def ok(self) -> bool:
        return self.status in {"PASS", "READY", "RUNNING"}


def _run(command: list[str], cwd: Path = ROOT) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
        )
        return completed.returncode, completed.stdout.strip()
    except Exception as exc:
        return 1, str(exc)


def _tcp_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def collect_checks(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> list[Check]:
    checks: list[Check] = []

    checks.append(Check("Project root", "PASS" if (ROOT / "controller").is_dir() else "FAIL", str(ROOT)))
    checks.append(Check("Python", "PASS", sys.version.split()[0]))

    git = shutil.which("git")
    if git:
        rc, out = _run([git, "rev-parse", "--is-inside-work-tree"])
        checks.append(Check("Git", "PASS" if rc == 0 and out.lower() == "true" else "FAIL", git))
        rc, branch = _run([git, "branch", "--show-current"])
        checks.append(Check("Git branch", "PASS" if rc == 0 else "FAIL", branch or "unknown", required=False))
    else:
        checks.append(Check("Git", "FAIL", "git executable not found"))

    key_ready = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    checks.append(Check("OpenAI credential", "PASS" if key_ready else "FAIL", "configured" if key_ready else "OPENAI_API_KEY is not set"))

    try:
        import controller.daniel_multi_mission_host  # noqa: F401
        checks.append(Check("Daniel module", "PASS", "controller.daniel_multi_mission_host"))
    except Exception as exc:
        checks.append(Check("Daniel module", "FAIL", str(exc)))

    try:
        import controller.ops_console  # noqa: F401
        checks.append(Check("Operations console", "PASS", "controller.ops_console"))
    except Exception as exc:
        checks.append(Check("Operations console", "FAIL", str(exc)))

    running = _tcp_open(host, port)
    checks.append(Check("Daniel endpoint", "RUNNING" if running else "READY", f"{host}:{port}"))
    return checks


def required_ready(checks: Iterable[Check]) -> bool:
    return all(check.ok for check in checks if check.required)


def render(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = ["RVSC ENVIRONMENT PREFLIGHT", "-" * 72]
    for check in checks:
        lines.append(f"{check.name:<{width}}  {check.status:<8}  {check.detail}")
    lines.append("-" * 72)
    lines.append("SYSTEM STATE: " + ("READY" if required_ready(checks) else "BLOCKED"))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an RVSC Windows/Linux host before starting Daniel.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    checks = collect_checks(args.host, args.port)
    if args.json:
        print(json.dumps({"ready": required_ready(checks), "root": str(ROOT), "checks": [asdict(c) for c in checks]}, indent=2))
    else:
        print(render(checks))
    return 0 if required_ready(checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
