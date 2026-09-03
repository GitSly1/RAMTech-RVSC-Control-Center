import unittest

from controller.work_package_controller import QA_ACCEPTED, QAHandoffError, build_qa_mission, validate_qa_result, validate_scope


class WorkPackageControllerTests(unittest.TestCase):
    def mission(self, project="semantiq", repository="GitSly1/RAMTech-SEMANTIQ"):
        return {"agent_id": "DEV-001", "project": project, "repository": repository, "wp_id": "SEM-123", "work_branch": "rvsc/SEM-123", "allowed_paths": ["source.py"], "validation_commands": [{"name": "tests", "argv": ["python", "-m", "unittest"]}]}

    def result(self):
        return {"success": True, "run_id": "ENG-RUN", "commit_sha": "a" * 40, "work_branch": "rvsc/SEM-123", "pushed": True}

    def test_build_qa_mission_propagates_cross_project_target(self):
        qa = build_qa_mission(engineering_mission=self.mission(), engineering_result=self.result(), qa_agent_id="QA-001")
        self.assertEqual(qa["engineering_project"], "semantiq")
        self.assertEqual(qa["engineering_repository"], "GitSly1/RAMTech-SEMANTIQ")
        self.assertEqual(qa["project"], "semantiq")
        self.assertEqual(qa["repository"], "GitSly1/RAMTech-SEMANTIQ")
        self.assertEqual(qa["engineering_commit_sha"], "a" * 40)
        self.assertEqual(qa["reviewed_commit_sha"], "a" * 40)

    def test_rvsc_qa_context_remains_supported(self):
        mission = self.mission("rvsc", "GitSly1/RAMTech-RVSC-Control-Center")
        qa = build_qa_mission(engineering_mission=mission, engineering_result=self.result(), qa_agent_id="QA-001")
        self.assertEqual(qa["engineering_project"], "rvsc")
        self.assertEqual(qa["engineering_repository"], "GitSly1/RAMTech-RVSC-Control-Center")

    def test_build_qa_mission_requires_repository_context(self):
        mission = self.mission()
        mission.pop("repository")
        with self.assertRaisesRegex(QAHandoffError, "repository"):
            build_qa_mission(engineering_mission=mission, engineering_result=self.result(), qa_agent_id="QA-001")

    def test_build_qa_mission_requires_push_and_commit(self):
        with self.assertRaises(QAHandoffError):
            build_qa_mission(engineering_mission=self.mission(), engineering_result={"success": True}, qa_agent_id="QA-001")

    def test_implementer_cannot_review_own_work(self):
        with self.assertRaisesRegex(QAHandoffError, "implementer"):
            build_qa_mission(engineering_mission=self.mission(), engineering_result=self.result(), qa_agent_id="DEV-001")

    def test_validate_qa_result_preserves_evidence(self):
        verdict, evidence = validate_qa_result({"success": True, "verdict": QA_ACCEPTED, "evidence": ["tests:pass"]})
        self.assertEqual(verdict, QA_ACCEPTED)
        self.assertEqual(evidence, ("tests:pass",))

    def test_scope_authorization_remains_enforced(self):
        self.assertEqual(validate_scope(["source.py", "secrets/token"], ["source.py"], ["secrets/**"]), ["secrets/token"])


if __name__ == "__main__":
    unittest.main()
