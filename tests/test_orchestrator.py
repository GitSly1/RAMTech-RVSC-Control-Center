import tempfile
import unittest
from pathlib import Path

from controller.orchestrator import Mission, MissionState, MissionStore, OrchestrationError, WorkerState, dispatch_next, select_dispatch, validate_mission_contract


class MissionStoreTests(unittest.TestCase):
    def contract(self, **changes):
        value = {
            "wp_id": "WP-1", "project": "rvsc", "objective": "Deliver work",
            "repository": "GitSly1/repo", "work_branch": "feature", "base_branch": "main",
            "agent_id": "DEV-001", "acceptance_criteria": ["tests pass"],
            "allowed_paths": ["controller/a.py"],
            "validation_commands": [{"name": "tests", "argv": ["python", "-m", "unittest"]}],
        }
        value.update(changes)

        if "requires_semantic_acceptance" not in changes:
            value["requires_semantic_acceptance"] = True

        if (
            value.get("requires_semantic_acceptance") is True
            and "acceptance_checks" not in changes
        ):
            commands = value.get("validation_commands", [])
            if commands:
                validation_name = str(commands[0].get("name", "")).strip()
                value["acceptance_checks"] = [
                    {
                        "criterion_index": index,
                        "type": "validation_passed",
                        "name": validation_name,
                    }
                    for index, _ in enumerate(
                        value.get("acceptance_criteria", []),
                        start=1,
                    )
                ]

        return value

    def completed(self, store, mission_id, implementer="DEV-001"):
        store.transition(mission_id, "assigned", worker_id=implementer)
        store.transition(mission_id, "running", worker_id=implementer)
        store.transition(mission_id, "completed", worker_id=implementer)

    def test_load_or_create_and_progress_are_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missions.json"
            store = MissionStore.load_or_create(path)
            store.add(Mission("M-1", "rvsc"))
            store.record_progress("M-1", timestamp=10, checkpoint="build", evidence=["commit:a"])
            store.record_progress("M-1", timestamp=20, checkpoint="build", evidence=["commit:a"])
            self.assertEqual(MissionStore.load(path).get("M-1").material_progress_at, 10.0)

    def test_independent_qa_and_dependency_dispatch_remain_intact(self):
        store = MissionStore()
        store.add(Mission("origin", "rvsc"))
        store.add(Mission("dependent", "rvsc", dependencies=("origin",), priority=1))
        self.completed(store, "origin")
        with self.assertRaises(OrchestrationError):
            store.transition("origin", "qa_pending", worker_id="DEV-001")
        store.process_qa_outcome("origin", "QA_ACCEPTED", qa_worker="QA-001", evidence={"commit": "abc"})
        self.assertTrue(store.readiness("dependent")[0])

    def test_blocked_mission_can_be_superseded_with_provenance(self):
        store = MissionStore()
        mission = store.add_contract(
            self.contract(wp_id="SUPERSEDE-1"),
            supported_projects=("rvsc",),
        )
        store.transition(
            mission.mission_id,
            MissionState.ASSIGNED,
            worker_id="DEV-001",
        )
        store.transition(
            mission.mission_id,
            MissionState.RUNNING,
            worker_id="DEV-001",
        )
        store.transition(
            mission.mission_id,
            MissionState.BLOCKED,
            worker_id="DEV-001",
            reason="historical execution failure",
        )

        store.transition(
            mission.mission_id,
            MissionState.SUPERSEDED,
            evidence={
                "reason": "objective independently satisfied",
                "superseded_by_revision": "abc123",
                "superseded_by_evidence": {
                    "qualification": "PASS",
                    "controller_revision": "abc123",
                },
            },
        )

        durable = store.get(mission.mission_id)

        self.assertEqual(
            durable.state,
            MissionState.SUPERSEDED,
        )
        self.assertEqual(
            durable.block_reason,
            "historical execution failure",
        )
        self.assertEqual(
            durable.metadata["supersession"]["superseded_by_revision"],
            "abc123",
        )
        self.assertEqual(
            durable.metadata["supersession"]["reason"],
            "objective independently satisfied",
        )

    def test_superseded_transition_fails_closed_without_provenance(self):
        invalid_evidence = (
            {},
            {"reason": "resolved"},
            {
                "reason": "resolved",
                "superseded_by_revision": "abc123",
            },
            {
                "reason": "resolved",
                "superseded_by_revision": "abc123",
                "superseded_by_evidence": {},
            },
        )

        for index, evidence in enumerate(invalid_evidence):
            with self.subTest(index=index):
                store = MissionStore()
                mission = store.add_contract(
                    self.contract(
                        wp_id="SUPERSEDE-BAD-%d" % index
                    ),
                    supported_projects=("rvsc",),
                )
                store.transition(
                    mission.mission_id,
                    MissionState.ASSIGNED,
                    worker_id="DEV-001",
                )
                store.transition(
                    mission.mission_id,
                    MissionState.RUNNING,
                    worker_id="DEV-001",
                )
                store.transition(
                    mission.mission_id,
                    MissionState.BLOCKED,
                    worker_id="DEV-001",
                    reason="historical failure",
                )

                with self.assertRaises(OrchestrationError):
                    store.transition(
                        mission.mission_id,
                        MissionState.SUPERSEDED,
                        evidence=evidence,
                    )

                self.assertEqual(
                    store.get(mission.mission_id).state,
                    MissionState.BLOCKED,
                )

    def test_superseded_is_terminal_and_does_not_satisfy_dependency(self):
        store = MissionStore()

        origin = store.add_contract(
            self.contract(wp_id="SUPERSEDE-ORIGIN"),
            supported_projects=("rvsc",),
        )

        store.add_contract(
            self.contract(
                wp_id="SUPERSEDE-DEPENDENT",
                dependencies=["SUPERSEDE-ORIGIN"],
            ),
            supported_projects=("rvsc",),
        )

        store.transition(
            origin.mission_id,
            MissionState.ASSIGNED,
            worker_id="DEV-001",
        )
        store.transition(
            origin.mission_id,
            MissionState.RUNNING,
            worker_id="DEV-001",
        )
        store.transition(
            origin.mission_id,
            MissionState.BLOCKED,
            worker_id="DEV-001",
            reason="historical failure",
        )
        store.transition(
            origin.mission_id,
            MissionState.SUPERSEDED,
            evidence={
                "reason": "objective independently satisfied",
                "superseded_by_revision": "abc123",
                "superseded_by_evidence": {
                    "qualification": "PASS"
                },
            },
        )

        ready, blocker = store.readiness(
            "SUPERSEDE-DEPENDENT"
        )

        self.assertFalse(ready)
        self.assertIn(
            "SUPERSEDE-ORIGIN:superseded",
            blocker,
        )

        for target in (
            MissionState.QUEUED,
            MissionState.BLOCKED,
            MissionState.ACCEPTED,
        ):
            with self.subTest(target=target):
                with self.assertRaises(OrchestrationError):
                    store.transition(
                        origin.mission_id,
                        target,
                    )

    def test_non_blocked_mission_cannot_be_superseded(self):
        store = MissionStore()

        mission = store.add_contract(
            self.contract(wp_id="SUPERSEDE-QUEUED"),
            supported_projects=("rvsc",),
        )

        with self.assertRaises(OrchestrationError):
            store.transition(
                mission.mission_id,
                MissionState.SUPERSEDED,
                evidence={
                    "reason": "invalid shortcut",
                    "superseded_by_revision": "abc123",
                    "superseded_by_evidence": {
                        "qualification": "PASS"
                    },
                },
            )

        self.assertEqual(
            store.get(mission.mission_id).state,
            MissionState.QUEUED,
        )

    def test_dispatch_is_deterministic_and_honors_contract_agent(self):
        store = MissionStore()
        store.add_contract(self.contract(wp_id="later", priority=2), supported_projects=("rvsc",))
        store.add_contract(self.contract(wp_id="first", priority=1), supported_projects=("rvsc",))
        workers = [WorkerState("DEV-002", ("rvsc",)), WorkerState("DEV-001", ("rvsc",))]
        decision = select_dispatch(store, workers)
        self.assertEqual((decision.mission_id, decision.worker_id), ("first", "DEV-001"))
        self.assertEqual(dispatch_next(store, workers).outcome, "dispatch")

    def test_contract_ingestion_and_dispatch_contract_validate_identity(self):
        store = MissionStore()
        expected = validate_mission_contract(self.contract(), ("rvsc",))
        mission = store.add_contract(self.contract(), supported_projects=("rvsc",))
        self.assertEqual(mission.implementer, "DEV-001")
        self.assertIsNone(mission.assigned_worker)
        self.assertEqual(store.dispatch_contract("WP-1", "DEV-001", supported_projects=("rvsc",)), expected)
        for field, value in (("wp_id", "OTHER"), ("project", "moxie"), ("agent_id", "DEV-002")):
            with self.subTest(field=field):
                store.get("WP-1").metadata["contract"][field] = value
                with self.assertRaises(OrchestrationError):
                    store.dispatch_contract("WP-1", "DEV-001", supported_projects=("rvsc", "moxie"))
                store.get("WP-1").metadata["contract"] = expected.copy()

    def test_missing_and_malformed_stored_contracts_fail_closed(self):
        store = MissionStore()
        store.add(Mission("raw", "rvsc"))
        with self.assertRaises(OrchestrationError):
            store.dispatch_contract("raw", "DEV-001", supported_projects=("rvsc",))
        mission = store.get("raw")
        mission.metadata["contract"] = {"wp_id": "raw"}
        with self.assertRaises(OrchestrationError):
            store.dispatch_contract("raw", "DEV-001", supported_projects=("rvsc",))

    def test_rejection_creates_valid_dispatchable_corrective_contract(self):
        store = MissionStore()
        original = store.add_contract(self.contract(), supported_projects=("rvsc",))
        self.completed(store, original.mission_id)
        result = store.process_qa_outcome(original.mission_id, "QA_REJECTED", qa_worker="QA-001", evidence={"reason": "tests failed", "commit": "abc"})
        corrective = store.get(result.corrective_mission_id)
        self.assertEqual(corrective.implementer, "DEV-001")
        self.assertIsNone(corrective.assigned_worker)
        contract = store.dispatch_contract(corrective.mission_id, "DEV-001", supported_projects=("rvsc",))
        self.assertEqual(contract["wp_id"], corrective.mission_id)
        self.assertEqual(contract["repository"], self.contract()["repository"])
        self.assertEqual(contract["work_branch"], self.contract()["work_branch"])
        self.assertEqual(contract["allowed_paths"], self.contract()["allowed_paths"])
        self.assertEqual(contract["acceptance_criteria"], self.contract()["acceptance_criteria"])
        self.assertEqual(contract["dependencies"], [])
        self.assertIn("Original objective", contract["objective"])
        self.assertIn("QA-001", corrective.metadata["excluded_worker_ids"])
        self.assertTrue(corrective.metadata["requires_independent_qa"])

    def test_contract_rejects_more_than_two_validation_commands(self):
        contract = self.contract()
        contract["validation_commands"] = [
            {"name": "targeted", "argv": ["python", "-m", "unittest", "tests.test_runtime_supervisor"]},
            {"name": "full", "argv": ["python", "-m", "unittest", "discover"]},
            {"name": "extra", "argv": ["python", "-c", "print('extra')"]},
        ]

        with self.assertRaisesRegex(
            OrchestrationError,
            "at most two validation commands",
        ):
            MissionStore().add_contract(
                contract,
                supported_projects=("rvsc",),
            )

    def test_context_paths_are_validated_and_preserved(self):
        contract = validate_mission_contract(
            self.contract(
                context_paths=[
                    "controller/runtime_supervisor.py",
                    "controller/worker_runtime.py",
                ]
            ),
            ("rvsc",),
        )

        self.assertEqual(
            contract["context_paths"],
            [
                "controller/runtime_supervisor.py",
                "controller/worker_runtime.py",
            ],
        )
        self.assertEqual(
            contract["allowed_paths"],
            ["controller/a.py"],
        )

    def test_unsafe_and_malformed_context_paths_fail_closed(self):
        with self.assertRaises(OrchestrationError):
            validate_mission_contract(
                self.contract(context_paths=["../secret"]),
                ("rvsc",),
            )

        with self.assertRaises(OrchestrationError):
            validate_mission_contract(
                self.contract(context_paths="controller/runtime_supervisor.py"),
                ("rvsc",),
            )

        with self.assertRaises(OrchestrationError):
            validate_mission_contract(
                self.contract(context_paths=[""]),
                ("rvsc",),
            )

    def test_malformed_unsupported_and_unsafe_contracts_fail_closed(self):
        with self.assertRaises(OrchestrationError):
            validate_mission_contract(self.contract(project="unknown"), ("rvsc",))
        with self.assertRaises(OrchestrationError):
            validate_mission_contract(self.contract(allowed_paths=["../secret"]), ("rvsc",))
        with self.assertRaises(OrchestrationError):
            validate_mission_contract(self.contract(validation_commands=[]), ("rvsc",))
        with self.assertRaises(OrchestrationError):
            validate_mission_contract(self.contract(mission_id="OTHER"), ("rvsc",))

    def test_strict_semantic_contract_requires_checks(self):
        contract = self.contract()
        contract["requires_semantic_acceptance"] = True
        contract.pop("acceptance_checks", None)

        with self.assertRaisesRegex(
            OrchestrationError,
            "strict semantic mission requires acceptance_checks",
        ):
            validate_mission_contract(contract, ("rvsc",))

    def test_strict_semantic_contract_requires_full_criterion_coverage(self):
        contract = self.contract()
        contract["requires_semantic_acceptance"] = True
        contract["acceptance_criteria"] = [
            "targeted validation passes",
            "behavior materially changes",
        ]
        contract["acceptance_checks"] = [
            {
                "criterion_index": 1,
                "type": "validation_passed",
                "name": contract["validation_commands"][0]["name"],
            }
        ]

        with self.assertRaisesRegex(
            OrchestrationError,
            "lack machine-verifiable checks: 2",
        ):
            validate_mission_contract(contract, ("rvsc",))

    def test_strict_semantic_contract_is_preserved_at_admission(self):
        contract = self.contract()
        contract["requires_semantic_acceptance"] = True
        contract["acceptance_checks"] = [
            {
                "criterion_index": index,
                "type": "validation_passed",
                "name": contract["validation_commands"][0]["name"],
            }
            for index, _ in enumerate(
                contract["acceptance_criteria"],
                start=1,
            )
        ]

        validated = validate_mission_contract(contract, ("rvsc",))

        self.assertIs(validated["requires_semantic_acceptance"], True)
        self.assertEqual(
            len(validated["acceptance_checks"]),
            len(validated["acceptance_criteria"]),
        )

    def test_acceptance_checks_cannot_silently_bypass_strict_mode(self):
        contract = self.contract(
            requires_semantic_acceptance=False,
        )
        contract["acceptance_checks"] = [
            {
                "criterion_index": 1,
                "type": "validation_passed",
                "name": contract["validation_commands"][0]["name"],
            }
        ]

        with self.assertRaisesRegex(
            OrchestrationError,
            "acceptance_checks require requires_semantic_acceptance=true",
        ):
            validate_mission_contract(contract, ("rvsc",))

    def test_strict_semantic_contract_rejects_unknown_validation_reference(self):
        contract = self.contract()
        contract["requires_semantic_acceptance"] = True
        contract["acceptance_checks"] = [
            {
                "criterion_index": index,
                "type": "validation_passed",
                "name": "NOT_DECLARED",
            }
            for index, _ in enumerate(
                contract["acceptance_criteria"],
                start=1,
            )
        ]

        with self.assertRaisesRegex(
            OrchestrationError,
            "references unknown validation command",
        ):
            validate_mission_contract(contract, ("rvsc",))

    def test_strict_semantic_contract_rejects_unauthorized_path_reference(self):
        contract = self.contract()
        contract["requires_semantic_acceptance"] = True
        contract["acceptance_checks"] = [
            {
                "criterion_index": index,
                "type": "path_changed",
                "path": "controller/not-authorized.py",
            }
            for index, _ in enumerate(
                contract["acceptance_criteria"],
                start=1,
            )
        ]

        with self.assertRaisesRegex(
            OrchestrationError,
            "references unauthorized path",
        ):
            validate_mission_contract(contract, ("rvsc",))

    def test_new_contract_admission_requires_strict_semantic_acceptance(self):
        store = MissionStore()
        contract = self.contract(
            requires_semantic_acceptance=False,
        )
        contract.pop("acceptance_checks", None)

        with self.assertRaisesRegex(
            OrchestrationError,
            "new engineering mission admission requires "
            "requires_semantic_acceptance=true",
        ):
            store.add_contract(
                contract,
                supported_projects=("rvsc",),
            )

    def test_new_strict_contract_is_admitted_with_machine_checks(self):
        store = MissionStore()
        contract = self.contract()
        contract["requires_semantic_acceptance"] = True
        contract["acceptance_checks"] = [
            {
                "criterion_index": index,
                "type": "validation_passed",
                "name": contract["validation_commands"][0]["name"],
            }
            for index, _ in enumerate(
                contract["acceptance_criteria"],
                start=1,
            )
        ]

        mission = store.add_contract(
            contract,
            supported_projects=("rvsc",),
        )

        stored = mission.metadata["contract"]

        self.assertIs(
            stored["requires_semantic_acceptance"],
            True,
        )
        self.assertEqual(
            len(stored["acceptance_checks"]),
            len(stored["acceptance_criteria"]),
        )

    def test_legacy_stored_contract_validation_remains_readable(self):
        contract = self.contract(
            requires_semantic_acceptance=False,
        )
        contract.pop("acceptance_checks", None)

        validated = validate_mission_contract(
            contract,
            ("rvsc",),
        )

        self.assertIs(
            validated["requires_semantic_acceptance"],
            False,
        )
        self.assertNotIn(
            "acceptance_checks",
            validated,
        )


if __name__ == "__main__":
    unittest.main()
