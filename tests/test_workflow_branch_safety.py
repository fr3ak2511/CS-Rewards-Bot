import ast
import subprocess
import textwrap
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
        # Pull-request events run unconditionally; path filters remain push-only.
        self.assertEqual(workflow.count("'promotion_followups.py'"), 1)
        self.assertEqual(workflow.count("'.github/workflows/recovery.yml'"), 1)
        self.assertEqual(workflow.count("'recovery_policy.py'"), 1)
        self.assertEqual(workflow.count("'.github/scripts/recovery_policy.js'"), 1)

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


    def test_non_promotional_full_runs_route_existing_queue_through_slot_gate(self):
        source = (ROOT / "master_claimer.py").read_text(encoding="utf-8")
        queue = (ROOT / "progression_inspection_queue.py").read_text(encoding="utf-8")
        self.assertIn('slot_label=run_label', source)
        self.assertIn('progression_reinspection=progression_attempt', source)
        self.assertIn('progression_inspection_blocked=progression_slot_blocked', source)
        self.assertIn('"Primary Run", "Manual Run"', queue)
        self.assertIn("_preserve_queued_progression_state(", source)
        self.assertIn("pending_progression = get_pending_progression_inspection(meta, pid)", source)
        self.assertIn('and not r.get("progression_reinspection_attempted")', source)

    def test_deferred_progression_queue_is_serviced_without_widening_retry_scope(self):
        source = (ROOT / "master_claimer.py").read_text(encoding="utf-8")
        queue = (ROOT / "progression_inspection_queue.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "event-tests.yml").read_text(encoding="utf-8")
        self.assertIn("'progression_inspection_queue.py'", workflow)
        self.assertIn("record_progression_inspection_result", source)
        self.assertIn("_record_queued_progression_inspection_result(meta, pid, r, run_label)", source)
        self.assertIn("progression_reinspection=progression_attempt", source)
        self.assertIn("progression_inspection_blocked=progression_slot_blocked", source)
        self.assertIn("elif recovered_store_indices and not progression_inspection_blocked:", source)
        self.assertIn("_run_queued_progression_inspection(driver, pid, stats, progression_reinspection)", source)
        self.assertIn("if requires_exact and not target_signature:", source)
        self.assertIn("target_card_signature=target_signature", source)
        queued_handler = source.split("def _run_queued_progression_inspection", 1)[1].split(
            "def _record_queued_progression_inspection_result", 1
        )[0]
        self.assertIn("max_claims_per_visit=1", queued_handler)
        self.assertIn("target_card_text_fragments=target_fragments", queued_handler)
        self.assertIn("require_unique_claimable_card=True", queued_handler)
        self.assertIn("defer_if_more_claimable=True", queued_handler)
        self.assertIn("_non_progression_recovery_candidate", source)
        self.assertIn("progression_inspection_blocked=bool(queued_progression)", source)
        self.assertIn("elif is_promotional_backup:", source)
        self.assertIn("progression_reinspection=progression_attempt", source)
        self.assertIn("not result.get(\"progression_deferred\")", source.split("def _full_claim_recovery_candidate", 1)[1].split("def _record_full_claim_coverage", 1)[0])
        self.assertIn("requires_exact_target", queue)
        self.assertIn("attempted_slots", queue)
        recovery_predicate = source.split("def _full_claim_recovery_candidate", 1)[1].split("def _record_full_claim_coverage", 1)[0]
        self.assertIn("progression_unconfirmed", recovery_predicate)
        self.assertIn("and not result.get(\"progression_deferred\")", recovery_predicate)
        self.assertIn("and not result.get(\"progression_reinspection_attempted\")", recovery_predicate)
        self.assertIn("progression_reinspection=progression_attempt", source)
        self.assertIn("progression_inspection_blocked=progression_slot_blocked", source)
        self.assertIn("progression_inspection_blocked=bool(queued_progression)", source)

    def test_daily_only_backup_never_services_progression_queue_for_non_target_accounts(self):
        source = (ROOT / "master_claimer.py").read_text(encoding="utf-8")
        self.assertIn(
            "if DAILY_ONLY_RECOVERY and not is_promotional_backup and not MANUAL_DAILY_RECOVERY:",
            source,
        )
        marker = source.index("# Other roster members in a Daily-only promotional backup remain")
        start = source.rfind("elif ", 0, marker)
        end = source.index("elif is_promotional_backup:", marker)
        non_target_branch = source[start:end]
        self.assertIn("process_player_promotions_only(pid, new_id, run_label)", non_target_branch)
        self.assertNotIn("get_pending_progression_inspection", non_target_branch)
        self.assertNotIn("progression_reinspection", non_target_branch)

    def test_slot_block_preserves_exact_unconfirmed_progression_identity(self):
        source = (ROOT / "master_claimer.py").read_text(encoding="utf-8")
        self.assertIn("if progression_slot_blocked and queued_progression:", source)
        self.assertIn(
            'r["progression_unconfirmed"] = bool(queued_progression.get("requires_exact_target"))',
            source,
        )
        self.assertIn(
            'r["progression_unconfirmed_card_signature"] = (',
            source,
        )

    def test_daily_only_backup_recovery_preserves_roster_streak_and_coverage(self):
        source = (ROOT / "master_claimer.py").read_text(encoding="utf-8")
        self.assertIn("not RECOVERY_ONLY_FAILED and not DAILY_ONLY_RECOVERY", source)
        self.assertIn("_mark_daily_recovery_slot_attempt(meta, pid, run_label)", source)
        policy = (ROOT / "recovery_policy.py").read_text(encoding="utf-8")
        self.assertIn("daily_only_pending_targets", policy)
        self.assertIn("eligible_daily_recovery_ids", policy)


    def test_manual_daily_recovery_is_exact_one_account_and_uses_shared_claim_owner(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        self.assertIn("recover_pending_daily:", workflow)
        self.assertIn("daily_recovery_player_id:", workflow)
        self.assertIn("manual_daily_recovery_targets(", workflow)
        self.assertIn("one account, Daily reward only", workflow)
        self.assertIn("MANUAL_DAILY_RECOVERY_PLAYER_ID:", workflow)
        self.assertIn("scheduled backup slot consumed", workflow)
        run_claimer = workflow.split("  run-claimer:", 1)[1]
        self.assertIn("group: rewards-claimer", run_claimer)
        self.assertIn('MANUAL_DAILY_RECOVERY" == "true"', run_claimer)
        self.assertIn('elif [[ -z "$MANUAL_DAILY_RECOVERY_PLAYER_ID" ]]; then', run_claimer)

        source = (ROOT / "master_claimer.py").read_text(encoding="utf-8")
        self.assertIn("MANUAL_DAILY_RECOVERY = os.getenv", source)
        self.assertIn("manual_daily_recovery_targets(", source)
        self.assertIn("players = [(pid, loyalty) for pid, loyalty in players if pid == selected_pid]", source)
        self.assertIn("MANUAL_DAILY_RECOVERY\n                or (", source)
        self.assertIn('r["standard_rewards_checked"] = False', source)
        self.assertIn('"daily_recovery_mode":', source)
        self.assertIn('"manual_daily_recovery_player_id":', source)

    def test_manual_daily_recovery_is_not_dispatched_into_general_post_run_recovery(self):
        policy = (ROOT / ".github" / "scripts" / "recovery_policy.js").read_text(encoding="utf-8")
        self.assertIn("lastRun.daily_only_recovery === true", policy)
        self.assertIn("scheduled Daily-only recovery already ran", policy)

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

    def test_schedule_persists_state_on_dedicated_runtime_branch(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        self.assertGreaterEqual(workflow.count("bash .github/scripts/runtime_state_branch.sh load"), 2)
        self.assertIn("bash .github/scripts/runtime_state_branch.sh save", workflow)
        self.assertIn("path: 'bot_meta.json', ref: 'runtime-state'", workflow)
        self.assertNotIn('TARGET_BRANCH: ${{ github.ref_name }}', workflow)
        self.assertNotIn('git push origin "HEAD:${TARGET_BRANCH}"', workflow)

        source = (ROOT / "master_claimer.py").read_text(encoding="utf-8")
        self.assertIn('GRENADE_BALANCE_FILE = "grenade_balance_baselines.json"', source)
        self.assertIn("save_grenade_balance_state", source)

    def test_immediate_recovery_reads_metadata_from_runtime_state_branch(self):
        workflow = (ROOT / ".github" / "workflows" / "recovery.yml").read_text(encoding="utf-8")
        self.assertIn("path: 'bot_meta.json', ref: 'runtime-state'", workflow)
        self.assertNotIn("path: 'bot_meta.json', ref: source.head_branch", workflow)

    def test_runtime_state_helper_has_valid_shell_syntax_and_explicit_file_allowlist(self):
        helper = ROOT / ".github" / "scripts" / "runtime_state_branch.sh"
        result = subprocess.run(["bash", "-n", str(helper)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        source = helper.read_text(encoding="utf-8")
        for filename in (
            "claim_history.json",
            "bot_meta.json",
            "grenade_balance_baselines.json",
            "portal_claim_audit.json",
        ):
            self.assertIn(filename, source)
        self.assertIn("refs/heads/${STATE_BRANCH}", source)
        self.assertIn("HEAD:refs/heads/${STATE_BRANCH}", source)
        self.assertIn("EXPECTED_RUNTIME_STATE_SHA", source)
        self.assertIn("refusing a stale overwrite", source)
        self.assertIn("advanced while the claimer was running", source)
        self.assertIn("not valid JSON", source)
        self.assertNotIn("git push --force", source)
        self.assertNotIn('git -C "${worktree}" rebase', source)
        self.assertIn('git worktree add -b "${local_branch}"', source)
        self.assertNotIn("git worktree add --track", source)

    def test_primary_wait_refreshes_coverage_from_runtime_state_branch(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        primary_delay = workflow.split("  primary_delay:", 1)[1].split("  run-claimer:", 1)[0]
        self.assertIn("path: 'bot_meta.json', ref: 'runtime-state'", primary_delay)
        self.assertIn("path: 'players.csv', ref: 'prod'", primary_delay)

    def test_runtime_state_save_uses_the_snapshot_loaded_by_the_claimer_job(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        run_claimer = workflow.split("  run-claimer:", 1)[1]
        self.assertIn("id: runtime_state", run_claimer)
        self.assertIn("EXPECTED_RUNTIME_STATE_SHA: ${{ steps.runtime_state.outputs.base_sha }}", run_claimer)


    def test_legacy_github_promotional_crons_are_removed_and_external_slots_remain_guarded(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        for cron in ("5 4 * * *", "5 8 * * *", "5 12 * * *", "5 16 * * *"):
            self.assertNotIn(f"    - cron: '{cron}'", workflow)

        self.assertEqual(workflow.count("    - cron:"), 2)
        self.assertIn("    - cron: '30 21 * * *'", workflow)
        self.assertIn("    - cron: '0-30/5 0 * * *'", workflow)
        self.assertNotIn("backup_crons", workflow)
        self.assertNotIn("evaluateScheduledBackupFreshness", workflow)
        self.assertNotIn("SCHEDULE_RUN_CREATED_AT:", workflow)

        self.assertIn("evaluateExternalPromotionalBackupFreshness", workflow)
        self.assertIn("EXTERNAL_PROMOTIONAL_RUN_CREATED_AT:", workflow)
        self.assertIn("max_delay = 45 * 60", workflow)
        self.assertIn("no browser session or reward click will start", workflow)

    def test_schedule_keeps_the_full_roster_fallback(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        self.assertIn("fallback primary: no complete full-roster cycle has started after 05:30 IST", workflow)
        self.assertIn("elif not covered_since_reset:", workflow)


    def test_embedded_run_mode_gate_is_valid_python(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        run_mode = workflow.split("id: run_mode", 1)[1].split("      - name: Set up Python", 1)[0]
        script = run_mode.split("python3 - <<'PY'", 1)[1].split("          PY", 1)[0]
        ast.parse(textwrap.dedent(script.lstrip("\n")))

    def test_external_promotional_backup_is_slot_scoped_freshness_gated_and_uses_shared_claimer(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        self.assertIn("external_promotional_backup:", workflow)
        self.assertIn("external_promotional_slot:", workflow)
        self.assertIn("evaluateExternalPromotionalBackupFreshness", workflow)
        self.assertIn("EXTERNAL_PROMOTIONAL_RUN_CREATED_AT:", workflow)
        self.assertIn("external promotional backup requires exactly slot 1 or 2", workflow)
        self.assertIn("elif external_promotional_backup:", workflow)
        self.assertNotIn("backup_crons", workflow)
        self.assertIn("EXTERNAL_PROMOTIONAL_BACKUP:", workflow)
        self.assertIn("EXTERNAL_PROMOTIONAL_SLOT:", workflow)
        self.assertIn("max_delay = 45 * 60", workflow)

        run_claimer = workflow.split("  run-claimer:", 1)[1]
        self.assertIn("group: rewards-claimer", run_claimer)
        self.assertIn("EXTERNAL_PROMOTIONAL_BACKUP:", run_claimer)

        source = (ROOT / "master_claimer.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("EXTERNAL_PROMOTIONAL_BACKUP", "").strip().lower() == "true"', source)
        self.assertIn('return f"Promotional Check #{slot}", int(slot)', source)
        self.assertIn("Unknown External Promotional Backup", source)

    def test_external_promotional_backup_replaces_legacy_crons_and_keeps_native_fallbacks(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("    - cron:"), 2)
        self.assertIn("'30 21 * * *'", workflow)
        self.assertIn("'0-30/5 0 * * *'", workflow)
        for cron in ("5 4 * * *", "5 8 * * *", "5 12 * * *", "5 16 * * *"):
            self.assertNotIn(f"    - cron: '{cron}'", workflow)
        self.assertIn("external_promotional_backup:", workflow)
        self.assertIn("external_promotional_slot:", workflow)
        self.assertIn("evaluateExternalPromotionalBackupFreshness", workflow)
        self.assertIn("group: rewards-claimer", workflow)
        self.assertIn("fallback primary: no complete full-roster cycle has started after 05:30 IST", workflow)



    def test_active_workflows_use_node24_compatible_action_majors(self):
        expected = {
            ".github/workflows/schedule.yml": (
                "actions/checkout@v7",
                "actions/github-script@v9",
                "actions/setup-python@v7",
                "actions/upload-artifact@v6",
            ),
            ".github/workflows/recovery.yml": (
                "actions/checkout@v7",
                "actions/github-script@v9",
            ),
            ".github/workflows/event-tests.yml": (
                "actions/checkout@v7",
                "actions/setup-python@v7",
                "actions/setup-node@v7",
            ),
        }
        for relative_path, required_pins in expected.items():
            workflow = (ROOT / relative_path).read_text(encoding="utf-8")
            for pin in required_pins:
                with self.subTest(path=relative_path, pin=pin):
                    self.assertIn(pin, workflow)

        # Keep old action-runtime majors out of active production workflows.
        for relative_path in expected:
            workflow = (ROOT / relative_path).read_text(encoding="utf-8")
            for retired_pin in (
                "actions/checkout@v4",
                "actions/github-script@v7",
                "actions/setup-python@v5",
                "actions/setup-node@v4",
                "actions/upload-artifact@v4",
            ):
                with self.subTest(path=relative_path, retired_pin=retired_pin):
                    self.assertNotIn(retired_pin, workflow)

    def test_github_script_v9_inline_scripts_avoid_esm_breaking_patterns(self):
        for relative_path in (
            ".github/workflows/schedule.yml",
            ".github/workflows/recovery.yml",
        ):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("require('@actions/github')", source)
            self.assertNotIn('require("@actions/github")', source)
            self.assertNotRegex(
                source,
                r"\b(?:const|let)\s+getOctokit\s*=",
            )
            self.assertNotRegex(
                source,
                r"\b(?:const|let)\s+\{\s*getOctokit\b",
            )

    def test_full_regression_suite_runs_for_every_pull_request_to_prod_and_dev(self):
        workflow = (ROOT / ".github" / "workflows" / "event-tests.yml").read_text(encoding="utf-8")
        pull_request = workflow.split("\n  pull_request:\n", 1)[1].split(
            "\n  workflow_dispatch:", 1
        )[0]
        self.assertIn("      - prod", pull_request)
        self.assertIn("      - dev", pull_request)
        self.assertNotIn("    paths:", pull_request)


    def test_immediate_progression_recovery_is_source_bound_serialized_and_category_isolated(self):
        schedule = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        recovery = (ROOT / ".github" / "workflows" / "recovery.yml").read_text(encoding="utf-8")
        source = (ROOT / "master_claimer.py").read_text(encoding="utf-8")
        queue = (ROOT / "progression_inspection_queue.py").read_text(encoding="utf-8")
        policy = (ROOT / ".github" / "scripts" / "recovery_policy.js").read_text(encoding="utf-8")

        self.assertIn("PROGRESSION_QUEUE_ONLY:", schedule)
        self.assertIn("PROGRESSION_SOURCE_RUN_ID:", schedule)
        self.assertIn("source_run_id=progression_source_run_id", schedule)
        self.assertIn("group: rewards-claimer", schedule)
        self.assertIn("progression_only={'true' if progression_only else 'false'}", schedule)

        self.assertIn("evaluateImmediateProgressionQueueRecovery", recovery)
        self.assertIn("progression_queue_only:", recovery)
        self.assertEqual(recovery.count("createDispatchEvent({"), 1)
        self.assertIn("sourceRun.id", policy)
        self.assertIn("Immediate Progression Recovery", queue)
        self.assertIn("source_run_id: str | None = None", queue)

        self.assertIn("def process_player_progression_only(", source)
        self.assertIn("source_run_id=PROGRESSION_SOURCE_RUN_ID", source)
        self.assertIn('meta["last_progression_recovery"] = run_record', source)
        processor = source.split("def process_player_progression_only(", 1)[1].split(
            "def _mark_queued_progression_inspection_attempt(", 1
        )[0]
        self.assertIn("_run_queued_progression_inspection(", processor)
        self.assertNotIn("claim_daily_rewards(", processor)
        self.assertNotIn("claim_store_rewards(", processor)
        self.assertNotIn("run_event_reward_checks(", processor)
        self.assertNotIn("_record_grenade_balance_for_player(", processor)



    def test_recovery_dispatch_supports_manual_source_run_bound_catchup(self):
        workflow = (ROOT / ".github" / "workflows" / "recovery.yml").read_text(encoding="utf-8")
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertIn("source_run_id:", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch' && github.ref_name == 'prod'", workflow)
        self.assertIn("context.eventName === 'workflow_dispatch'", workflow)
        self.assertIn("github.rest.actions.getWorkflowRun", workflow)
        self.assertIn("source.name !== 'CS Hub Rewards Claimer'", workflow)
        self.assertIn("source.status !== 'completed'", workflow)
        self.assertIn("source_run_id: source.id", workflow)
        self.assertIn(r"if (!/^\d+$/.test(sourceRunId))", workflow)
        self.assertIn("evaluateImmediateProgressionQueueRecovery", workflow)
        self.assertEqual(workflow.count("createDispatchEvent({"), 1)


if __name__ == "__main__":
    unittest.main()
