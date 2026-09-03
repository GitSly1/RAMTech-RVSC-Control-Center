from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from controller.generic_worker_host import (
    RegisteredAgent,
    automatic_qa_handoff,
    configured_agent,
    execute_payload,
    get_agent,
    health_payload,
    load_agents,
    select_registered_qa_agent,
    validate_worker,
)


class GenericWorkerHostTests(unittest.TestCase):
    def test_registry_loads_known_agents(self):
        agents = {agent.agent_id: agent for agent in load_agents()}
        self.assertIn("DEV-001", agents)
        self.assertIn("QA-001", agents)
        self.assertIn("OPS-001", agents)
        self.assertIn("AUTO-001", agents)

    def test_quinn_is_independent_qa_worker(self):
        quinn = get_agent("QA-001")
        self.assertEqual(quinn.name, "Quinn")
        self.assertTrue(quinn.worker_enabled)
        self.assertTrue(quinn.qa_eligible)
        self.assertIn("semantiq", quinn.projects)
        self.assertIn("rvsc", quinn.projects)

    def test_noah_is_authorized_for_rvsc(self):
        noah = get_agent("OPS-001")
        self.assertTrue(noah.worker_enabled)
        validate_worker(noah, "rvsc")

    def test_daniel_is_not_authorized_for_rvsc(self):
        daniel = get_agent("DEV-001")
        with self.assertRaises(ValueError):
            validate_worker(daniel, "rvsc")

    def test_command_agent_cannot_be_started_as_worker(self):
        max_agent = get_agent("CMD-001")
        with self.assertRaises(ValueError):
            validate_worker(max_agent)

    def test_configured_agent_uses_environment_identity(self):
        with patch.dict(os.environ, {"RVSC_WORKER_AGENT_ID": "OPS-001"}, clear=False):
            agent = configured_agent()
        self.assertEqual(agent.agent_id, "OPS-001")
        self.assertEqual(agent.name, "Noah")

    def test_health_reports_configured_qa_worker(self):
        with patch.dict(os.environ, {"RVSC_WORKER_AGENT_ID": "QA-001", "OPENAI_API_KEY": "qualification-placeholder"}, clear=False):
            payload = health_payload()
        self.assertEqual(payload["worker"], "QA-001")
        self.assertEqual(payload["name"], "Quinn")
        self.assertEqual(payload["service"], "rvsc-generic-worker")
        self.assertTrue(payload["worker_enabled"])
        self.assertTrue(payload["qa_eligible"])
        self.assertTrue(payload["ready"])
        self.assertTrue(payload["generic_qa"])
        self.assertFalse(payload["generic_engineering"])
        self.assertEqual(payload["execution_path"], "independent_qa")

    def test_registry_selects_independent_qa(self):
        noah = RegisteredAgent("OPS-001", "Noah", "DevOps", ("rvsc",), True, False)
        quinn = RegisteredAgent("QA-001", "Quinn", "QA", ("rvsc",), True, True)
        with patch("controller.generic_worker_host.load_agents", return_value=(noah, quinn)):
            selected = select_registered_qa_agent("OPS-001", "rvsc")
        self.assertEqual(selected.agent_id, "QA-001")

    def test_host_routes_qa_eligible_agent_to_qa_worker(self):
        quinn = RegisteredAgent("QA-001", "Quinn", "QA", ("rvsc",), True, True)
        payload = {"protocol": "rvsc.worker.v1", "mission": {"agent_id": "QA-001", "project": "rvsc", "wp_id": "QA-WP"}}
        expected = {"success": True, "run_id": "QA-RUN", "verdict": "QA_ACCEPTED"}
        with patch("controller.generic_worker_host.configured_agent", return_value=quinn), \
             patch("controller.generic_worker_host._set_runtime_state"), \
             patch("controller.generic_worker_host.execute_generic_qa", return_value=expected) as qa_worker, \
             patch("controller.generic_worker_host.execute_generic_engineering") as engineering_worker:
            result = execute_payload(payload)
        self.assertEqual(result, expected)
        qa_worker.assert_called_once()
        engineering_worker.assert_not_called()

    def test_host_preserves_generic_engineering_route_and_hands_off(self):
        noah = RegisteredAgent("OPS-001", "Noah", "DevOps & Runtime", ("rvsc",), True, False)
        payload = {"protocol": "rvsc.worker.v1", "mission": {"agent_id": "OPS-001", "project": "rvsc", "wp_id": "OPS-WP"}}
        engineering = {"success": True, "run_id": "OPS-RUN"}
        accepted = {**engineering, "success": True, "verdict": "QA_ACCEPTED"}
        with patch("controller.generic_worker_host.configured_agent", return_value=noah), \
             patch("controller.generic_worker_host._set_runtime_state"), \
             patch("controller.generic_worker_host.execute_generic_qa") as qa_worker, \
             patch("controller.generic_worker_host.execute_generic_engineering", return_value=engineering) as engineering_worker, \
             patch("controller.generic_worker_host.automatic_qa_handoff", return_value=accepted) as handoff:
            result = execute_payload(payload)
        self.assertEqual(result, accepted)
        engineering_worker.assert_called_once()
        handoff.assert_called_once_with(noah, payload["mission"], engineering)
        qa_worker.assert_not_called()

    def test_host_preserves_daniel_specific_route_and_hands_off(self):
        daniel = RegisteredAgent("DEV-001", "Daniel", "Development", ("semantiq",), True, False)
        payload = {"protocol": "rvsc.worker.v1", "mission": {"agent_id": "DEV-001", "project": "semantiq", "wp_id": "DEV-WP"}}
        engineering = {"success": True, "run_id": "DEV-RUN"}
        accepted = {**engineering, "success": True, "verdict": "QA_ACCEPTED"}
        with patch("controller.generic_worker_host.configured_agent", return_value=daniel), \
             patch("controller.generic_worker_host._set_runtime_state"), \
             patch("controller.generic_worker_host.daniel.execute_payload", return_value=engineering) as daniel_worker, \
             patch("controller.generic_worker_host.execute_generic_qa") as qa_worker, \
             patch("controller.generic_worker_host.execute_generic_engineering") as engineering_worker, \
             patch("controller.generic_worker_host.automatic_qa_handoff", return_value=accepted) as handoff:
            result = execute_payload(payload)
        self.assertEqual(result, accepted)
        daniel_worker.assert_called_once_with(payload)
        handoff.assert_called_once_with(daniel, payload["mission"], engineering)
        qa_worker.assert_not_called()
        engineering_worker.assert_not_called()

    def test_automatic_handoff_accepts_and_preserves_evidence(self):
        noah = RegisteredAgent("OPS-001", "Noah", "DevOps", ("rvsc",), True, False)
        quinn = RegisteredAgent("QA-001", "Quinn", "QA", ("rvsc",), True, True)
        mission = {
            "agent_id": "OPS-001", "project": "rvsc", "wp_id": "RVSC-026B",
            "work_branch": "rvsc/RVSC-026B-auto-qa-handoff",
            "allowed_paths": ["controller/generic_worker_host.py"],
            "validation_commands": [{"name": "tests", "argv": ["python", "-m", "unittest"]}],
        }
        engineering = {"success": True, "run_id": "ENG-RUN", "commit_sha": "0123456789abcdef", "pushed": True}
        qa = {"success": True, "verdict": "QA_ACCEPTED", "evidence": ["commit:0123456789abcdef", "tests:pass"]}
        with patch("controller.generic_worker_host.load_agents", return_value=(noah, quinn)), \
             patch("controller.generic_worker_host.dispatch_qa_payload", return_value=qa) as dispatch, \
             patch("controller.generic_worker_host._checkpoint"):
            result = automatic_qa_handoff(noah, mission, engineering)
        self.assertTrue(result["success"])
        self.assertTrue(result["qa_handoff"]["success"])
        self.assertEqual(result["verdict"], "QA_ACCEPTED")
        self.assertEqual(result["qa_evidence"], qa["evidence"])
        qa_mission = dispatch.call_args.args[0]["mission"]
        self.assertEqual(qa_mission["engineering_branch"], mission["work_branch"])
        self.assertEqual(qa_mission["engineering_commit_sha"], engineering["commit_sha"])
        self.assertEqual(qa_mission["allowed_paths"], mission["allowed_paths"])
        self.assertEqual(qa_mission["validation_commands"], mission["validation_commands"])

    def test_automatic_handoff_blocks_rejection(self):
        noah = RegisteredAgent("OPS-001", "Noah", "DevOps", ("rvsc",), True, False)
        quinn = RegisteredAgent("QA-001", "Quinn", "QA", ("rvsc",), True, True)
        mission = {"agent_id": "OPS-001", "project": "rvsc", "work_branch": "rvsc/test"}
        engineering = {"success": True, "commit_sha": "0123456789abcdef", "pushed": True}
        qa = {"success": True, "verdict": "QA_REJECTED", "evidence": ["regression:failed"]}
        with patch("controller.generic_worker_host.load_agents", return_value=(noah, quinn)), \
             patch("controller.generic_worker_host.dispatch_qa_payload", return_value=qa), \
             patch("controller.generic_worker_host._checkpoint"):
            result = automatic_qa_handoff(noah, mission, engineering)
        self.assertFalse(result["success"])
        self.assertEqual(result["verdict"], "QA_REJECTED")

    def test_automatic_handoff_fails_closed_without_candidate(self):
        noah = RegisteredAgent("OPS-001", "Noah", "DevOps", ("rvsc",), True, False)
        mission = {"agent_id": "OPS-001", "project": "rvsc", "work_branch": "rvsc/test"}
        engineering = {"success": True, "commit_sha": "0123456789abcdef", "pushed": True}
        with patch("controller.generic_worker_host.load_agents", return_value=(noah,)):
            result = automatic_qa_handoff(noah, mission, engineering)
        self.assertFalse(result["success"])
        self.assertIn("missing QA candidate", result["summary"])

    def test_automatic_handoff_fails_closed_rather_than_self_reviewing(self):
        noah = RegisteredAgent("OPS-001", "Noah", "DevOps", ("rvsc",), True, True)
        mission = {"agent_id": "OPS-001", "project": "rvsc", "work_branch": "rvsc/test"}
        engineering = {"success": True, "commit_sha": "0123456789abcdef", "pushed": True}
        with patch("controller.generic_worker_host.load_agents", return_value=(noah,)), \
             patch("controller.generic_worker_host.dispatch_qa_payload") as dispatch:
            result = automatic_qa_handoff(noah, mission, engineering)
        self.assertFalse(result["success"])
        self.assertIn("missing QA candidate", result["summary"])
        dispatch.assert_not_called()

    def test_automatic_handoff_fails_closed_on_dispatch_failure(self):
        noah = RegisteredAgent("OPS-001", "Noah", "DevOps", ("rvsc",), True, False)
        quinn = RegisteredAgent("QA-001", "Quinn", "QA", ("rvsc",), True, True)
        mission = {"agent_id": "OPS-001", "project": "rvsc", "work_branch": "rvsc/test"}
        engineering = {"success": True, "commit_sha": "0123456789abcdef", "pushed": True}
        with patch("controller.generic_worker_host.load_agents", return_value=(noah, quinn)), \
             patch("controller.generic_worker_host.dispatch_qa_payload", side_effect=RuntimeError("dispatch unavailable")), \
             patch("controller.generic_worker_host._checkpoint"):
            result = automatic_qa_handoff(noah, mission, engineering)
        self.assertFalse(result["success"])
        self.assertIn("dispatch unavailable", result["summary"])


if __name__ == "__main__":
    unittest.main()
