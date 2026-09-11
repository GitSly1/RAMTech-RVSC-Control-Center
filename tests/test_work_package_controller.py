import unittest

from controller.work_package_controller import QA_ACCEPTED, QA_REJECTED, QAHandoffError, build_qa_mission, validate_qa_result, validate_scope


from controller.work_package_controller import _assess_required_contract_inputs
class WorkPackageControllerTests(unittest.TestCase):
    def test_required_contract_inputs_absent_preserves_legacy_compatibility(self):
        result = _assess_required_contract_inputs({})
        self.assertTrue(result["complete"])
        self.assertFalse(result["declared"])
        self.assertEqual(result["blockers"], [])

    def test_required_a4_contract_input_with_value_is_complete(self):
        result = _assess_required_contract_inputs({
            "required_contract_inputs": [
                {
                    "name": "approved_value",
                    "authority_class": "A4",
                    "required": True,
                    "value": 30,
                }
            ]
        })
        self.assertTrue(result["complete"])
        self.assertTrue(result["declared"])
        self.assertEqual(result["blockers"], [])

    def test_missing_required_a4_contract_input_is_structured_blocker(self):
        result = _assess_required_contract_inputs({
            "required_contract_inputs": [
                {
                    "name": "approved_value",
                    "authority_class": "A4",
                    "required": True,
                }
            ]
        })
        self.assertFalse(result["complete"])
        self.assertEqual(
            result["blockers"],
            [
                {
                    "type": "MISSING_REQUIRED_CONTRACT_INPUT",
                    "authority_class": "A4",
                    "name": "approved_value",
                }
            ],
        )

    def test_non_authoritative_values_cannot_substitute_for_missing_a4_value(self):
        mission = {
            "required_contract_inputs": [
                {
                    "name": "approved_value",
                    "authority_class": "A4",
                    "required": True,
                }
            ],
            "implementation_value": 30,
            "default_value": 30,
            "historical_value": 30,
        }

        result = _assess_required_contract_inputs(
            mission
        )

        self.assertFalse(
            result["complete"]
        )
        self.assertEqual(
            result["blockers"][0]["type"],
            "MISSING_REQUIRED_CONTRACT_INPUT",
        )

    def mission(self, project="semantiq", repository="GitSly1/RAMTech-SEMANTIQ"):
        return {"agent_id": "DEV-001", "project": project, "repository": repository, "wp_id": "SEM-123", "work_branch": "rvsc/SEM-123", "allowed_paths": ["source.py"], "validation_commands": [{"name": "tests", "argv": ["python", "-m", "unittest"]}]}

    def result(self):
        return {
            "success": True,
            "run_id": "ENG-RUN",
            "commit_sha": "a" * 40,
            "work_branch": "rvsc/SEM-123",
            "pushed": True,
            "evidence": [
                "semantic_acceptance:criterion:1:validation:tests",
                "semantic_acceptance:criteria_verified:1",
                "semantic_acceptance:passed",
            ],
        }

    def test_build_qa_mission_propagates_cross_project_target(self):
        projects = (
            ("semantiq", "GitSly1/RAMTech-SEMANTIQ"),
            ("moxie", "GitSly1/RAMTech-MOXIE"),
            ("rvsc", "GitSly1/RAMTech-RVSC-Control-Center"),
        )
        for project, repository in projects:
            with self.subTest(project=project):
                qa = build_qa_mission(engineering_mission=self.mission(project, repository), engineering_result=self.result(), qa_agent_id="QA-001")
                self.assertEqual(qa["engineering_project"], project)
                self.assertEqual(qa["engineering_repository"], repository)
                self.assertEqual(qa["project"], project)
                self.assertEqual(qa["repository"], repository)
                self.assertEqual(qa["engineering_commit_sha"], "a" * 40)
                self.assertEqual(qa["reviewed_commit_sha"], "a" * 40)

    def test_build_qa_mission_propagates_engineering_evidence(self):
        qa = build_qa_mission(
            engineering_mission=self.mission(),
            engineering_result=self.result(),
            qa_agent_id="QA-001",
        )

        self.assertEqual(
            qa["engineering_evidence"],
            self.result()["evidence"],
        )

    def test_build_qa_mission_assigns_independent_qa_run_id(self):
        qa = build_qa_mission(engineering_mission=self.mission(), engineering_result=self.result(), qa_agent_id="QA-001")
        self.assertEqual(qa["engineering_run_id"], "ENG-RUN")
        self.assertTrue(qa["run_id"].startswith("RVSC-QA-001-"))
        self.assertNotEqual(qa["run_id"], qa["engineering_run_id"])
        self.assertEqual(qa["agent_id"], "QA-001")

    def test_build_qa_mission_replaces_stale_engineering_mission_run_id(self):
        mission = self.mission()
        mission["run_id"] = "STALE-ENGINEERING-MISSION-RUN"
        qa = build_qa_mission(engineering_mission=mission, engineering_result=self.result(), qa_agent_id="QA-001")
        self.assertNotEqual(qa["run_id"], "STALE-ENGINEERING-MISSION-RUN")
        self.assertEqual(qa["engineering_run_id"], "ENG-RUN")

    def test_build_qa_mission_requires_engineering_run_id(self):
        result = self.result()
        result.pop("run_id")
        with self.assertRaisesRegex(QAHandoffError, "run_id"):
            build_qa_mission(engineering_mission=self.mission(), engineering_result=result, qa_agent_id="QA-001")

    def test_build_qa_mission_requires_repository_context(self):
        mission = self.mission()
        mission.pop("repository")
        with self.assertRaisesRegex(QAHandoffError, "repository"):
            build_qa_mission(engineering_mission=mission, engineering_result=self.result(), qa_agent_id="QA-001")

    def test_build_qa_mission_requires_push_and_commit(self):
        with self.assertRaises(QAHandoffError):
            build_qa_mission(engineering_mission=self.mission(), engineering_result={"success": True}, qa_agent_id="QA-001")

    def test_build_qa_mission_rejects_mismatched_branch_evidence(self):
        result = self.result()
        result["work_branch"] = "rvsc/OTHER"
        with self.assertRaisesRegex(QAHandoffError, "does not match"):
            build_qa_mission(engineering_mission=self.mission(), engineering_result=result, qa_agent_id="QA-001")

    def test_implementer_cannot_review_own_work(self):
        with self.assertRaisesRegex(QAHandoffError, "implementer"):
            build_qa_mission(engineering_mission=self.mission(), engineering_result=self.result(), qa_agent_id="DEV-001")

    def test_validate_qa_result_preserves_acceptance_evidence(self):
        verdict, evidence = validate_qa_result({"success": True, "verdict": QA_ACCEPTED, "evidence": ["tests:pass"]})
        self.assertEqual(verdict, QA_ACCEPTED)
        self.assertEqual(evidence, ("tests:pass",))

    def test_validate_qa_result_preserves_rejection(self):
        verdict, evidence = validate_qa_result({"success": True, "verdict": QA_REJECTED, "evidence": ["tests:failed"]})
        self.assertEqual(verdict, QA_REJECTED)
        self.assertEqual(evidence, ("tests:failed",))

    def test_validate_qa_result_classifies_malformed_response(self):
        malformed = {"success": True, "evidence": ["tests:pass"]}
        with self.assertRaises(QAHandoffError) as raised:
            validate_qa_result(malformed)
        self.assertEqual(raised.exception.category, "malformed_qa_response")
        self.assertEqual(raised.exception.response, malformed)

    def test_validate_qa_result_classifies_structured_worker_failure(self):
        failed = {"success": False, "verdict": QA_REJECTED, "evidence": ["worker:failed"], "summary": "runtime failure"}
        with self.assertRaises(QAHandoffError) as raised:
            validate_qa_result(failed)
        self.assertEqual(raised.exception.category, "qa_worker_failure")
        self.assertEqual(raised.exception.response, failed)

    def test_scope_authorization_remains_enforced(self):
        self.assertEqual(validate_scope(["source.py", "secrets/token"], ["source.py"], ["secrets/**"]), ["secrets/token"])


if __name__ == "__main__":
    unittest.main()
