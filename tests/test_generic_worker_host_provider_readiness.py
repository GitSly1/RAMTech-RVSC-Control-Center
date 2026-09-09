import unittest
from unittest.mock import patch

from controller import generic_worker_host as host


class GenericWorkerHostProviderReadinessTests(unittest.TestCase):
    def health_for(self, agent_id: str, environment: dict[str, str]):
        env = {
            "RVSC_WORKER_AGENT_ID": agent_id,
            **environment,
        }

        with patch.dict(host.os.environ, env, clear=True):
            with patch.dict(
                host._RUNTIME_STATE,
                {
                    "active_mission": None,
                    "recovery_required": False,
                },
                clear=False,
            ):
                return host.health_payload()

    def test_default_ollama_engineering_is_ready_without_openai_key(self):
        health = self.health_for("DEV-001", {})

        self.assertEqual(health["execution_path"], "generic_engineering")
        self.assertTrue(health["credential_ready"])
        self.assertTrue(health["ready"])

    def test_explicit_ollama_engineering_is_ready_without_openai_key(self):
        health = self.health_for(
            "DEV-001",
            {"RVSC_AI_PROVIDER": "ollama"},
        )

        self.assertTrue(health["credential_ready"])
        self.assertTrue(health["ready"])

    def test_openai_engineering_requires_api_key(self):
        health = self.health_for(
            "DEV-001",
            {"RVSC_AI_PROVIDER": "openai"},
        )

        self.assertFalse(health["credential_ready"])
        self.assertFalse(health["ready"])

    def test_openai_engineering_is_ready_with_api_key(self):
        health = self.health_for(
            "DEV-001",
            {
                "RVSC_AI_PROVIDER": "openai",
                "OPENAI_API_KEY": "test-key",
            },
        )

        self.assertTrue(health["credential_ready"])
        self.assertTrue(health["ready"])

    def test_unsupported_engineering_provider_fails_closed(self):
        health = self.health_for(
            "DEV-001",
            {"RVSC_AI_PROVIDER": "unsupported-provider"},
        )

        self.assertFalse(health["credential_ready"])
        self.assertFalse(health["ready"])

    def test_independent_qa_requires_no_model_provider_credential(self):
        health = self.health_for(
            "QA-001",
            {"RVSC_AI_PROVIDER": "unsupported-provider"},
        )

        self.assertEqual(health["execution_path"], "independent_qa")
        self.assertTrue(health["credential_ready"])
        self.assertTrue(health["ready"])


if __name__ == "__main__":
    unittest.main()
