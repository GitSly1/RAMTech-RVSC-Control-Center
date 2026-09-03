from __future__ import annotations

import io
import json
import tempfile
import unittest
from urllib import error
from unittest.mock import patch

import controller.generic_worker_host as host
from controller.generic_worker_host import RegisteredAgent, automatic_qa_handoff, dispatch_qa_payload, execute_payload, is_legacy_daniel_mission
from controller.runtime_state_store import DurableRuntimeStateStore
from controller.work_package_controller import QA_ACCEPTED, QA_REJECTED, QAHandoffError


class FakeResponse:
    def __init__(self, payload, status=200):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.body = io.BytesIO(raw)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size=-1):
        return self.body.read(size)

    def getcode(self):
        return self.status


class GenericWorkerHostTests(unittest.TestCase):
    def setUp(self):
        self.daniel = RegisteredAgent("DEV-001", "Daniel", "Development", ("semantiq",), True, False)
        self.noah = RegisteredAgent("OPS-001", "Noah", "DevOps & Runtime", ("rvsc",), True, False)
        self.qa = RegisteredAgent("QA-001", "Quinn", "Quality Assurance", ("rvsc",), True, True)
        self.alternate_qa = RegisteredAgent("PERF-001", "Performance", "Performance QA", ("rvsc",), True, True)
        self.mission = {"agent_id": "OPS-001", "project": "rvsc", "repository": "GitSly1/RAMTech-RVSC-Control-Center", "wp_id": "RVSC-027C", "run_id": "RUN-027C", "base_branch": "rvsc/base", "work_branch": "rvsc/RVSC-027C", "allowed_paths": ["controller/generic_worker_host.py"], "validation_commands": [{"name": "tests", "argv": ["python", "-m", "unittest"]}]}
        self.engineering = {"success": True, "run_id": "RUN-027C", "project": "rvsc", "repository": "GitSly1/RAMTech-RVSC-Control-Center", "commit_sha": "a" * 40, "work_branch": "rvsc/RVSC-027C", "pushed": True}
        with host._STATE_LOCK:
            host._RUNTIME_STATE.update({"active_mission": None, "active_run_id": None, "last_run_id": None, "last_activity": None, "last_result": None, "last_checkpoint": None, "checkpoint_evidence": (), "recovery_required": False, "recovered_checkpoint": None, "lifecycle_state": "idle", "recovery_context": None, "recovery_digest": None, "recovery_attempted": False, "engineering_result": None, "qa_dispatch_started": False, "terminal_recovery": None})

    def health(self, worker="QA-001", **updates):
        payload = {"protocol": "rvsc.worker.health.v1", "service": "rvsc-generic-worker", "worker": worker, "ready": True, "worker_enabled": True, "qa_eligible": True}
        payload.update(updates)
        return payload

    def test_identifies_only_historical_daniel_contracts_as_legacy(self):
        self.assertTrue(is_legacy_daniel_mission({"wp_id": "SEM-DANIEL-002"}))
        self.assertTrue(is_legacy_daniel_mission({"wp_id": " sem-daniel-003 "}))
        self.assertFalse(is_legacy_daniel_mission({"wp_id": "SEM-DANIEL-QUALIFICATION-001"}))

    def test_new_daniel_mission_uses_generic_engineering(self):
        mission = {"agent_id": "DEV-001", "project": "semantiq", "wp_id": "SEM-DANIEL-027B-LIVE-QUALIFICATION", "run_id": "DEV-RUN", "repository": "GitSly1/RAMTech-SEMANTIQ"}
        engineering = {"success": False, "run_id": "DEV-RUN"}
        with patch("controller.generic_worker_host.configured_agent", return_value=self.daniel), patch("controller.generic_worker_host._set_runtime_state"), patch("controller.generic_worker_host.execute_generic_engineering", return_value=engineering) as generic, patch("controller.generic_worker_host.daniel.execute_payload") as legacy:
            self.assertEqual(execute_payload({"protocol": "rvsc.worker.v1", "mission": mission}), engineering)
        generic.assert_called_once()
        legacy.assert_not_called()

    def test_authorization_is_checked_before_routing(self):
        mission = {"agent_id": "DEV-001", "project": "rvsc", "wp_id": "RVSC-UNAUTHORIZED"}
        with patch("controller.generic_worker_host.configured_agent", return_value=self.daniel), patch("controller.generic_worker_host.execute_generic_engineering") as generic:
            with self.assertRaisesRegex(ValueError, "not authorized"):
                execute_payload({"protocol": "rvsc.worker.v1", "mission": mission})
        generic.assert_not_called()

    def test_health_endpoint_is_safely_derived_from_execution_endpoint(self):
        self.assertEqual(host.qa_health_endpoint("https://qa.example.test/workers/current/execute?route=blue"), "https://qa.example.test/workers/current/health?route=blue")
        for invalid in ("", "file:///worker/execute", "https://user:secret@qa.example/execute", "https://qa.example/health", "https://qa.example/execute#fragment"):
            with self.subTest(invalid=invalid), self.assertRaises(QAHandoffError):
                host.qa_health_endpoint(invalid)

    def test_endpoint_identity_selects_exact_live_registered_agent(self):
        agents = (self.alternate_qa, self.qa, self.noah)
        with patch("controller.generic_worker_host.load_agents", return_value=agents), patch("controller.generic_worker_host.request.urlopen", return_value=FakeResponse(self.health())) as urlopen:
            selected = host.select_registered_qa_agent(self.noah.agent_id, "rvsc", "http://qa.example/execute")
        self.assertEqual(selected, self.qa)
        requested = urlopen.call_args.args[0]
        self.assertEqual(requested.full_url, "http://qa.example/health")
        self.assertEqual(requested.get_method(), "GET")

    def test_endpoint_identity_is_used_in_mission_with_exact_source_revision(self):
        agents = (self.alternate_qa, self.qa, self.noah)
        accepted = {"success": True, "verdict": QA_ACCEPTED, "evidence": ["tests:pass"]}
        with patch("controller.generic_worker_host.load_agents", return_value=agents), patch("controller.generic_worker_host.request.urlopen", return_value=FakeResponse(self.health())), patch("controller.generic_worker_host.dispatch_qa_payload", return_value=accepted) as dispatch, patch("controller.generic_worker_host._checkpoint"):
            result = automatic_qa_handoff(self.noah, self.mission, self.engineering)
        qa_mission = dispatch.call_args.args[0]["mission"]
        self.assertEqual(qa_mission["agent_id"], self.qa.agent_id)
        self.assertEqual(qa_mission["engineering_branch"], self.engineering["work_branch"])
        self.assertEqual(qa_mission["engineering_commit_sha"], self.engineering["commit_sha"])
        self.assertEqual(result["qa_handoff"]["qa_agent_id"], self.qa.agent_id)

    def test_malformed_or_unavailable_health_fails_closed(self):
        invalid_payloads = (
            b"not-json",
            [],
            self.health(protocol="other"),
            self.health(service="other"),
            self.health(worker=""),
            self.health(ready=False),
            self.health(worker_enabled=False),
            self.health(qa_eligible=False),
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), patch("controller.generic_worker_host.request.urlopen", return_value=FakeResponse(payload)), patch("controller.generic_worker_host.load_agents", return_value=(self.qa,)):
                with self.assertRaises(QAHandoffError):
                    host.select_registered_qa_agent(self.noah.agent_id, "rvsc", "http://qa.example/execute")
        with patch("controller.generic_worker_host.request.urlopen", side_effect=error.URLError("private-network-detail")):
            with self.assertRaises(QAHandoffError) as raised:
                host.select_registered_qa_agent(self.noah.agent_id, "rvsc", "http://qa.example/execute")
        self.assertEqual(raised.exception.category, "transport_failure")
        self.assertNotIn("private-network-detail", str(raised.exception))

    def test_invalid_live_registry_identity_never_falls_back(self):
        invalid_registry_cases = (
            (self.health(worker="UNKNOWN-001"), (self.qa, self.alternate_qa)),
            (self.health(worker=self.noah.agent_id), (self.noah, self.qa)),
            (self.health(), (RegisteredAgent(self.qa.agent_id, self.qa.name, self.qa.role, self.qa.projects, False, True), self.alternate_qa)),
            (self.health(), (RegisteredAgent(self.qa.agent_id, self.qa.name, self.qa.role, self.qa.projects, True, False), self.alternate_qa)),
            (self.health(), (RegisteredAgent(self.qa.agent_id, self.qa.name, self.qa.role, ("other",), True, True), self.alternate_qa)),
        )
        for health_payload, agents in invalid_registry_cases:
            with self.subTest(worker=health_payload["worker"], agents=agents), patch("controller.generic_worker_host.request.urlopen", return_value=FakeResponse(health_payload)), patch("controller.generic_worker_host.load_agents", return_value=agents):
                with self.assertRaises(QAHandoffError):
                    host.select_registered_qa_agent(self.noah.agent_id, "rvsc", "http://qa.example/execute")

    def test_invalid_endpoint_identity_blocks_dispatch_without_registry_fallback(self):
        with patch("controller.generic_worker_host.request.urlopen", return_value=FakeResponse(self.health(worker="UNKNOWN-001"))), patch("controller.generic_worker_host.load_agents", return_value=(self.qa, self.alternate_qa)), patch("controller.generic_worker_host.dispatch_qa_payload") as dispatch:
            result = automatic_qa_handoff(self.noah, self.mission, self.engineering)
        self.assertFalse(result["success"])
        self.assertEqual(result["qa_handoff"]["classification"], "qa_endpoint_invalid")
        dispatch.assert_not_called()

    def test_structured_http_error_body_is_preserved(self):
        body = {"success": False, "summary": "checkout failed", "evidence": ["worker:qa"], "retryable": False}
        http_error = error.HTTPError("http://qa/execute", 500, "Internal Server Error", {}, io.BytesIO(json.dumps(body).encode("utf-8")))
        with patch("controller.generic_worker_host.request.urlopen", side_effect=http_error):
            with self.assertRaises(QAHandoffError) as raised:
                dispatch_qa_payload({"protocol": "rvsc.worker.v1", "mission": {}})
        self.assertEqual(raised.exception.category, "qa_http_error")
        self.assertEqual(raised.exception.response, body)

    def test_qa_rejected_remains_rejected(self):
        with patch("controller.generic_worker_host.select_registered_qa_agent", return_value=self.qa), patch("controller.generic_worker_host.dispatch_qa_payload", return_value={"success": True, "verdict": QA_REJECTED, "evidence": ["tests:failed"]}), patch("controller.generic_worker_host._checkpoint"):
            result = automatic_qa_handoff(self.noah, self.mission, self.engineering)
        self.assertFalse(result["success"])
        self.assertEqual(result["qa_handoff"]["classification"], "qa_rejected")

    def test_qa_accepted_preserves_identity(self):
        with patch("controller.generic_worker_host.select_registered_qa_agent", return_value=self.qa), patch("controller.generic_worker_host.dispatch_qa_payload", return_value={"success": True, "verdict": QA_ACCEPTED, "evidence": ["tests:pass"]}), patch("controller.generic_worker_host._checkpoint"):
            result = automatic_qa_handoff(self.noah, self.mission, self.engineering)
        self.assertTrue(result["success"])
        self.assertEqual(result["qa_handoff"]["engineering_commit_sha"], "a" * 40)

    def _install_interrupted_state(self, *, engineering_result=None, qa_started=False, checkpoint=None):
        context = host._mission_context(self.mission)
        with host._STATE_LOCK:
            host._RUNTIME_STATE.update({"active_mission": self.mission["wp_id"], "active_run_id": self.mission["run_id"], "recovery_required": True, "lifecycle_state": "recovery_required", "recovery_context": context, "recovery_digest": host._context_digest(context), "recovery_attempted": False, "last_checkpoint": checkpoint or ("engineering_result_persisted" if engineering_result else "mission_acknowledged"), "recovered_checkpoint": checkpoint, "engineering_result": engineering_result, "qa_dispatch_started": qa_started})

    def test_exact_recovery_reuses_persisted_result_without_duplicate_execution(self):
        self._install_interrupted_state(engineering_result=self.engineering)
        qa_result = {**self.engineering, "success": True, "verdict": QA_ACCEPTED}
        with patch("controller.generic_worker_host.configured_agent", return_value=self.noah), patch("controller.generic_worker_host._set_runtime_state") as state_update, patch("controller.generic_worker_host.execute_generic_engineering") as engineering, patch("controller.generic_worker_host.automatic_qa_handoff", return_value=qa_result) as qa:
            result = execute_payload({"protocol": "rvsc.worker.v1", "recovery": True, "mission": self.mission})
        self.assertTrue(result["success"])
        engineering.assert_not_called()
        qa.assert_called_once()
        self.assertTrue(any(call.kwargs.get("lifecycle_state") == "recovered" for call in state_update.call_args_list))

    def test_mismatched_recovery_is_refused(self):
        self._install_interrupted_state()
        with patch("controller.generic_worker_host.configured_agent", return_value=self.noah), patch("controller.generic_worker_host._set_runtime_state"):
            with self.assertRaisesRegex(RuntimeError, "exactly match|identity mismatch"):
                execute_payload({"protocol": "rvsc.worker.v1", "recovery": True, "mission": dict(self.mission, run_id="OTHER-RUN")})

    def test_duplicate_dispatch_and_unknown_qa_are_refused(self):
        self._install_interrupted_state(engineering_result=self.engineering, qa_started=True)
        with patch("controller.generic_worker_host.configured_agent", return_value=self.noah), patch("controller.generic_worker_host._set_runtime_state"):
            with self.assertRaisesRegex(RuntimeError, "already dispatched"):
                execute_payload({"protocol": "rvsc.worker.v1", "recovery": True, "mission": self.mission})
        with patch("controller.generic_worker_host.configured_agent", return_value=self.noah):
            with self.assertRaisesRegex(RuntimeError, "refusing duplicate dispatch"):
                execute_payload({"protocol": "rvsc.worker.v1", "mission": self.mission})

    def test_automatic_startup_recovery_uses_exact_persisted_context(self):
        self._install_interrupted_state(engineering_result=self.engineering)
        with patch("controller.generic_worker_host.configured_agent", return_value=self.noah), patch("controller.generic_worker_host.execute_payload", return_value={"success": True}) as execute:
            thread = host._start_automatic_recovery()
            self.assertIsNotNone(thread)
            thread.join(timeout=2)
        execute.assert_called_once_with({"protocol": "rvsc.worker.v1", "recovery": True, "mission": host._mission_context(self.mission)})

    def test_automatic_and_http_recovery_share_execution_lock(self):
        self._install_interrupted_state()
        self.assertTrue(host._EXECUTION_LOCK.acquire(blocking=False))
        try:
            with patch("controller.generic_worker_host.configured_agent", return_value=self.noah):
                with self.assertRaisesRegex(RuntimeError, "already in progress"):
                    execute_payload({"protocol": "rvsc.worker.v1", "recovery": True, "mission": self.mission})
        finally:
            host._EXECUTION_LOCK.release()

    def test_workspace_checkpoint_is_restored_before_reexecution(self):
        self._install_interrupted_state(checkpoint="implementation_applied")
        failed = {"success": False, "run_id": self.mission["run_id"], "project": "rvsc", "repository": self.mission["repository"], "work_branch": self.mission["work_branch"]}
        with patch("controller.generic_worker_host.configured_agent", return_value=self.noah), patch("controller.generic_worker_host._set_runtime_state"), patch("controller.generic_worker_host.recover_controlled_workspace", return_value=("workspace_restore:success",)) as restore, patch("controller.generic_worker_host.execute_generic_engineering", return_value=failed):
            execute_payload({"protocol": "rvsc.worker.v1", "recovery": True, "mission": self.mission})
        restore.assert_called_once_with(self.mission)

    def test_restore_detects_durable_interrupted_state(self):
        context = host._mission_context(self.mission)
        saved = {**host._RUNTIME_STATE, "active_mission": self.mission["wp_id"], "active_run_id": self.mission["run_id"], "recovery_context": context, "recovery_digest": host._context_digest(context), "last_checkpoint": "mission_acknowledged", "recovery_attempted": False, "qa_dispatch_started": False, "lifecycle_state": "executing"}
        with tempfile.TemporaryDirectory() as temp:
            DurableRuntimeStateStore(temp).save("OPS-001", saved)
            with patch("controller.generic_worker_host.configured_agent", return_value=self.noah), patch("controller.generic_worker_host._state_store", return_value=DurableRuntimeStateStore(temp)):
                self.assertTrue(host._restore_runtime_state())
        self.assertTrue(host._RUNTIME_STATE["recovery_required"])
        self.assertEqual(host._RUNTIME_STATE["lifecycle_state"], "recovery_required")


if __name__ == "__main__":
    unittest.main()
