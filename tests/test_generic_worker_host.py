from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from controller.generic_worker_host import (
    configured_agent,
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

    def test_health_reports_configured_worker(self):
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


if __name__ == "__main__":
    unittest.main()
