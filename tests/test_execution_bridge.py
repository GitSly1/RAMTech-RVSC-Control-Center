import json
import os
import unittest
from unittest.mock import patch

from controller.adapters import WorkerRequest
from controller.execution_bridge import (
    ExecutionBridgeError,
    ExecutionBroker,
    HttpJsonWorkerAdapter,
    ProviderConfig,
    ProviderResponse,
)


REQUEST = WorkerRequest(
    agent_id="DEV-001",
    wp_id="SEM-PILOT-001",
    project="SEMANTIQ",
    repository="GitSly1/RAMTech-SEMANTIQ",
    base_branch="main",
    work_branch="semantiq/daniel-pilot",
    objective="Implement one bounded SEMANTIQ change",
    allowed_paths=("semantiq/", "tests/"),
    acceptance_criteria=("tests pass", "evidence returned"),
)


class ExecutionBridgeTests(unittest.TestCase):
    def test_successful_provider_invocation_preserves_scope_and_evidence(self):
        captured = {}

        def transport(request, timeout):
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return ProviderResponse(
                status=200,
                body=json.dumps({"success": True, "summary": "Daniel completed bounded mission", "evidence": ["commit:abc123", "tests:pass"]}).encode("utf-8"),
            )

        adapter = HttpJsonWorkerAdapter(ProviderConfig(name="primary", endpoint="https://worker.example/run", timeout_seconds=30), transport=transport)
        result = adapter.execute(REQUEST)
        self.assertTrue(result.success)
        self.assertIn("commit:abc123", result.evidence)
        self.assertIn("provider:primary", result.evidence)
        self.assertEqual(captured["timeout"], 30)
        mission = captured["payload"]["mission"]
        self.assertEqual(mission["agent_id"], "DEV-001")
        self.assertEqual(mission["allowed_paths"], ["semantiq/", "tests/"])

    def test_health_preflight_allows_ready_worker_then_dispatches(self):
        calls = []

        def transport(request, timeout):
            calls.append((request.method, request.full_url))
            if request.method == "GET":
                return ProviderResponse(status=200, body=json.dumps({"ready": True, "worker": "DEV-001", "status": "idle"}).encode("utf-8"))
            return ProviderResponse(status=200, body=json.dumps({"success": True, "summary": "ok", "evidence": ["ack:DEV-001"]}).encode("utf-8"))

        adapter = HttpJsonWorkerAdapter(ProviderConfig(name="daniel", endpoint="http://127.0.0.1:8768/execute", require_healthcheck=True), transport=transport)
        result = adapter.execute(REQUEST)
        self.assertTrue(result.success)
        self.assertEqual(calls[0], ("GET", "http://127.0.0.1:8768/health"))
        self.assertEqual(calls[1], ("POST", "http://127.0.0.1:8768/execute"))

    def test_health_preflight_blocks_not_ready_worker_without_dispatch(self):
        calls = []

        def transport(request, timeout):
            calls.append(request.method)
            return ProviderResponse(status=200, body=json.dumps({"ready": False, "worker": "DEV-001", "status": "busy", "active_mission": "SEM-OTHER"}).encode("utf-8"))

        adapter = HttpJsonWorkerAdapter(ProviderConfig(name="daniel", endpoint="http://127.0.0.1:8768/execute", require_healthcheck=True), transport=transport)
        result = adapter.execute(REQUEST)
        self.assertFalse(result.success)
        self.assertTrue(result.retryable)
        self.assertEqual(calls, ["GET"])
        self.assertIn("preflight:not_ready", result.evidence)
        self.assertIn("active_mission:SEM-OTHER", result.evidence)

    def test_health_preflight_marks_unreachable_worker_retryable(self):
        adapter = HttpJsonWorkerAdapter(
            ProviderConfig(name="daniel", endpoint="http://127.0.0.1:8768/execute", require_healthcheck=True),
            transport=lambda request, timeout: (_ for _ in ()).throw(ExecutionBridgeError("provider transport failed: refused")),
        )
        result = adapter.execute(REQUEST)
        self.assertFalse(result.success)
        self.assertTrue(result.retryable)
        self.assertIn("preflight:unreachable", result.evidence)

    def test_missing_credential_fails_closed(self):
        adapter = HttpJsonWorkerAdapter(ProviderConfig(name="secured", endpoint="https://worker.example/run", token_env="RVSC_TEST_TOKEN"), transport=lambda request, timeout: ProviderResponse(status=200, body=b"{}"))
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ExecutionBridgeError):
                adapter.execute(REQUEST)

    def test_server_failure_is_retryable(self):
        adapter = HttpJsonWorkerAdapter(ProviderConfig(name="primary", endpoint="https://worker.example/run"), transport=lambda request, timeout: ProviderResponse(status=503, body=b"unavailable"))
        result = adapter.execute(REQUEST)
        self.assertFalse(result.success)
        self.assertTrue(result.retryable)
        self.assertIn("http_status:503", result.evidence)

    def test_structured_worker_failure_preserves_root_cause(self):
        body = json.dumps({"success": False, "summary": "SEMANTIQ repository must be clean before Daniel mission", "evidence": ["worker_host:daniel"], "retryable": False}).encode("utf-8")
        adapter = HttpJsonWorkerAdapter(ProviderConfig(name="primary", endpoint="https://worker.example/run"), transport=lambda request, timeout: ProviderResponse(status=500, body=body))
        result = adapter.execute(REQUEST)
        self.assertFalse(result.success)
        self.assertEqual(result.summary, "SEMANTIQ repository must be clean before Daniel mission")
        self.assertFalse(result.retryable)
        self.assertIn("worker_host:daniel", result.evidence)
        self.assertIn("http_status:500", result.evidence)

    def test_invalid_provider_response_is_rejected(self):
        adapter = HttpJsonWorkerAdapter(ProviderConfig(name="primary", endpoint="https://worker.example/run"), transport=lambda request, timeout: ProviderResponse(status=200, body=b"not-json"))
        with self.assertRaises(ExecutionBridgeError):
            adapter.execute(REQUEST)

    def test_broker_routes_dynamically(self):
        adapter = HttpJsonWorkerAdapter(ProviderConfig(name="primary", endpoint="https://worker.example/run"), transport=lambda request, timeout: ProviderResponse(status=200, body=json.dumps({"success": True, "summary": "ok", "evidence": []}).encode("utf-8")))
        broker = ExecutionBroker()
        broker.register(adapter)
        self.assertEqual(broker.providers(), ("primary",))
        self.assertTrue(broker.execute("primary", REQUEST).success)

    def test_unknown_provider_is_rejected(self):
        broker = ExecutionBroker()
        with self.assertRaises(ExecutionBridgeError):
            broker.execute("missing", REQUEST)


if __name__ == "__main__":
    unittest.main()
