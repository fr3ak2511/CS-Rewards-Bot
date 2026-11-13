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

    def test_recovery_workflow_uses_executable_immediate_recovery_policy(self):
        workflow = (ROOT / ".github" / "workflows" / "recovery.yml").read_text(encoding="utf-8")
        self.assertIn("evaluateImmediateRecovery", workflow)
        self.assertIn("require(policyPath)", workflow)
        self.assertNotIn("last.failed_player_ids", workflow)

    def test_regression_path_filters_cover_promotion_tracker_and_recovery_workflow(self):
        workflow = (ROOT / ".github" / "workflows" / "event-tests.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("'promotion_followups.py'"), 2)
        self.assertEqual(workflow.count("'.github/workflows/recovery.yml'"), 2)
        self.assertEqual(workflow.count("'recovery_policy.py'"), 2)
        self.assertEqual(workflow.count("'.github/scripts/recovery_policy.js'"), 2)

    def test_schedule_recovery_uses_shared_scoped_count_validator(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        self.assertIn("full_claim_retry_ids", workflow)
        self.assertIn("is_recent_full_claim_recovery", workflow)
        self.assertEqual(workflow.count("is_recent_full_claim_recovery("), 2)
        self.assertNotIn("failed_player_ids", workflow)

    def test_python_recovery_receiver_uses_shared_candidate_validator(self):
        source = (ROOT / "master_claimer.py").read_text(encoding="utf-8")
        self.assertIn("from recovery_policy import (", source)
        self.assertIn("full_claim_retry_ids,", source)
        self.assertIn("daily_only_pending_targets,", source)
        self.assertIn("retry_ids = full_claim_retry_ids(prior)", source)


    def test_daily_only_backup_recovery_preserves_roster_streak_and_coverage(self):
        source = (ROOT / "master_claimer.py").read_text(encoding="utf-8")
        self.assertIn("not RECOVERY_ONLY_FAILED and not DAILY_ONLY_RECOVERY", source)
        self.assertIn("_mark_daily_recovery_slot_attempt(meta, pid, run_label)", source)
        policy = (ROOT / "recovery_policy.py").read_text(encoding="utf-8")
        self.assertIn("daily_only_pending_targets", policy)
        self.assertIn("eligible_daily_recovery_ids", policy)


    def test_external_primary_dispatch_starts_after_0535_without_randomized_wait(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        self.assertIn("external_primary:", workflow)
        self.assertIn("context.payload.inputs?.external_primary", workflow)
        self.assertIn("isExternalPrimary", workflow)
        self.assertIn("isBlockingClaimJob", workflow)
        self.assertIn("primaryDelayStatus", workflow)
        self.assertIn("has_complete_full_roster_coverage", workflow)
        self.assertIn("EXTERNAL_PRIMARY:", workflow)

        primary_delay = workflow.split("  primary_delay:", 1)[1].split("  run-claimer:", 1)[0]
        self.assertIn("github.event_name == 'schedule'", primary_delay)
        self.assertNotIn("inputs.external_primary == true", primary_delay)

        run_claimer = workflow.split("  run-claimer:", 1)[1]
        self.assertIn("needs.primary_delay.result == 'skipped'", run_claimer)

    def test_external_primary_is_labeled_as_the_normal_primary_run(self):
        source = (ROOT / "master_claimer.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("EXTERNAL_PRIMARY", "").strip().lower() == "true"', source)
        self.assertIn('return "Primary Run", 0', source)

    def test_schedule_persists_separate_grenade_balance_baselines(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        self.assertIn(
            "git add -- claim_history.json bot_meta.json grenade_balance_baselines.json",
            workflow,
        )
        source = (ROOT / "master_claimer.py").read_text(encoding="utf-8")
        self.assertIn('GRENADE_BALANCE_FILE = "grenade_balance_baselines.json"', source)
        self.assertIn("save_grenade_balance_state", source)


    def test_backup_schedule_rejects_stale_event_before_browser_execution(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        self.assertIn("getWorkflowRun", workflow)
        self.assertIn("evaluateScheduledBackupFreshness", workflow)
        self.assertIn("SCHEDULE_RUN_CREATED_AT", workflow)
        self.assertIn("max_delay = 45 * 60", workflow)
        self.assertIn("no browser session or reward click will start", workflow)

    def test_schedule_keeps_the_full_roster_fallback(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        self.assertIn("fallback primary: no complete full-roster cycle has started after 05:30 IST", workflow)
        self.assertIn("elif not covered_since_reset:", workflow)


if __name__ == "__main__":
    unittest.main()
