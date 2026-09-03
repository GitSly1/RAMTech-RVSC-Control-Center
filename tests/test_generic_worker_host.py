from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from controller.generic_worker_host import (
    RegisteredAgent,
    configured_agent,
    execute_payload,
    get_agent,
    health_payload,
    load_agents,
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
        with patch.dict(
            os.environ,
            {"RVSC_WORKER_AGENT_ID": "OPS-001"},
            clear=False,
        ):
            agent = configured_agent()

        self.assertEqual(agent.agent_id, "OPS-001")
        self.assertEqual(agent.name, "Noah")

    def test_health_reports_configured_qa_worker(self):
        with patch.dict(
            os.environ,
            {
                "RVSC_WORKER_AGENT_ID": "QA-001",
                "OPENAI_API_KEY": "qualification-placeholder",
            },
            clear=False,
        ):
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

    def test_host_routes_qa_eligible_agent_to_qa_worker(self):
        quinn = RegisteredAgent("QA-001", "Quinn", "QA", ("rvsc",), True, True)
        payload = {
            "protocol": "rvsc.worker.v1",
            "mission": {"agent_id": "QA-001", "project": "rvsc", "wp_id": "QA-WP"},
        }
        expected = {"success": True, "run_id": "QA-RUN", "verdict": "QA_ACCEPTED"}

        with patch("controller.generic_worker_host.configured_agent", return_value=quinn), \
             patch("controller.generic_worker_host._set_runtime_state"), \
             patch("controller.generic_worker_host.execute_generic_qa", return_value=expected) as qa_worker, \
             patch("controller.generic_worker_host.execute_generic_engineering") as engineering_worker:
            result = execute_payload(payload)

        self.assertEqual(result, expected)
        qa_worker.assert_called_once()
        engineering_worker.assert_not_called()

    def test_host_preserves_generic_engineering_route(self):
        noah = RegisteredAgent("OPS-001", "Noah", "DevOps & Runtime", ("rvsc",), True, False)
        payload = {
            "protocol": "rvsc.worker.v1",
            "mission": {"agent_id": "OPS-001", "project": "rvsc", "wp_id": "OPS-WP"},
        }
        expected = {"success": True, "run_id": "OPS-RUN"}

        with patch("controller.generic_worker_host.configured_agent", return_value=noah), \
             patch("controller.generic_worker_host._set_runtime_state"), \
             patch("controller.generic_worker_host.execute_generic_qa") as qa_worker, \
             patch("controller.generic_worker_host.execute_generic_engineering", return_value=expected) as engineering_worker:
            result = execute_payload(payload)

        self.assertEqual(result, expected)
        engineering_worker.assert_called_once()
        called = engineering_worker.call_args.kwargs
        self.assertEqual(called["agent_id"], "OPS-001")
        self.assertEqual(called["agent_name"], "Noah")
        qa_worker.assert_not_called()

    def test_host_preserves_daniel_specific_route(self):
        daniel = RegisteredAgent("DEV-001", "Daniel", "Development", ("semantiq",), True, False)
        payload = {
            "protocol": "rvsc.worker.v1",
            "mission": {"agent_id": "DEV-001", "project": "semantiq", "wp_id": "DEV-WP"},
        }
        expected = {"success": True, "run_id": "DEV-RUN"}

        with patch("controller.generic_worker_host.configured_agent", return_value=daniel), \
             patch("controller.generic_worker_host._set_runtime_state"), \
             patch("controller.generic_worker_host.daniel.execute_payload", return_value=expected) as daniel_worker, \
             patch("controller.generic_worker_host.execute_generic_qa") as qa_worker, \
             patch("controller.generic_worker_host.execute_generic_engineering") as engineering_worker:
            result = execute_payload(payload)

        self.assertEqual(result, expected)
        daniel_worker.assert_called_once_with(payload)
        qa_worker.assert_not_called()
        engineering_worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
