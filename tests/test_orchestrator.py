import unittest

from controller.orchestrator import Event, OrchestrationError, build_execution_plan, dispatch_order


CONFIG = {
    "triggers": [
        {
            "id": "wp_ready",
            "event": "work_package.status_changed",
            "when": {"to": "ready"},
            "route": "dispatch_work_package",
        },
        {
            "id": "qa_pass",
            "event": "qa.completed",
            "when": {"outcome": "pass"},
            "route": "prepare_merge",
        },
    ],
    "routes": {
        "dispatch_work_package": {
            "actions": ["validate_scope", "resolve_agent", "invoke_worker"]
        },
        "prepare_merge": {
            "actions": ["evaluate_merge_eligibility", "open_or_update_pull_request"]
        },
    },
}


class OrchestratorTests(unittest.TestCase):
    def test_ready_event_resolves_dispatch_path(self):
        plan = build_execution_plan(
            CONFIG,
            Event("work_package.status_changed", {"to": "ready"}),
        )
        self.assertEqual(plan.trigger_id, "wp_ready")
        self.assertEqual(plan.route, "dispatch_work_package")
        self.assertEqual(
            plan.actions,
            ("validate_scope", "resolve_agent", "invoke_worker"),
        )

    def test_qa_pass_resolves_merge_path(self):
        plan = build_execution_plan(CONFIG, Event("qa.completed", {"outcome": "pass"}))
        self.assertEqual(plan.route, "prepare_merge")

    def test_unknown_event_is_rejected(self):
        with self.assertRaises(OrchestrationError):
            build_execution_plan(CONFIG, Event("unknown.event", {}))

    def test_project_priority_is_deterministic(self):
        order = dispatch_order(
            {
                "moxie": {"priority": "P1"},
                "semantiq": {"priority": "P0"},
                "other": {"priority": "P2"},
            }
        )
        self.assertEqual(order, ("semantiq", "moxie", "other"))


if __name__ == "__main__":
    unittest.main()
