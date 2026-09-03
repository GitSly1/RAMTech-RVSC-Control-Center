from __future__ import annotations

import io
import json
import unittest
from urllib import error
from unittest.mock import patch

from controller.generic_worker_host import RegisteredAgent, automatic_qa_handoff, dispatch_qa_payload, execute_payload, is_legacy_daniel_mission
from controller.work_package_controller import QA_ACCEPTED, QA_REJECTED, QAHandoffError


class GenericWorkerHostTests(unittest.TestCase):
    def setUp(self):
        self.daniel = RegisteredAgent("DEV-001", "Daniel", "Development", ("semantiq",), True, False)
        self.noah = RegisteredAgent("OPS-001", "Noah", "DevOps & Runtime", ("rvsc",), True, False)
        self.qa = RegisteredAgent("QA-001", "Quinn", "Quality Assurance", ("rvsc",), True, True)
        self.mission = {
            "agent_id": "OPS-001",
            "project": "rvsc",
            "repository": "GitSly1/RAMTech-RVSC-Control-Center",
            "wp_id": "RVSC-027C",
            "work_branch": "rvsc/RVSC-027C",
            "allowed_paths": ["controller/generic_worker_host.py"],
        }
        self.engineering = {
            "success": True,
            "run_id": "ENG-RUN",
            "commit_sha": "a" * 40,
            "work_branch": "rvsc/RVSC-027C",
            "pushed": True,
        }

    def test_identifies_only_historical_daniel_contracts_as_legacy(self):
        self.assertTrue(is_legacy_daniel_mission({"wp_id": "SEM-DANIEL-002"}))
        self.assertTrue(is_legacy_daniel_mission({"wp_id": " sem-daniel-003 "}))
        self.assertFalse(is_legacy_daniel_mission({"wp_id": "SEM-DANIEL-QUALIFICATION-001"}))
        self.assertFalse(is_legacy_daniel_mission({"wp_id": "SEM-DANIEL-027B-LIVE-QUALIFICATION"}))
        self.assertFalse(is_legacy_daniel_mission({"wp_id": "SEM-1234-ORDINARY"}))
        self.assertFalse(is_legacy_daniel_mission({"wp_id": ""}))

    def test_new_daniel_mission_uses_generic_engineering(self):
        mission = {"agent_id": "DEV-001", "project": "semantiq", "wp_id": "SEM-DANIEL-027B-LIVE-QUALIFICATION", "repository": "GitSly1/RAMTech-SEMANTIQ"}
        engineering = {"success": False, "run_id": "DEV-RUN"}
        with patch("controller.generic_worker_host.configured_agent", return_value=self.daniel), patch("controller.generic_worker_host._set_runtime_state"), patch("controller.generic_worker_host.execute_generic_engineering", return_value=engineering) as generic, patch("controller.generic_worker_host.daniel.execute_payload") as legacy:
            result = execute_payload({"protocol": "rvsc.worker.v1", "mission": mission})
        self.assertEqual(result, engineering)
        generic.assert_called_once()
        legacy.assert_not_called()

    def test_historical_daniel_missions_preserve_specific_handler(self):
        for wp_id in ("SEM-DANIEL-002", "SEM-DANIEL-003"):
            with self.subTest(wp_id=wp_id):
                mission = {"agent_id": "DEV-001", "project": "semantiq", "wp_id": wp_id, "repository": "GitSly1/RAMTech-SEMANTIQ"}
                engineering = {"success": False, "run_id": "LEGACY-RUN"}
                with patch("controller.generic_worker_host.configured_agent", return_value=self.daniel), patch("controller.generic_worker_host._set_runtime_state"), patch("controller.generic_worker_host.daniel.execute_payload", return_value=engineering) as legacy, patch("controller.generic_worker_host.execute_generic_engineering") as generic:
                    result = execute_payload({"protocol": "rvsc.worker.v1", "mission": mission})
                self.assertEqual(result, engineering)
                legacy.assert_called_once()
                generic.assert_not_called()

    def test_authorization_is_checked_before_routing(self):
        mission = {"agent_id": "DEV-001", "project": "rvsc", "wp_id": "RVSC-UNAUTHORIZED"}
        with patch("controller.generic_worker_host.configured_agent", return_value=self.daniel), patch("controller.generic_worker_host.execute_generic_engineering") as generic:
            with self.assertRaisesRegex(ValueError, "not authorized"):
                execute_payload({"protocol": "rvsc.worker.v1", "mission": mission})
        generic.assert_not_called()

    def test_structured_http_error_body_is_preserved(self):
        body = {"success": False, "summary": "checkout failed", "evidence": ["worker:qa"], "retryable": False}
        http_error = error.HTTPError("http://qa/execute", 500, "Internal Server Error", {}, io.BytesIO(json.dumps(body).encode("utf-8")))
        with patch("controller.generic_worker_host.request.urlopen", side_effect=http_error):
            with self.assertRaises(QAHandoffError) as raised:
                dispatch_qa_payload({"protocol": "rvsc.worker.v1", "mission": {}})
        self.assertEqual(raised.exception.category, "qa_http_error")
        self.assertEqual(raised.exception.http_status, 500)
        self.assertEqual(raised.exception.response, body)
        self.assertIn("checkout failed", str(raised.exception))

    def test_connection_failure_is_distinguished_and_fail_closed(self):
        with patch("controller.generic_worker_host.request.urlopen", side_effect=error.URLError("connection refused")):
            with self.assertRaises(QAHandoffError) as raised:
                dispatch_qa_payload({"protocol": "rvsc.worker.v1", "mission": {}})
        self.assertEqual(raised.exception.category, "transport_failure")
        self.assertTrue(raised.exception.retryable)

    def test_qa_rejected_remains_rejected(self):
        qa_result = {"success": True, "verdict": QA_REJECTED, "evidence": ["tests:failed"]}
        with patch("controller.generic_worker_host.select_registered_qa_agent", return_value=self.qa), patch("controller.generic_worker_host.dispatch_qa_payload", return_value=qa_result), patch("controller.generic_worker_host._checkpoint"):
            result = automatic_qa_handoff(self.noah, self.mission, self.engineering)
        self.assertFalse(result["success"])
        self.assertEqual(result["verdict"], QA_REJECTED)
        self.assertEqual(result["qa_handoff"]["classification"], "qa_rejected")

    def test_qa_accepted_remains_accepted_and_preserves_identity(self):
        qa_result = {"success": True, "verdict": QA_ACCEPTED, "evidence": ["tests:pass"]}
        with patch("controller.generic_worker_host.select_registered_qa_agent", return_value=self.qa), patch("controller.generic_worker_host.dispatch_qa_payload", return_value=qa_result), patch("controller.generic_worker_host._checkpoint"):
            result = automatic_qa_handoff(self.noah, self.mission, self.engineering)
        self.assertTrue(result["success"])
        self.assertEqual(result["verdict"], QA_ACCEPTED)
        self.assertEqual(result["qa_handoff"]["engineering_branch"], "rvsc/RVSC-027C")
        self.assertEqual(result["qa_handoff"]["engineering_commit_sha"], "a" * 40)
        self.assertEqual(result["qa_handoff"]["engineering_project"], "rvsc")
        self.assertEqual(result["qa_handoff"]["engineering_repository"], "GitSly1/RAMTech-RVSC-Control-Center")

    def test_malformed_qa_response_fails_closed(self):
        malformed = {"success": True, "evidence": ["tests:pass"]}
        with patch("controller.generic_worker_host.select_registered_qa_agent", return_value=self.qa), patch("controller.generic_worker_host.dispatch_qa_payload", return_value=malformed), patch("controller.generic_worker_host._checkpoint"):
            result = automatic_qa_handoff(self.noah, self.mission, self.engineering)
        self.assertFalse(result["success"])
        self.assertEqual(result["qa_handoff"]["classification"], "malformed_qa_response")
        self.assertEqual(result["qa_handoff"]["response"], malformed)

    def test_engineering_is_not_rerun_after_qa_transport_failure(self):
        with patch("controller.generic_worker_host.configured_agent", return_value=self.noah), patch("controller.generic_worker_host._set_runtime_state"), patch("controller.generic_worker_host.execute_generic_engineering", return_value=self.engineering) as engineering, patch("controller.generic_worker_host.select_registered_qa_agent", return_value=self.qa), patch("controller.generic_worker_host.dispatch_qa_payload", side_effect=QAHandoffError("connection refused", category="transport_failure", retryable=True)), patch("controller.generic_worker_host._checkpoint"):
            result = execute_payload({"protocol": "rvsc.worker.v1", "mission": self.mission})
        engineering.assert_called_once()
        self.assertFalse(result["success"])
        self.assertEqual(result["qa_handoff"]["classification"], "transport_failure")
        self.assertEqual(result["qa_handoff"]["engineering_commit_sha"], "a" * 40)


if __name__ == "__main__":
    unittest.main()
