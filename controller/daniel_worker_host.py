from __future__ import annotations

import json
import os
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

OPENAI_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = os.environ.get("RVSC_OPENAI_MODEL", "gpt-5.6")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _openai_call(api_key: str, mission: dict[str, Any]) -> dict[str, Any]:
    prompt = (
        "You are DEV-001 Daniel, Lead Software Engineer inside RVSC. "
        "Respond as the assigned worker only. Do not claim actions you did not perform. "
        "Return a concise execution acknowledgement for this bounded mission, including "
        "agent id, work package, project, branch, objective, and acceptance criteria.\n\n"
        + json.dumps(mission, indent=2)
    )
    body = json.dumps({"model": DEFAULT_MODEL, "input": prompt}).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI transport error: {exc.reason}") from exc


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
    response = _openai_call(api_key, mission)
    ended = _utc_now()
    provider_response_id = str(response.get("id", ""))
    model = str(response.get("model", DEFAULT_MODEL))
    status = str(response.get("status", "unknown"))

    evidence = [
        f"run_id:{run_id}",
        f"agent_id:{mission['agent_id']}",
        f"wp_id:{mission.get('wp_id', '')}",
        f"project:{mission.get('project', '')}",
        f"branch:{mission.get('work_branch', '')}",
        f"started_at:{started}",
        f"ended_at:{ended}",
        "provider:openai",
        f"model:{model}",
        f"provider_response_id:{provider_response_id}",
        f"provider_status:{status}",
    ]
    return {
        "success": status == "completed",
        "summary": f"DEV-001 Daniel live invocation completed for {mission.get('wp_id', '')}",
        "evidence": evidence,
        "retryable": False,
    }


class DanielHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/execute":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = execute_payload(payload)
            encoded = json.dumps(result).encode("utf-8")
            self.send_response(200)
        except Exception as exc:  # fail closed and surface exact blocker
            encoded = json.dumps({
                "success": False,
                "summary": str(exc),
                "evidence": ["worker_host:daniel"],
                "retryable": False,
            }).encode("utf-8")
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[DanielHost] {fmt % args}")


def main() -> None:
    host = os.environ.get("RVSC_DANIEL_HOST", "127.0.0.1")
    port = int(os.environ.get("RVSC_DANIEL_PORT", "8767"))
    print(f"DEV-001 Daniel worker host listening on http://{host}:{port}/execute")
    ThreadingHTTPServer((host, port), DanielHandler).serve_forever()


if __name__ == "__main__":
    main()
