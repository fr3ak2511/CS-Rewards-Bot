import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowBranchSafetyTests(unittest.TestCase):
    def test_claimer_is_guarded_at_preflight_and_job_level(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        self.assertIn("selectedBranch !== 'prod'", workflow)
        self.assertIn("github.ref_name == 'prod'", workflow)
        self.assertIn("SELECTED_BRANCH: ${{ github.ref_name }}", workflow)
        self.assertIn("selected_branch != 'prod'", workflow)

    def test_regression_workflow_covers_two_permanent_and_short_lived_branches(self):
        workflow = (ROOT / ".github" / "workflows" / "event-tests.yml").read_text(encoding="utf-8")
        for branch in ("prod", "dev", "'feature/**'", "'fix/**'", "'chore/**'"):
            self.assertIn(branch, workflow)
        self.assertNotIn("      - test\n", workflow)

    def test_recovery_workflow_requires_full_claim_scope_and_no_legacy_fallback(self):
        workflow = (ROOT / ".github" / "workflows" / "recovery.yml").read_text(encoding="utf-8")
        self.assertIn("last.retry_candidate_scope !== 'full_claim_only'", workflow)
        self.assertIn("retry candidate count does not match unique candidate IDs", workflow)
        self.assertNotIn("last.failed_player_ids", workflow)

    def test_regression_path_filters_cover_promotion_tracker_and_recovery_workflow(self):
        workflow = (ROOT / ".github" / "workflows" / "event-tests.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("'promotion_followups.py'"), 2)
        self.assertEqual(workflow.count("'.github/workflows/recovery.yml'"), 2)

    def test_schedule_recovery_requires_scoped_count_verified_full_claim_ids(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        self.assertIn("def full_claim_retry_ids(last_run):", workflow)
        self.assertIn("last_run.get('retry_candidate_scope') != 'full_claim_only'", workflow)
        self.assertIn("last_run.get('retry_player_count', -1)", workflow)
        self.assertEqual(workflow.count("retry_ids = full_claim_retry_ids(last)"), 2)

    def test_schedule_recovery_never_uses_legacy_failed_player_fallback(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        self.assertNotIn("failed_player_ids", workflow)

    def test_schedule_and_recovery_workflows_both_enforce_full_claim_scope(self):
        schedule = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        recovery = (ROOT / ".github" / "workflows" / "recovery.yml").read_text(encoding="utf-8")
        self.assertIn("last_run.get('retry_candidate_scope') != 'full_claim_only'", schedule)
        self.assertIn("last.retry_candidate_scope !== 'full_claim_only'", recovery)


if __name__ == "__main__":
    unittest.main()
