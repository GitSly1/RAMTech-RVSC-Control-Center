import unittest

from controller.adapters import AdapterRegistry, DryRunAdapter, WorkerRequest


class WorkerAdapterTests(unittest.TestCase):
    def setUp(self):
        self.request = WorkerRequest(
            agent_id="AUTO-001",
            wp_id="RVSC-017",
            project="rvsc",
            repository="GitSly1/RAMTech-RVSC-Control-Center",
            base_branch="main",
            work_branch="rvsc/017-autonomous-worker-runtime",
            objective="Qualify the worker adapter contract",
            allowed_paths=("controller/**", "tests/**"),
            acceptance_criteria=("adapter resolves", "evidence is emitted"),
        )

    def test_registry_resolves_registered_adapter(self):
        adapter = DryRunAdapter()
        registry = AdapterRegistry({adapter.name: adapter})
        self.assertIs(registry.resolve("dry_run"), adapter)

    def test_registry_rejects_unknown_adapter(self):
        registry = AdapterRegistry()
        with self.assertRaisesRegex(ValueError, "worker adapter not configured"):
            registry.resolve("missing")

    def test_register_rejects_unnamed_adapter(self):
        class UnnamedAdapter:
            name = ""

        registry = AdapterRegistry()
        with self.assertRaisesRegex(ValueError, "adapter requires a name"):
            registry.register(UnnamedAdapter())

    def test_dry_run_emits_bounded_evidence(self):
        result = DryRunAdapter().execute(self.request)
        self.assertTrue(result.success)
        self.assertFalse(result.retryable)
        self.assertIn("branch:rvsc/017-autonomous-worker-runtime", result.evidence)
        self.assertIn("adapter:dry_run", result.evidence)
        self.assertIn("AUTO-001", result.summary)
        self.assertIn("RVSC-017", result.summary)


if __name__ == "__main__":
    unittest.main()
